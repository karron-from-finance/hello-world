# Network Security Automation

Automated VM discovery and Palo Alto Panorama TS-Agent provisioning pipeline.

## Overview

This project automates two manual network engineering workflows:

1. **Nutanix VM Discovery** — Detects new VMs created on monitored subnets by comparing the current Nutanix inventory to the previous run.
2. **Panorama TS-Agent Provisioning** — Adds newly discovered VMs as TS-Agent entries in Palo Alto Panorama, validates/commits/pushes the configuration, and verifies connectivity on a sample firewall.

## Project Structure

```
├── config/
│   └── settings.ini                    # All configuration (API endpoints, credentials, paths)
├── nutanix_vm_discovery/
│   └── discover_vms.py                 # VM discovery script
├── panorama_ts_agent/
│   └── provision_ts_agents.py          # Panorama provisioning script
├── output/
│   ├── nutanix_reports/                # VM discovery CSV reports + inventory state
│   └── panorama_reports/               # TS-Agent status CSV reports
├── Claude Code Change Logs/            # Session-by-session change logs
├── CLAUDE.md                           # AI assistant context and session history
├── requirements.txt                    # Python dependencies
└── README.md                           # This file
```

## Prerequisites

- Python 3.10+
- Network access to Nutanix Prism Central, Panorama, and target firewalls
- Valid API credentials for all systems

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Edit `config/settings.ini` with your environment-specific values:

| Section      | Key                      | Description                                              |
|--------------|--------------------------|----------------------------------------------------------|
| `nutanix`    | `api_endpoint`           | Prism Central URL (e.g., `https://pc.example.com:9440`)  |
| `nutanix`    | `username` / `password`  | Prism Central credentials                                |
| `nutanix`    | `monitored_subnets`      | Comma-separated subnet UUIDs to watch                    |
| `panorama`   | `host`                   | Panorama management address                              |
| `panorama`   | `api_key`                | Panorama API key                                         |
| `panorama`   | `template_name`          | Template name (default: `GLOBAL`)                        |
| `panorama`   | `group_mapping_profile`  | User-ID group mapping profile (default: `USER-GRO-MAP-PRO`) |
| `panorama`   | `domain_suffix`          | Domain appended to hostname (default: `xyz.com`)         |
| `firewall`   | `sample_firewall_host`   | Firewall used for post-push TS-Agent status verification |
| `firewall`   | `sample_firewall_api_key`| Firewall API key                                         |

## Scripts

### 1. Nutanix VM Discovery (`nutanix_vm_discovery/discover_vms.py`)

Queries Nutanix Prism Central v3 API to list all VMs, filters to monitored subnets, and compares against the previous inventory to identify new VMs.

**What it does:**
- Calls `/api/nutanix/v3/vms/list` with pagination
- Filters VMs by subnet UUID membership
- Compares against `previous_vm_inventory.json` (auto-maintained)
- Performs reverse DNS lookup on each new VM's IP
- Writes a CSV with columns: `Hostname`, `IP Address`, `FQDN`, `VM Creation Date`

**Usage:**
```bash
python nutanix_vm_discovery/discover_vms.py --config config/settings.ini
```

**Output:** `output/nutanix_reports/new_vms_<timestamp>.csv`

### 2. Panorama TS-Agent Provisioning (`panorama_ts_agent/provision_ts_agents.py`)

Reads the VM CSV from step 1 and provisions each VM as a TS-Agent in Panorama.

**What it does:**
- Reads the new VMs CSV
- Checks existing TS-Agent entries in Panorama to avoid duplicates
- Adds each new VM as a TS-Agent entry with:
  - `name` = hostname in ALL CAPS
  - `host` = `hostname.xyz.com` (configurable domain suffix)
- Validates the Panorama candidate configuration
- Commits the configuration to Panorama
- Pushes the configuration to managed firewalls
- Waits 5 minutes for TS-Agents to connect
- Queries a sample firewall for TS-Agent connection status
- Writes a status report CSV with columns: `VM Hostname`, `Agent Created Successfully`, `Connection Status`

**Usage:**
```bash
python panorama_ts_agent/provision_ts_agents.py \
  --csv output/nutanix_reports/new_vms_20260308_120000.csv \
  --config config/settings.ini
```

**Output:** `output/panorama_reports/ts_agent_status_<timestamp>.csv`

## Typical Workflow

```bash
# Step 1: Discover new VMs
python nutanix_vm_discovery/discover_vms.py --config config/settings.ini

# Step 2: Provision TS-Agents using the latest CSV from step 1
python panorama_ts_agent/provision_ts_agents.py \
  --csv output/nutanix_reports/new_vms_<timestamp>.csv \
  --config config/settings.ini
```

## Error Handling

Both scripts include comprehensive error handling:
- All API calls are wrapped in try/except with specific exception types
- Verbose logging with full tracebacks on errors
- Deterministic behavior: scripts fail explicitly rather than silently proceeding
- Duplicate detection prevents re-creating existing TS-Agent entries
- Job status polling with configurable timeout prevents indefinite waits

## Logging

All scripts log to stdout at INFO level by default. Errors include full stack traces for debugging.
