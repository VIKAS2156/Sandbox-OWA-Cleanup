import argparse #to build cli tool
import json
import subprocess #to run commands inside python script
import sys
from urllib.parse import quote, urlparse
from simple_salesforce import Salesforce

def get_sf_connection(org_alias):
    result = subprocess.run(['sf', 'org', 'display', '--json', '-o', org_alias], 
                            capture_output=True, text=True, shell=True)
    if result.returncode != 0:
        print(f"Error: Could not display org {org_alias}. {result.stderr}")
        sys.exit(1)
        
    try:
        org_data = json.loads(result.stdout)
        org_info = org_data['result']
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Failed to parse CLI output: {e}")
        sys.exit(1)
    
    instance_url = org_info['instanceUrl']
    parsed_url = urlparse(instance_url)
    hostname = parsed_url.hostname
    
    # Enforce safe Enhanced Domains structure matching production parameters
    if hostname and 'force.com' in hostname and 'my.salesforce.com' not in hostname:
        clean_host = hostname.replace('.my.salesforce.com', '').replace('.force.com', '')
        if '--' in clean_host and '.sandbox' not in clean_host:
            hostname = f"{clean_host}.sandbox.my.salesforce.com"
        else:
            hostname = f"{clean_host}.my.salesforce.com"
            
    normalized_url = f"https://{hostname}"
    print(f"Connecting to: {normalized_url}")
    
    return Salesforce(instance_url=normalized_url, session_id=org_info['accessToken'])


# --- Command 1: Preview ---
def handle_preview(args):
    sf = get_sf_connection(args.org)
    with open(args.config, 'r') as f: config = json.load(f)
    
    print(f"--- DEEP SCANNING ALERTS FOR {args.org} ---")
    
    # Fetch existing Sandbox OWAs
    owa_recs = sf.query("SELECT Id, Address FROM OrgWideEmailAddress")['records']
    owa_addresses_lower = [owa['Address'].lower().strip() for owa in owa_recs]
    
    # Get All Alerts from Tooling API
    query_str = quote("SELECT Id, DeveloperName FROM WorkflowAlert")
    alerts_response = sf.toolingexecute(f"query?q={query_str}")
    alerts = alerts_response.get('records', [])

    print(f"Found {len(alerts)} Email Alerts. Inspecting metadata components...")

    manifest = {"to_create": [], "to_swap": []}
    prod_emails = [m['production_email'].lower().strip() for m in config['owa_mappings']]

    for alert in alerts:
        # Retrieve detailed Tooling object metadata payload layer
        full_alert = sf.toolingexecute(f"sobjects/WorkflowAlert/{alert['Id']}")
        metadata = full_alert.get('Metadata', {})
        
        # Pull senderAddress directly from the deep metadata config block
        current_email_in_alert = metadata.get('senderAddress') or full_alert.get('SenderAddress') or "" #checking for both ld and new api versions
        current_email_in_alert = str(current_email_in_alert).lower().strip()
        
        print(f"  -> Alert '{alert['DeveloperName']}' field reads: '{current_email_in_alert}'")
        
        if current_email_in_alert in prod_emails:
            mapping = next(m for m in config['owa_mappings'] if m['production_email'].lower().strip() == current_email_in_alert)
            print(f"     [MATCHED] Ready to swap to {mapping['sandbox_email']}")
            
            manifest['to_swap'].append({
                "alert_id": alert['Id'], 
                "developer_name": alert['DeveloperName'], 
                "target_email": mapping['sandbox_email']
            })

    # Identify missing sandbox configuration definitions
    for mapping in config['owa_mappings']:
        if mapping['sandbox_email'].lower().strip() not in owa_addresses_lower:
            manifest['to_create'].append(mapping)

    with open('execution_manifest.json', 'w') as f:
        json.dump(manifest, f, indent=4)
        
    print(f"\n[SUCCESS] Manifest generated. Need Creation: {len(manifest['to_create'])} | Swaps identified: {len(manifest['to_swap'])}")


# --- Command 2: Provision ---
def handle_provision(args):
    sf = get_sf_connection(args.org)
    with open('execution_manifest.json', 'r') as f: manifest = json.load(f)
    
    for owa in manifest['to_create']:
        payload = {
            "Address": owa['sandbox_email'], 
            "DisplayName": owa['display_name'], 
            "Purpose": owa.get('purpose', 'UserSelection').replace(" ", ""),
            "IsAllowAllProfiles": True
        }
        try:
            sf.OrgWideEmailAddress.create(payload)
            print(f"[CREATED] {owa['sandbox_email']} with 'All Profiles' access.")
        except Exception as e:
            print(f"[SKIP/ERROR] {owa['sandbox_email']}: {e}")

    print("\nCRITICAL: Verify the verification link in the sandbox email inbox, then run 'swap'.")


# --- Command 3: Swap ---
def handle_swap(args):
    sf = get_sf_connection(args.org)
    try:
        with open('execution_manifest.json', 'r') as f: manifest = json.load(f)
    except FileNotFoundError:
        print("Error: execution_manifest.json not found. Run 'preview' first.")
        sys.exit(1)
    
    owa_records = sf.query("SELECT Id, Address FROM OrgWideEmailAddress WHERE IsVerified = true")['records']
    verified_map = {owa['Address'].lower().strip(): owa['Id'] for owa in owa_records}

    print(f"--- STARTING SWAP FOR {args.org} ---")
    
    for action in manifest['to_swap']:
        target_email = action['target_email'].lower().strip()
        alert_id = action['alert_id']

        if target_email in verified_map:
            try:
                check = sf.toolingexecute(f"sobjects/WorkflowAlert/{alert_id}")
                current_metadata = check.get('Metadata', {})
                
                invalid_keys = ['developerName', 'id', 'ManageableState', 'CreatedDate', 'LastModifiedDate']
                for key in invalid_keys:
                    current_metadata.pop(key, None)
                
                current_metadata['senderType'] = "OrgWideEmailAddress"
                current_metadata['senderAddress'] = target_email

                payload = {
                    "Metadata": current_metadata
                }
                sf.toolingexecute(f"sobjects/WorkflowAlert/{alert_id}", method="PATCH", data=payload)
                
                # IMMEDIATE RE-CHECK VERIFICATION
                verify_check = sf.toolingexecute(f"sobjects/WorkflowAlert/{alert_id}")
                verify_metadata = verify_check.get('Metadata', {})
                saved_sender = str(verify_metadata.get('senderAddress') or verify_check.get('SenderAddress') or "").lower().strip()
                
                if saved_sender == target_email:
                    print(f"[SUCCESS] Alert '{action['developer_name']}' successfully migrated to {target_email}")
                else:
                    print(f"[FAIL] Server processed request but internal tracking value is still: {saved_sender}")
                
            except Exception as e:
                print(f"[ERROR] API Exception updating alert {action['developer_name']}: {e}")
        else:
            print(f"[PENDING] {target_email} skipped. Click the verification link in the sandbox inbox first!")
            
    print("\n[COMPLETE] Script finished. Verify changes in Salesforce Setup.")

# --- Command 4: Cleanup ---
def handle_cleanup(args):
    sf = get_sf_connection(args.org)
    
    with open(args.config, 'r') as f: config = json.load(f)
        
    prod_emails = [m['production_email'].lower().strip() for m in config['owa_mappings']]
    
    print(f"--- STARTING PRODUCTION OWA CLEANUP FOR {args.org} ---")
    
    owa_recs = sf.query("SELECT Id, Address, DisplayName FROM OrgWideEmailAddress")['records']
    
    deleted_count = 0
    for owa in owa_recs:
        email = owa['Address'].lower().strip()
        
        if email in prod_emails:
            print(f"Found production OWA: {owa['Address']} ({owa['DisplayName']})")
            try:
                sf.OrgWideEmailAddress.delete(owa['Id'])
                print(f"  [DELETED] Successfully removed {owa['Address']}")
                deleted_count += 1
            except Exception as e:
                print(f"  [ERROR] Could not delete {owa['Address']}: {e}")
                print("  [TIP] If an Email Alert is still using this address, you must run 'swap' first.")

    print(f"\n[COMPLETE] Cleanup finished. Removed {deleted_count} production OWA addresses.")


# --- Main CLI Logic ---
def main():
    parser = argparse.ArgumentParser(description="Salesforce Sandbox Email OWA Cleanup Tool")
    parser.add_argument('--org', '-o', required=True, help="Salesforce Org Alias")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser('preview', help="Analyze sandbox data parameters").add_argument('--config', '-c', default='project_config.json')
    subparsers.add_parser('provision', help="Deploy brand new OWA entries")
    subparsers.add_parser('swap', help="Perform routing swap adjustments")
    subparsers.add_parser('cleanup', help="Remove production OWA entries from the sandbox").add_argument('--config', '-c', default='project_config.json')

    args = parser.parse_args()
    if args.command == 'preview': handle_preview(args)
    elif args.command == 'provision': handle_provision(args)
    elif args.command == 'swap': handle_swap(args)
    elif args.command == 'cleanup': handle_cleanup(args)

if __name__ == "__main__":
    main()
