# Salesforce Sandbox Email OWA Cleanup Tool

A command-line utility for cleaning up **Org-Wide Email Addresses** in Salesforce sandboxes.

This tool helps sandbox administrators replace production Org-Wide Email Addresses used by Workflow Email Alerts with sandbox-safe email addresses. It can also provision missing sandbox Org-Wide Email Addresses and remove production addresses from the sandbox after the swap is complete.

## What This Tool Does

This script supports a four-step workflow:

1. **Preview**
   - Scans Workflow Email Alerts in a Salesforce sandbox.
   - Finds alerts still using production Org-Wide Email Addresses.
   - Identifies sandbox Org-Wide Email Addresses that need to be created.
   - Generates an `execution_manifest.json` file.

2. **Provision**
   - Creates missing sandbox Org-Wide Email Address records.
   - Enables access for all profiles.

3. **Swap**
   - Updates matching Workflow Email Alerts to use the verified sandbox email address.
   - Skips any sandbox Org-Wide Email Address that has not yet been verified.

4. **Cleanup**
   - Deletes production Org-Wide Email Address records from the sandbox.
   - Helps prevent sandbox email alerts from sending as production addresses.

## Important Safety Notes

This tool is intended for **Salesforce sandbox environments**.

Do not run this against production unless you have carefully reviewed and modified the script for that use case.

The `cleanup` command deletes Org-Wide Email Address records that match the configured production email addresses. Always run `preview` and `swap` first, then verify the results in Salesforce Setup before running `cleanup`.

## Requirements

Before using this tool, make sure you have:

- Python 3 installed
- Salesforce CLI installed and authenticated
- A Salesforce org alias for the target sandbox
- The `simple-salesforce` Python package installed
- Salesforce permissions sufficient to:
  - Read Org-Wide Email Addresses
  - Create Org-Wide Email Addresses
  - Delete Org-Wide Email Addresses
  - Read and update Workflow Email Alert metadata through the Tooling API
