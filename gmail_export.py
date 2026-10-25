import json
import os
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
PROJECT_DIR = Path(__file__).resolve().parent
STATE_FILE = PROJECT_DIR / "gmail.state.json"
OUTPUT_FILE = PROJECT_DIR / "emails.json"
CREDENTIALS_FILE = PROJECT_DIR / "client_secret.json"
TOKEN_FILE = PROJECT_DIR / "gmail_credentials.json"

def get_gmail_service():
    """Manage OAuth2 authentication and return the Gmail service."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"OAuth client file not found: {CREDENTIALS_FILE}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
        with TOKEN_FILE.open('w', encoding='utf-8') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

def parse_messages_headers(messages_payload):
    "Extract and parse the headers from the messages payload"
    headers = messages_payload.get('headers', [])
    parsed_headers = {}
    for header in headers:
        name = header.get('name').lower()
        if name in ['subject', 'from', 'to', 'date']:
            parsed_headers[name] = header.get('value')
    return parsed_headers


def save_export(messages):
    """Write only unread messages to the JSON export."""
    unread_messages = [m for m in messages if not m["read"]]
    export = {
        "total": len(unread_messages),
        "read_count": 0,
        "unread_count": len(unread_messages),
        "read": [],
        "unread": unread_messages
    }
    with OUTPUT_FILE.open('w', encoding='utf-8') as f:
        json.dump(export, f, ensure_ascii=False, indent=2)

def initial_full_export(service):
    """Performs a first full export and saves the initial historyId"""  
    print(f"First execution, performing full complete export of all emails...") 

    #1. Get the user's profile to retrieve the current historyId
    profile = service.users().getProfile(userId='me').execute()
    current_history_id = profile.get('historyId')

    #2. Fetch all messages in the user's mailbox (INBOX)
    results = service.users().messages().list(
        userId='me',
        #is:inbox is:unread category:primary 
        q='is:inbox is:unread category:primary'
    ).execute()
    message_summary = results.get('messages', [])
    all_messages = []
    for msg_references in message_summary:
        msg_id = msg_references.get('id')
        msg = service.users().messages().get(userId='me', id=msg_id, format='metadata', metadataHeaders=['Subject', 'From', 'To', 'Date']).execute()
        parsed_headers = parse_messages_headers(msg.get('payload', {}))
        message = {
            "id": msg_id,
            "subject": parsed_headers.get('subject'),
            "from": parsed_headers.get('from'),
            "to": parsed_headers.get('to'),
            "date": parsed_headers.get('date'),
            "read": "unread" not in msg.get('labelIds', []),
            "status": 'read' if "unread" not in msg.get('labelIds', []) else 'unread'
        }
        all_messages.append(message)
    state = {
        "historyId": current_history_id,
         "emails": all_messages
    }

    # 3. Local save and JSON export
    with STATE_FILE.open('w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    save_export(all_messages)
    print(f"Initial export completed. HistoryId saved to {STATE_FILE}. Total emails exported: {len(all_messages)}")

def sync_with_history_api(service,last_history_id):
    """Synchronize with Gmail using the History API to fetch new emails since the last historyId"""
    print(f"Synchronizing with Gmail using History API from historyId: {last_history_id}...")
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
            local_emails = {
                email['id']: email
                for email in state.get('emails', [])
                if not email.get('read', False)
            }
    else:
        local_emails = {}
    try:
        # Call the Gmail History API to get changes since the last historyId
        history_response = service.users().history.list(
            userId='me',
            startHistoryId=last_history_id,
            historyTypes=[
                'messageAdded',
                'messageDeleted',
                'labelAdded',
                'labelRemoved',
            ],
        ).execute()
    except Exception as e:
        print(f"Error occurred while fetching history: {e}")
        return
    histories_records = history_response.get('history', [])
    new_history_id = history_response.get('historyId', last_history_id)
    if not histories_records:
        with STATE_FILE.open('w', encoding='utf-8') as f:
            json.dump(
                {"historyId": new_history_id, "emails": list(local_emails.values())},
                f,
                ensure_ascii=False,
                indent=2,
            )
        save_export(list(local_emails.values()))
        print("No new changes found since the last historyId.")
        return
    for hist in histories_records:
        # 1. New messages added
        for item in hist.get('messagesAdded', []):
            msg_id = item['message']['id']
            msg = service.users().messages().get(userId='me', id=msg_id, format='metadata', metadataHeaders=['Subject', 'From', 'To', 'Date']).execute()
            if 'UNREAD' not in msg.get('labelIds', []):
                continue
            parsed_headers = parse_messages_headers(msg.get('payload', {}))
            message = {
                "id": msg_id,
                "subject": parsed_headers.get('subject'),
                "from": parsed_headers.get('from'),
                "to": parsed_headers.get('to'),
                "date": parsed_headers.get('date'),
                "read": "unread" not in msg.get('labelIds', []),
                "status": 'read' if "unread" not in msg.get('labelIds', []) else 'unread'
            }
            local_emails[msg_id] = message
        for item in hist.get('messagesDeleted', []):
            msg_id = item['message']['id']
            local_emails.pop(msg_id, None)
        # 2. Changes in message status (read/unread)
        for item in hist.get('labelsAdded', []):
            msg_id = item['message']['id']
            if msg_id in local_emails and 'UNREAD' in item.get('labelIds', []):
                local_emails[msg_id]['read'] = False
                local_emails[msg_id]['status'] = 'unread'
        for item in hist.get('labelsRemoved', []):
            msg_id = item['message']['id']
            if msg_id in local_emails and 'UNREAD' in item.get('labelIds', []):
                local_emails.pop(msg_id, None)

    all_messages = list(local_emails.values())
    # Save the updated state with the new historyId and emails
    with STATE_FILE.open('w', encoding='utf-8') as f:
        json.dump({"historyId": new_history_id, "emails": all_messages}, f, ensure_ascii=False, indent=2)
    save_export(all_messages)
    print(f"Synchronization completed. Updated historyId saved to {STATE_FILE}. Total emails after sync: {len(all_messages)}, new historyId: {new_history_id}")



def main():
    service = get_gmail_service()

    if os.path.exists(STATE_FILE):
        with STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)
            last_history_id = state.get("historyId")

        if last_history_id:
            sync_with_history_api(service, last_history_id)
        else:
            initial_full_export(service)
    else:
        initial_full_export(service)


if __name__ == "__main__":
    main()
