#!/usr/bin/env python3
"""
Nutanix VM Discovery Script
============================
Queries Nutanix Prism Central APIs to discover new VMs on monitored subnets.
Compares current VM inventory against the previous run to identify newly created VMs.
Outputs a CSV report with hostname, IP address, FQDN (reverse DNS), and creation date.

Usage:
    python discover_vms.py [--config path/to/settings.ini]
"""

import argparse
import configparser
import csv
import json
import logging
import os
import socket
import sys
import traceback
from datetime import datetime, timezone

import requests
import urllib3

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("nutanix_vm_discovery")


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
    required_sections = ["nutanix", "paths"]
    for section in required_sections:
        if section not in config:
            logger.error("Missing required config section: [%s]", section)
            sys.exit(1)
    return config


def reverse_dns_lookup(ip_address: str) -> str:
    """
    Perform a reverse DNS lookup on the given IP address.
    Returns the FQDN if found, otherwise returns 'N/A'.
    """
    try:
        fqdn, _, _ = socket.gethostbyaddr(ip_address)
        logger.info("Reverse DNS for %s -> %s", ip_address, fqdn)
        return fqdn
    except socket.herror as exc:
        logger.warning("Reverse DNS lookup failed for %s: %s", ip_address, exc)
        return "N/A"
    except Exception as exc:
        logger.error("Unexpected error during reverse DNS lookup for %s: %s\n%s",
                      ip_address, exc, traceback.format_exc())
        return "N/A"


def load_previous_inventory(path: str) -> dict:
    """
    Load the previous VM inventory from a JSON file.
    Returns a dict keyed by VM UUID -> VM metadata.
    """
    if not os.path.isfile(path):
        logger.info("No previous inventory file found at %s. Treating all VMs as new.", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        logger.info("Loaded previous inventory with %d VMs.", len(data))
        return data
    except json.JSONDecodeError as exc:
        logger.error("Corrupt previous inventory JSON at %s: %s", path, exc)
        return {}
    except Exception as exc:
        logger.error("Failed to load previous inventory from %s: %s\n%s",
                      path, exc, traceback.format_exc())
        return {}


def save_current_inventory(path: str, inventory: dict) -> None:
    """Persist the current VM inventory to JSON for the next run comparison."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(inventory, fh, indent=2)
        logger.info("Saved current inventory (%d VMs) to %s", len(inventory), path)
    except Exception as exc:
        logger.error("Failed to save inventory to %s: %s\n%s",
                      path, exc, traceback.format_exc())


# ---------------------------------------------------------------------------
# Nutanix API interactions
# ---------------------------------------------------------------------------

class NutanixClient:
    """Client for Nutanix Prism Central v3 API."""

    def __init__(self, api_endpoint: str, username: str, password: str,
                 verify_ssl: bool = False):
        self.api_endpoint = api_endpoint.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.verify_ssl = verify_ssl
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _post(self, path: str, payload: dict) -> dict:
        """Execute a POST request against Prism Central and return JSON."""
        url = f"{self.api_endpoint}{path}"
        logger.info("POST %s", url)
        try:
            resp = self.session.post(url, json=payload, verify=self.verify_ssl,
                                     timeout=60)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            logger.error("HTTP error from %s: %s – Response body: %s",
                          url, exc, exc.response.text if exc.response else "N/A")
            raise
        except requests.exceptions.ConnectionError as exc:
            logger.error("Connection error to %s: %s\n%s",
                          url, exc, traceback.format_exc())
            raise
        except requests.exceptions.Timeout as exc:
            logger.error("Request to %s timed out: %s", url, exc)
            raise
        except Exception as exc:
            logger.error("Unexpected error calling %s: %s\n%s",
                          url, exc, traceback.format_exc())
            raise

    def list_vms(self, limit: int = 500) -> list:
        """
        Retrieve all VMs from Prism Central using the v3 vms/list endpoint.
        Handles pagination automatically.
        """
        all_vms = []
        offset = 0
        while True:
            payload = {
                "kind": "vm",
                "length": limit,
                "offset": offset,
            }
            try:
                data = self._post("/api/nutanix/v3/vms/list", payload)
            except Exception:
                logger.error("Aborting VM list retrieval due to API error.")
                break

            entities = data.get("entities", [])
            if not entities:
                logger.info("No more VMs returned at offset %d.", offset)
                break

            all_vms.extend(entities)
            total = data.get("metadata", {}).get("total_matches", 0)
            offset += limit
            logger.info("Fetched %d / %d VMs so far.", len(all_vms), total)

            if offset >= total:
                break

        logger.info("Total VMs retrieved: %d", len(all_vms))
        return all_vms


def extract_vm_info(vm_entity: dict) -> dict | None:
    """
    Extract relevant fields from a Nutanix VM entity.
    Returns None if the VM has no NIC / IP information.
    """
    try:
        metadata = vm_entity.get("metadata", {})
        spec = vm_entity.get("spec", {})
        status = vm_entity.get("status", {})
        resources = status.get("resources", {})

        vm_uuid = metadata.get("uuid", "UNKNOWN")
        vm_name = spec.get("name") or status.get("name", "UNKNOWN")

        # Creation time from metadata
        creation_time_raw = metadata.get("creation_time", "")
        creation_date = ""
        if creation_time_raw:
            try:
                dt = datetime.fromisoformat(creation_time_raw.replace("Z", "+00:00"))
                creation_date = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except ValueError:
                creation_date = creation_time_raw

        # Extract IP addresses and subnet UUIDs from NICs
        nics = resources.get("nic_list", [])
        ip_addresses = []
        subnet_uuids = []
        for nic in nics:
            subnet_ref = nic.get("subnet_reference", {})
            sub_uuid = subnet_ref.get("uuid", "")
            if sub_uuid:
                subnet_uuids.append(sub_uuid)

            # IP endpoints from status-side nic_list
            ip_endpoint_list = nic.get("ip_endpoint_list", [])
            for ep in ip_endpoint_list:
                ip = ep.get("ip", "")
                if ip:
                    ip_addresses.append(ip)

        if not ip_addresses:
            logger.debug("VM %s (%s) has no IP addresses; skipping.", vm_name, vm_uuid)
            return None

        return {
            "uuid": vm_uuid,
            "hostname": vm_name,
            "ip_address": ip_addresses[0],  # Primary IP
            "all_ips": ip_addresses,
            "subnet_uuids": subnet_uuids,
            "creation_date": creation_date,
        }
    except Exception as exc:
        logger.error("Error extracting VM info: %s\n%s", exc, traceback.format_exc())
        return None


def filter_by_subnets(vm_list: list, monitored_subnets: set) -> list:
    """Return only VMs that are attached to at least one monitored subnet."""
    filtered = []
    for vm in vm_list:
        if vm is None:
            continue
        vm_subnets = set(vm.get("subnet_uuids", []))
        if vm_subnets & monitored_subnets:
            filtered.append(vm)
        else:
            logger.debug("VM %s not on monitored subnets; skipping.", vm.get("hostname"))
    logger.info("VMs on monitored subnets: %d", len(filtered))
    return filtered


def identify_new_vms(current_vms: list, previous_inventory: dict) -> list:
    """
    Compare current VMs to the previous inventory.
    A VM is 'new' if its UUID was not present in the previous inventory.
    """
    new_vms = []
    for vm in current_vms:
        if vm["uuid"] not in previous_inventory:
            logger.info("New VM detected: %s (%s)", vm["hostname"], vm["uuid"])
            new_vms.append(vm)
    logger.info("Total new VMs identified: %d", len(new_vms))
    return new_vms


def write_csv_report(new_vms: list, report_dir: str) -> str:
    """
    Write the new VMs CSV report.
    Columns: Hostname, IP Address, FQDN, VM Creation Date
    Returns the path to the written CSV file.
    """
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(report_dir, f"new_vms_{timestamp}.csv")

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Hostname", "IP Address", "FQDN", "VM Creation Date"])
            for vm in new_vms:
                fqdn = reverse_dns_lookup(vm["ip_address"])
                writer.writerow([
                    vm["hostname"],
                    vm["ip_address"],
                    fqdn,
                    vm["creation_date"],
                ])
        logger.info("CSV report written to %s with %d entries.", csv_path, len(new_vms))
    except Exception as exc:
        logger.error("Failed to write CSV report: %s\n%s", exc, traceback.format_exc())
        csv_path = ""

    return csv_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Nutanix VM Discovery Script")
    parser.add_argument("--config", default="config/settings.ini",
                        help="Path to settings.ini configuration file")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Nutanix VM Discovery – starting run")
    logger.info("=" * 60)

    config = load_config(args.config)

    # Read Nutanix settings
    api_endpoint = config.get("nutanix", "api_endpoint")
    username = config.get("nutanix", "username")
    password = config.get("nutanix", "password")
    verify_ssl = config.getboolean("nutanix", "verify_ssl", fallback=False)
    monitored_subnets_raw = config.get("nutanix", "monitored_subnets", fallback="")
    monitored_subnets = {s.strip() for s in monitored_subnets_raw.split(",") if s.strip()}

    if not monitored_subnets:
        logger.error("No monitored_subnets defined in config. Exiting.")
        sys.exit(1)

    logger.info("Monitored subnets: %s", monitored_subnets)

    # Paths
    prev_inventory_path = config.get("paths", "previous_inventory")
    report_dir = config.get("paths", "nutanix_report_dir")

    # Load previous inventory
    previous_inventory = load_previous_inventory(prev_inventory_path)

    # Query Nutanix
    client = NutanixClient(api_endpoint, username, password, verify_ssl)

    try:
        raw_vms = client.list_vms()
    except Exception as exc:
        logger.error("Fatal: could not retrieve VM list from Nutanix: %s\n%s",
                      exc, traceback.format_exc())
        sys.exit(1)

    # Extract structured info
    parsed_vms = [extract_vm_info(vm) for vm in raw_vms]
    parsed_vms = [vm for vm in parsed_vms if vm is not None]
    logger.info("Parsed VMs with IP addresses: %d", len(parsed_vms))

    # Filter to monitored subnets
    subnet_vms = filter_by_subnets(parsed_vms, monitored_subnets)

    # Build current inventory dict (keyed by UUID)
    current_inventory = {vm["uuid"]: vm for vm in subnet_vms}

    # Identify new VMs
    new_vms = identify_new_vms(subnet_vms, previous_inventory)

    # Write CSV report
    if new_vms:
        csv_path = write_csv_report(new_vms, report_dir)
        if csv_path:
            logger.info("New VM report: %s", csv_path)
    else:
        logger.info("No new VMs found on this run.")

    # Save current inventory for next run comparison
    save_current_inventory(prev_inventory_path, current_inventory)

    logger.info("=" * 60)
    logger.info("Nutanix VM Discovery – run complete")
    logger.info("=" * 60)

    return new_vms


if __name__ == "__main__":
    main()
