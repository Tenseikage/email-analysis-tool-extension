import json
import os
import imaplib
import email
from email.header import decode_header, make_header

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
OUTPUT_FILE = "emails.json"


def decode_email_value(value):
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def get_mailbox_messages():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_PASSWORD)
    mail.select("INBOX")

    status, data = mail.uid("SEARCH", "ALL")
    if status != "OK":
        raise RuntimeError("Unable to fetch emails from Gmail.")

    message_ids = data[0].split()
    all_messages = []

    for message_id in message_ids:
        status, fetch_data = mail.uid("FETCH", message_id, "(RFC822 FLAGS)")
        if status != "OK":
            continue

        email_data = None
        flags = ""

        for part in fetch_data:
            if isinstance(part, tuple):
                email_data = part[1]
                flags = str(part[0], "utf-8", errors="ignore")
                break

        if not email_data:
            continue

        msg = email.message_from_bytes(email_data)
        is_read = "\\Seen" in flags or "SEEN" in flags

        message = {
            "uid": message_id.decode("utf-8", errors="ignore"),
            "subject": decode_email_value(msg.get("Subject")),
            "from": decode_email_value(msg.get("From")),
            "to": decode_email_value(msg.get("To")),
            "date": decode_email_value(msg.get("Date")),
            "read": is_read,
            "status": "read" if is_read else "unread",
        }
        all_messages.append(message)

    mail.logout()

    read_messages = [m for m in all_messages if m["read"]]
    unread_messages = [m for m in all_messages if not m["read"]]

    export = {
        "total": len(all_messages),
        "read": len(read_messages),
        "unread": len(unread_messages),
        "read": read_messages,
        "unread": unread_messages,
        "all": all_messages,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(export, file, ensure_ascii=False, indent=2)

    print(f"Export completed : {len(all_messages)} emails saved to {OUTPUT_FILE}")
    print(f"Read : {len(read_messages)}")
    print(f"Unread : {len(unread_messages)}")

    return export


if __name__ == "__main__":
    get_mailbox_messages()
