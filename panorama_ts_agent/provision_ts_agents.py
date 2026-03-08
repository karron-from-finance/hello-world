#!/usr/bin/env python3
"""
Panorama TS-Agent Provisioning Script
=======================================
Reads a Nutanix new-VM CSV report and provisions each VM as a TS-Agent entry
in Palo Alto Panorama under the specified template and user-ID group mapping
profile. After provisioning, the script validates, commits, and pushes the
configuration, then verifies TS-Agent connectivity on a sample firewall.

Usage:
    python provision_ts_agents.py --csv path/to/new_vms.csv [--config path/to/settings.ini]
"""

import argparse
import configparser
import csv
import logging
import os
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
import urllib3

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("panorama_ts_agent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> configparser.ConfigParser:
    """Load and validate the INI configuration file."""
    if not os.path.isfile(config_path):
        logger.error("Configuration file not found: %s", config_path)
        sys.exit(1)
    config = configparser.ConfigParser()
    config.read(config_path)
    for section in ["panorama", "firewall", "paths"]:
        if section not in config:
            logger.error("Missing required config section: [%s]", section)
            sys.exit(1)
    return config


def read_new_vms_csv(csv_path: str) -> list:
    """
    Read the new VMs CSV produced by the Nutanix discovery script.
    Returns a list of dicts with keys: hostname, ip_address, fqdn, creation_date.
    """
    if not os.path.isfile(csv_path):
        logger.error("CSV file not found: %s", csv_path)
        sys.exit(1)

    vms = []
    try:
        with open(csv_path, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                vms.append({
                    "hostname": row.get("Hostname", "").strip(),
                    "ip_address": row.get("IP Address", "").strip(),
                    "fqdn": row.get("FQDN", "").strip(),
                    "creation_date": row.get("VM Creation Date", "").strip(),
                })
        logger.info("Read %d VMs from CSV: %s", len(vms), csv_path)
    except Exception as exc:
        logger.error("Failed to read CSV %s: %s\n%s", csv_path, exc, traceback.format_exc())
        sys.exit(1)

    return vms


# ---------------------------------------------------------------------------
# Panorama XML API Client
# ---------------------------------------------------------------------------

class PanoramaClient:
    """Client for Palo Alto Panorama XML API."""

    def __init__(self, host: str, api_key: str, verify_ssl: bool = False):
        self.base_url = f"https://{host}/api"
        self.api_key = api_key
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _request(self, params: dict) -> ET.Element:
        """
        Execute a Panorama API request and return the parsed XML response root.
        Raises on HTTP or API-level errors.
        """
        params["key"] = self.api_key
        url = self.base_url
        logger.info("Panorama API request: type=%s, action=%s",
                     params.get("type", "N/A"), params.get("action", "N/A"))
        try:
            resp = self.session.get(url, params=params, verify=self.verify_ssl,
                                    timeout=120)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            logger.error("Panorama HTTP error: %s – Body: %s",
                          exc, exc.response.text if exc.response else "N/A")
            raise
        except requests.exceptions.ConnectionError as exc:
            logger.error("Connection error to Panorama: %s\n%s",
                          exc, traceback.format_exc())
            raise
        except requests.exceptions.Timeout as exc:
            logger.error("Panorama request timed out: %s", exc)
            raise

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.error("Failed to parse Panorama XML response: %s\nRaw: %s",
                          exc, resp.text[:2000])
            raise

        status = root.attrib.get("status", "")
        if status != "success":
            msg_elem = root.find(".//msg/line")
            msg_text = msg_elem.text if msg_elem is not None else resp.text[:500]
            logger.error("Panorama API returned status='%s': %s", status, msg_text)
            raise RuntimeError(f"Panorama API error: {msg_text}")

        return root

    # ------------------------------------------------------------------
    # TS-Agent operations
    # ------------------------------------------------------------------

    def get_existing_ts_agents(self, template_name: str,
                                group_mapping_profile: str) -> set:
        """
        Retrieve the set of existing TS-Agent entry names in the specified
        template and group mapping profile.
        Returns a set of agent names (uppercase).
        """
        xpath = (
            f"/config/devices/entry[@name='localhost.localdomain']"
            f"/template/entry[@name='{template_name}']"
            f"/config/devices/entry[@name='localhost.localdomain']"
            f"/vsys/entry[@name='vsys1']"
            f"/user-id-agent/entry[@name='{group_mapping_profile}']"
            f"/agent-list"
        )
        params = {
            "type": "config",
            "action": "get",
            "xpath": xpath,
        }

        existing_names = set()
        try:
            root = self._request(params)
            entries = root.findall(".//agent-list/entry")
            for entry in entries:
                name = entry.attrib.get("name", "")
                if name:
                    existing_names.add(name.upper())
            logger.info("Found %d existing TS-Agent entries in %s / %s.",
                         len(existing_names), template_name, group_mapping_profile)
        except RuntimeError:
            # If the profile path doesn't exist yet, treat as empty
            logger.warning("Could not retrieve existing TS-Agents; "
                           "assuming none exist yet.")
        except Exception as exc:
            logger.error("Error fetching existing TS-Agents: %s\n%s",
                          exc, traceback.format_exc())

        return existing_names

    def add_ts_agent(self, template_name: str, group_mapping_profile: str,
                     agent_name: str, host_address: str) -> bool:
        """
        Add a single TS-Agent entry to the Panorama candidate configuration.
        Returns True on success, False on failure.
        """
        xpath = (
            f"/config/devices/entry[@name='localhost.localdomain']"
            f"/template/entry[@name='{template_name}']"
            f"/config/devices/entry[@name='localhost.localdomain']"
            f"/vsys/entry[@name='vsys1']"
            f"/user-id-agent/entry[@name='{group_mapping_profile}']"
            f"/agent-list"
        )
        element = (
            f"<entry name=\"{agent_name}\">"
            f"<host>{host_address}</host>"
            f"<port>5007</port>"
            f"<enabled>yes</enabled>"
            f"</entry>"
        )
        params = {
            "type": "config",
            "action": "set",
            "xpath": xpath,
            "element": element,
        }

        try:
            self._request(params)
            logger.info("Successfully added TS-Agent entry: %s -> %s",
                         agent_name, host_address)
            return True
        except Exception as exc:
            logger.error("Failed to add TS-Agent '%s': %s\n%s",
                          agent_name, exc, traceback.format_exc())
            return False

    # ------------------------------------------------------------------
    # Validate, commit, and push
    # ------------------------------------------------------------------

    def validate_config(self) -> bool:
        """Validate the Panorama candidate configuration."""
        # Panorama validate uses op command
        cmd = "<validate><full></full></validate>"
        params = {
            "type": "op",
            "cmd": cmd,
        }
        try:
            root = self._request(params)
            result_elem = root.find(".//result/msg")
            result_text = result_elem.text if result_elem is not None else ""
            logger.info("Panorama validation result: %s", result_text or "success")
            return True
        except Exception as exc:
            logger.error("Panorama validation failed: %s\n%s",
                          exc, traceback.format_exc())
            return False

    def commit(self) -> str | None:
        """
        Commit the Panorama candidate configuration.
        Returns the job ID on success, None on failure.
        """
        params = {
            "type": "commit",
            "cmd": "<commit></commit>",
        }
        try:
            root = self._request(params)
            job_elem = root.find(".//result/job")
            job_id = job_elem.text if job_elem is not None else None
            logger.info("Panorama commit initiated. Job ID: %s", job_id)
            return job_id
        except Exception as exc:
            logger.error("Panorama commit failed: %s\n%s",
                          exc, traceback.format_exc())
            return None

    def push_to_devices(self) -> str | None:
        """
        Push the committed configuration from Panorama to managed firewalls.
        Returns the job ID on success, None on failure.
        """
        cmd = (
            "<commit-all>"
            "<shared-policy>"
            "<include-template>yes</include-template>"
            "</shared-policy>"
            "</commit-all>"
        )
        params = {
            "type": "commit",
            "action": "all",
            "cmd": cmd,
        }
        try:
            root = self._request(params)
            job_elem = root.find(".//result/job")
            job_id = job_elem.text if job_elem is not None else None
            logger.info("Panorama push to devices initiated. Job ID: %s", job_id)
            return job_id
        except Exception as exc:
            logger.error("Panorama push to devices failed: %s\n%s",
                          exc, traceback.format_exc())
            return None

    def check_job_status(self, job_id: str) -> str:
        """
        Check the status of a Panorama job.
        Returns 'FIN' when complete, 'PEND' when still running, or 'ERR' on error.
        """
        cmd = f"<show><jobs><id>{job_id}</id></jobs></show>"
        params = {
            "type": "op",
            "cmd": cmd,
        }
        try:
            root = self._request(params)
            status_elem = root.find(".//result/job/status")
            result_elem = root.find(".//result/job/result")
            status = status_elem.text if status_elem is not None else "UNKNOWN"
            result = result_elem.text if result_elem is not None else "UNKNOWN"
            logger.info("Job %s status: %s, result: %s", job_id, status, result)
            if status == "FIN":
                if result == "OK":
                    return "FIN"
                else:
                    return "ERR"
            return "PEND"
        except Exception as exc:
            logger.error("Error checking job %s status: %s\n%s",
                          job_id, exc, traceback.format_exc())
            return "ERR"


# ---------------------------------------------------------------------------
# Firewall TS-Agent Status Client
# ---------------------------------------------------------------------------

class FirewallClient:
    """Client for querying a PAN-OS firewall for TS-Agent status."""

    def __init__(self, host: str, api_key: str, verify_ssl: bool = False):
        self.base_url = f"https://{host}/api"
        self.api_key = api_key
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _request(self, params: dict) -> ET.Element:
        """Execute a firewall API request and return parsed XML root."""
        params["key"] = self.api_key
        try:
            resp = self.session.get(self.base_url, params=params,
                                    verify=self.verify_ssl, timeout=60)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            return root
        except Exception as exc:
            logger.error("Firewall API error: %s\n%s", exc, traceback.format_exc())
            raise

    def get_ts_agent_status(self) -> dict:
        """
        Retrieve TS-Agent connection status from the firewall.
        Returns a dict of agent_name -> status (e.g., 'Connected', 'Not Connected').
        """
        cmd = "<show><user><ts-agent><state>all</state></ts-agent></user></show>"
        params = {
            "type": "op",
            "cmd": cmd,
        }
        agent_status = {}
        try:
            root = self._request(params)
            entries = root.findall(".//entry")
            for entry in entries:
                name_elem = entry.find("name")
                status_elem = entry.find("status")
                host_elem = entry.find("host")
                if name_elem is not None and name_elem.text:
                    agent_name = name_elem.text.strip().upper()
                    status_text = (status_elem.text.strip()
                                   if status_elem is not None and status_elem.text
                                   else "Unknown")
                    agent_status[agent_name] = status_text
            logger.info("Retrieved status for %d TS-Agents from firewall.",
                         len(agent_status))
        except Exception as exc:
            logger.error("Failed to retrieve TS-Agent status: %s\n%s",
                          exc, traceback.format_exc())
        return agent_status


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def wait_for_job(client: PanoramaClient, job_id: str,
                 poll_interval: int = 30, max_wait: int = 600) -> bool:
    """
    Poll a Panorama job until it finishes or times out.
    Returns True if the job completed successfully.
    """
    elapsed = 0
    while elapsed < max_wait:
        status = client.check_job_status(job_id)
        if status == "FIN":
            logger.info("Job %s completed successfully.", job_id)
            return True
        if status == "ERR":
            logger.error("Job %s completed with errors.", job_id)
            return False
        logger.info("Job %s still pending. Waiting %ds...", job_id, poll_interval)
        time.sleep(poll_interval)
        elapsed += poll_interval

    logger.error("Job %s timed out after %d seconds.", job_id, max_wait)
    return False


def write_status_report(vm_agents: list, agent_statuses: dict,
                        report_dir: str) -> str:
    """
    Write the TS-Agent status report CSV.
    Columns: VM Hostname, Agent Created Successfully, Connection Status
    Returns the path to the written CSV file.
    """
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(report_dir, f"ts_agent_status_{timestamp}.csv")

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "VM Hostname",
                "Agent Created Successfully",
                "Connection Status",
            ])
            for agent in vm_agents:
                hostname_upper = agent["hostname"].upper()
                created = agent.get("created", False)
                created_str = "Yes" if created else "No"

                conn_status = agent_statuses.get(hostname_upper, "Not Found")
                writer.writerow([
                    hostname_upper,
                    created_str,
                    conn_status,
                ])
        logger.info("Status report written to %s", csv_path)
    except Exception as exc:
        logger.error("Failed to write status report: %s\n%s",
                      exc, traceback.format_exc())
        csv_path = ""

    return csv_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Panorama TS-Agent Provisioning Script")
    parser.add_argument("--csv", required=True,
                        help="Path to the new VMs CSV from the Nutanix discovery script")
    parser.add_argument("--config", default="config/settings.ini",
                        help="Path to settings.ini configuration file")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Panorama TS-Agent Provisioning – starting run")
    logger.info("=" * 60)

    config = load_config(args.config)

    # Panorama settings
    pan_host = config.get("panorama", "host")
    pan_api_key = config.get("panorama", "api_key")
    pan_verify = config.getboolean("panorama", "verify_ssl", fallback=False)
    template_name = config.get("panorama", "template_name")
    gmp_name = config.get("panorama", "group_mapping_profile")
    domain_suffix = config.get("panorama", "domain_suffix")

    # Firewall settings
    fw_host = config.get("firewall", "sample_firewall_host")
    fw_api_key = config.get("firewall", "sample_firewall_api_key")
    fw_verify = config.getboolean("firewall", "verify_ssl", fallback=False)

    # Paths
    report_dir = config.get("paths", "panorama_report_dir")

    # Read input CSV
    vms = read_new_vms_csv(args.csv)
    if not vms:
        logger.info("No VMs to provision. Exiting.")
        return

    # Initialize Panorama client
    pan_client = PanoramaClient(pan_host, pan_api_key, pan_verify)

    # Get existing TS-Agent entries to avoid duplicates
    existing_agents = pan_client.get_existing_ts_agents(template_name, gmp_name)
    logger.info("Existing TS-Agent entries: %s", existing_agents)

    # Track provisioning results
    vm_agents = []

    for vm in vms:
        hostname = vm["hostname"]
        agent_name = hostname.upper()
        host_address = f"{hostname.lower()}.{domain_suffix}"

        if agent_name in existing_agents:
            logger.info("TS-Agent '%s' already exists. Skipping creation.", agent_name)
            vm_agents.append({"hostname": hostname, "created": True})
            continue

        logger.info("Adding TS-Agent: name=%s, host=%s", agent_name, host_address)
        success = pan_client.add_ts_agent(
            template_name, gmp_name, agent_name, host_address)
        vm_agents.append({"hostname": hostname, "created": success})

        if not success:
            logger.error("Failed to create TS-Agent for %s. Continuing with next VM.",
                          hostname)

    # Check if any agents were actually created
    agents_created = [a for a in vm_agents if a["created"]]
    if not agents_created:
        logger.error("No TS-Agent entries were successfully created. "
                      "Skipping commit and push.")
        write_status_report(vm_agents, {}, report_dir)
        return

    # Validate configuration
    logger.info("Validating Panorama configuration...")
    if not pan_client.validate_config():
        logger.error("Panorama validation failed. Aborting commit.")
        write_status_report(vm_agents, {}, report_dir)
        return

    logger.info("Panorama validation passed.")

    # Commit
    logger.info("Committing Panorama configuration...")
    commit_job_id = pan_client.commit()
    if not commit_job_id:
        logger.error("Panorama commit did not return a job ID. Aborting.")
        write_status_report(vm_agents, {}, report_dir)
        return

    if not wait_for_job(pan_client, commit_job_id, poll_interval=30, max_wait=600):
        logger.error("Panorama commit job failed or timed out.")
        write_status_report(vm_agents, {}, report_dir)
        return

    logger.info("Panorama commit completed successfully.")

    # Push to devices
    logger.info("Pushing configuration to managed firewalls...")
    push_job_id = pan_client.push_to_devices()
    if not push_job_id:
        logger.error("Panorama push did not return a job ID.")
        write_status_report(vm_agents, {}, report_dir)
        return

    if not wait_for_job(pan_client, push_job_id, poll_interval=30, max_wait=600):
        logger.error("Panorama push job failed or timed out.")
        write_status_report(vm_agents, {}, report_dir)
        return

    logger.info("Panorama push to devices completed successfully.")

    # Wait 5 minutes for TS-Agents to connect
    wait_minutes = 5
    logger.info("Waiting %d minutes for TS-Agents to establish connections...",
                 wait_minutes)
    time.sleep(wait_minutes * 60)

    # Check TS-Agent status on sample firewall
    logger.info("Checking TS-Agent status on sample firewall: %s", fw_host)
    fw_client = FirewallClient(fw_host, fw_api_key, fw_verify)
    agent_statuses = fw_client.get_ts_agent_status()

    # Write final status report
    csv_path = write_status_report(vm_agents, agent_statuses, report_dir)
    if csv_path:
        logger.info("Final status report: %s", csv_path)

    logger.info("=" * 60)
    logger.info("Panorama TS-Agent Provisioning – run complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
