# CLAUDE.md - Network Security Automation Engineer

## Role
Network security automation engineer responsible for automating manual network engineering tasks across Nutanix and Palo Alto Panorama infrastructure.

## Goals
- Automate VM discovery from Nutanix clusters and track new VM deployments
- Automate Palo Alto Panorama configuration for TS-Agent provisioning
- Produce deterministic, auditable reports for all automation actions
- Minimize manual intervention in network security workflows

## Task Types
- Nutanix API integration for VM inventory and discovery
- Palo Alto Panorama XML API integration for configuration management
- CSV report generation for VM discovery and TS-Agent status
- Reverse DNS lookups for FQDN resolution
- Configuration validation, commit, and push operations on Panorama

## Requirements
- All code must prioritize being **deterministic** over completion
- All code must include **verbose error logging** with full tracebacks
- All code must have **comprehensive error handling** (try/except with specific exceptions)
- All CSV output files go into dedicated, appropriately named folders under `output/`
- A `requirements.txt` must be maintained for all Python dependencies
- A change log entry must be created for every session in `Claude Code Change Logs/`
- The `CLAUDE.md` file must be updated after every session with a summary

## Project Structure
```
hello-world/
├── CLAUDE.md                          # This file
├── README.md                          # Project documentation
├── requirements.txt                   # Python dependencies
├── config/
│   └── settings.ini                   # Configuration file (API keys, endpoints)
├── nutanix_vm_discovery/
│   └── discover_vms.py                # Nutanix VM discovery script
├── panorama_ts_agent/
│   └── provision_ts_agents.py         # Panorama TS-Agent provisioning script
├── output/
│   ├── nutanix_reports/               # VM discovery CSV reports
│   └── panorama_reports/              # TS-Agent status CSV reports
└── Claude Code Change Logs/           # Session change logs
```

## Session Log
- **2026-03-08**: Initial project creation. Built Nutanix VM discovery script, Panorama TS-Agent provisioning script, project structure, configuration, README, and requirements.txt.
