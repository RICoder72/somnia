#!/usr/bin/env python3
"""
Outlook OAuth Bootstrap — Device Code Flow

Run this script once per Outlook account to obtain initial OAuth tokens.
After that, Vigil's OutlookAdapter handles silent token refresh automatically.

Usage:
    python outlook_oauth_bootstrap.py --client-id YOUR_CLIENT_ID \
                                       --tenant-id YOUR_TENANT_ID \
                                       --token-path /data/tokens/outlook_token_work.json

For personal Microsoft accounts, use --tenant-id consumers
For M365 / Exchange Online, use your org's tenant ID (GUID) or domain

Prerequisites:
    1. Register an app in Azure Portal → App registrations
    2. Add redirect URI: https://login.microsoftonline.com/common/oauth2/nativeclient
    3. Under "API permissions", add Microsoft Graph delegated permissions:
       - Mail.Read
       - Mail.ReadWrite
       - Mail.Send
       - User.Read
    4. Under "Authentication", enable "Allow public client flows" (for device code)
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import msal
except ImportError:
    print("ERROR: msal not installed. Run: pip install msal")
    sys.exit(1)


GRAPH_SCOPES = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/User.Read",
]


def main():
    parser = argparse.ArgumentParser(description="Outlook OAuth Bootstrap (Device Code)")
    parser.add_argument("--client-id", required=True, help="Azure AD Application (client) ID")
    parser.add_argument("--tenant-id", default="common",
                        help="Azure AD tenant ID (default: 'common' for multi-tenant)")
    parser.add_argument("--token-path", required=True,
                        help="Path to save token cache JSON")
    args = parser.parse_args()

    authority = f"https://login.microsoftonline.com/{args.tenant_id}"

    cache = msal.SerializableTokenCache()
    token_path = Path(args.token_path)

    # Load existing cache if present
    if token_path.exists():
        cache.deserialize(token_path.read_text())

    app = msal.PublicClientApplication(
        args.client_id,
        authority=authority,
        token_cache=cache,
    )

    # Check for existing accounts first
    accounts = app.get_accounts()
    if accounts:
        print(f"Found cached account: {accounts[0].get('username', 'unknown')}")
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            print("✅ Token still valid — refreshed successfully.")
            _save_cache(cache, token_path)
            _verify_token(result["access_token"])
            return

    # Device code flow
    print("\\nStarting device code flow...")
    flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)

    if "user_code" not in flow:
        print(f"ERROR: Failed to initiate device flow: {json.dumps(flow, indent=2)}")
        sys.exit(1)

    print(f"\\n{'='*60}")
    print(f"  Go to: {flow['verification_uri']}")
    print(f"  Enter code: {flow['user_code']}")
    print(f"{'='*60}")
    print(f"\\nWaiting for authentication (expires in {flow.get('expires_in', 900)}s)...")

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" in result:
        print(f"\\n✅ Authentication successful!")
        _save_cache(cache, token_path)
        _verify_token(result["access_token"])
    else:
        print(f"\\n❌ Authentication failed:")
        print(json.dumps(result, indent=2))
        sys.exit(1)


def _save_cache(cache: msal.SerializableTokenCache, path: Path):
    """Save the token cache."""
    if cache.has_state_changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cache.serialize())
        print(f"💾 Token cache saved to: {path}")


def _verify_token(access_token: str):
    """Quick verification — fetch user profile."""
    try:
        import requests
        resp = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        profile = resp.json()
        email = profile.get("mail") or profile.get("userPrincipalName", "unknown")
        print(f"👤 Authenticated as: {profile.get('displayName', 'N/A')} ({email})")
        print(f"\\nNext step: Add this account to /data/config/mail_accounts.json:")
        print(f'  "{email}": {{')
        print(f'    "adapter": "outlook",')
        print(f'    "credentials_ref": "",')
        print(f'    "config": {{')
        print(f'      "token_path": "{path}",')
        print(f'      "client_id": "<your-client-id>",')
        print(f'      "tenant_id": "<your-tenant-id>"')
        print(f'    }}')
        print(f'  }}')
    except Exception as e:
        print(f"⚠️ Token obtained but verification failed: {e}")


if __name__ == "__main__":
    main()
