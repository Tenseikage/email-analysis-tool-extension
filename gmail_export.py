import json
import os
#import imaplib
#import email
#from email.header import decode_header, make_header
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
OUTPUT_FILE = "emails.json"

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
STATE_FILE = "gmail.state.json"
OUTPUT_FILE = "emails.json"

def get_gmail_service():
    "Manange Oauth2 Authentification and return gmail service"
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
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
    """Create the finale JSON structure"""
    read_messages = [m for m in messages if m["read"]]
    unread_messages = [m for m in messages if not m["read"]]
    export = {
        "total": len(messages),
        "read_count": len(read_messages),
        "unread_count": len(unread_messages),
        "read": read_messages,
        "unread": unread_messages
    }
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(export, f, ensure_ascii=False, indent=2)

def initial_full_export(service):
    """Performs a first full export and saves the initial historyId"""  
    print(f"First execution, performing full complete export of all emails...") 

    #1. Get the user's profile to retrieve the current historyId
    profile = service.users().getProfile(userId='me').execute()
    current_history_id = profile.get('historyId')

    #2. Fetch all messages in the user's mailbox (INBOX)
    results = service.users().messages().list(userId='me', labelIds=['INBOX']).execute()
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
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    save_export(all_messages)
    print(f"Initial export completed. HistoryId saved to {STATE_FILE}. Total emails exported: {len(all_messages)}")

def sync_with_history_api(service,last_history_id):
    """Synchronize with Gmail using the History API to fetch new emails since the last historyId"""
    print(f"Synchronizing with Gmail using History API from historyId: {last_history_id}...")
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
            local_emails = {email['id']: email for email in state.get('emails', [])}
    else:
        local_emails = {}
    try:
        # Call the Gmail History API to get changes since the last historyId
        history_response = service.users().history.list(userId='me', startHistoryId=last_history_id, historyTypes=['messageAdded', 'messageDeleted']).execute()
    except Exception as e:
        print(f"Error occurred while fetching history: {e}")
        return
    histories_records = history_response.get('history', [])
    new_history_id = history_response.get('historyId', last_history_id)
    if not histories_records:
        print("No new changes found since the last historyId.")
        return
    for hist in histories_records:
        # 1. New messages added
        for item in hist.get('messagesAdded', []):
            msg_id = item['message']['id']
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
            local_emails[msg_id] = message
    # 2. Changes in message status (read/unread)
    if 'labelsAdded' in hist:
        for item in hist.get('labelsAdded', []):
            msg_id = item['message']['id']
            if msg_id in local_emails and 'UNREAD' in item.get('labelIds', [])  :
                local_emails[msg_id]['read'] = False
                local_emails[msg_id]['status'] = 'unread'
    if 'labelsRemoved' in hist:
        for item in hist.get('labelsRemoved', []):
            msg_id = item['message']['id']
            if msg_id in local_emails and 'UNREAD' in item.get('labelIds', []):
                local_emails[msg_id]['read'] = True
                local_emails[msg_id]['status'] = 'read' 

    all_messages = list(local_emails.values())
    # Save the updated state with the new historyId and emails
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump({"historyId": new_history_id, "emails": all_messages}, f, ensure_ascii=False, indent=2)
    save_export(all_messages)
    print(f"Synchronization completed. Updated historyId saved to {STATE_FILE}. Total emails after sync: {len(all_messages)}, new historyId: {new_history_id}")



def main():
    service = get_gmail_service()

    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            last_history_id = state.get("lastHistoryId")

        if last_history_id:
            sync_with_history_api(service, last_history_id)
        else:
            initial_full_export(service)
    else:
        initial_full_export(service)


if __name__ == "__main__":
    main()

