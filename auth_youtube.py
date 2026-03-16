"""
One-time OAuth authorization script.
Run this once to generate token.json — after that everything is automatic.
"""

import json
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

def main():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
            creds = flow.run_local_server(port=3000)

        with open("token.json", "w") as f:
            f.write(creds.to_json())

    print("\n✅ Authorization successful! token.json saved.")
    print("   You can now run fetch_channel_data.py and generate_report.py as normal.")
    print("   token.json auto-refreshes — you won't need to do this again.")

if __name__ == "__main__":
    main()
