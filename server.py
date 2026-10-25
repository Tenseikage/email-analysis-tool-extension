import json
import os
import re
from pathlib import Path
from flask_cors import CORS
from flask import Flask, jsonify, redirect, request, session
from google import genai
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

#Authorizing requests from Chrome extension and Localhost
app = Flask(__name__)
CORS(app, support_credentials=True)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CLIENT_SECRET_FILE = Path(__file__).with_name("client_secret.json")
CREDENTIALS_FILE = Path(__file__).with_name("gmail_credentials.json")
OAUTH_STATE_FILE = Path(__file__).with_name(".oauth_state.json")
OUTPUT_FILE = Path(__file__).with_name("emails.json")
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/oauth/callback")
_gemini_client = None


def get_gemini_client():
    global _gemini_client

    if _gemini_client is not None:
        return _gemini_client

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Define it in the server environment."
        )
    _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def get_flow():
    if not CLIENT_SECRET_FILE.exists():
        raise RuntimeError(
            "client_secret.json file not found.\n"
            "Download it from Google Cloud and place it in the project directory."
        )

    with CLIENT_SECRET_FILE.open("r", encoding="utf-8") as f:
        client_config = json.load(f)

    if "web" not in client_config:
        raise RuntimeError(
            "client_secret.json must contain a 'web' configuration. "
            "Create a OAuth client of type Web Application and add "
            f"'{REDIRECT_URI}' to the authorized redirect URIs."
        )

    return Flow.from_client_secrets_file(
        str(CLIENT_SECRET_FILE),
        scopes=SCOPES,
    )


def load_credentials():
    if not CREDENTIALS_FILE.exists():
        return None

    with CREDENTIALS_FILE.open("r", encoding="utf-8") as f:
        info = json.load(f)

    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            app.logger.warning("Stored Gmail credentials are no longer valid; clearing them.")
            CREDENTIALS_FILE.unlink(missing_ok=True)
            return None
        save_credentials(creds)
    return creds


def save_credentials(creds):
    with CREDENTIALS_FILE.open("w", encoding="utf-8") as f:
        json.dump(json.loads(creds.to_json()), f, ensure_ascii=False, indent=2)


def save_email_export(export):
    """Replace the local export with the result of the current Gmail query."""
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)


def extract_email_address(sender_str):
    """Extracts the email address from a sender string."""
    if not sender_str:
        return ""
    match = re.search(r'<([^>]+)>', sender_str)
    return match.group(1) if match else sender_str.strip()

@app.route("/")
def index():
    return jsonify({
        "status": "online",
        "service": "Gmail Phishing Analysis Server",
        "endpoints": {
            "login": "/login",
            "callback": "/oauth/callback",
            "emails": "/emails",
            "ai_analyze": "/ai/analyze",
            "logout": "/logout",
        }
    })

@app.route("/login")
def login():
    flow = get_flow()
    flow.redirect_uri = REDIRECT_URI
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["state"] = state
    with OAUTH_STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump({"state": state}, f, ensure_ascii=False, indent=2)
    return redirect(authorization_url)


@app.route("/logout")
def logout():
    CREDENTIALS_FILE.unlink(missing_ok=True)
    OAUTH_STATE_FILE.unlink(missing_ok=True)
    session.pop("state", None)
    return jsonify({"message": "Logged out successfully."})

@app.route("/oauth/callback")
def oauth_callback():
    if request.args.get("error"):
        return jsonify({"error": request.args.get("error")}), 400
    state = request.args.get("state")
    if not state and OAUTH_STATE_FILE.exists():
        with OAUTH_STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f).get("state")

    code = request.args.get("code")
    if not state or not code:
        return jsonify({"error": "Missing OAuth code."}), 400
    flow = get_flow()
    flow.redirect_uri = REDIRECT_URI
    flow.fetch_token(code=code)
    OAUTH_STATE_FILE.unlink(missing_ok=True)
    session.pop("state", None)
    creds = flow.credentials
    save_credentials(creds)
    return jsonify({"message": "Gmail authorization successful.", "status": "connected"})


@app.route("/emails")
def emails():
    creds = load_credentials()
    if creds is None:
        return jsonify({"error": "Gmail credentials not found. Please log in."}), 401
    try:
        service = build("gmail", "v1", credentials=creds)
        # 1. Obtain all unread emails from the user's inbox.
        messages_refs = []
        page_token = None
        while True:
            list_request = service.users().messages().list(
                userId="me",
                q='is:inbox is:unread category:primary',
                maxResults=100,
                pageToken=page_token,
            )
            list_response = list_request.execute()
            messages_refs.extend(list_response.get("messages", []))
            page_token = list_response.get("nextPageToken")
            if not page_token:
                break
        if not messages_refs:
            export = {
                "all": [],
                "total": 0,
                "read_count": 0,
                "unread_count": 0,
            }
            save_email_export(export)
            return jsonify(export)
        fetched_details = []
        # 2. Callback executed for each email to fetch its details
        def fetch_email_details(request_id, response, exception):
            if exception is not None:
                app.logger.warning(
                    "Unable to retrieve Gmail message %s: %s",
                    request_id,
                    exception,
                )
                return
            fetched_details.append(response)
        # 3. Fetch details in batches to stay within Gmail batch limits.
        for start in range(0, len(messages_refs), 50):
            batch = service.new_batch_http_request(callback=fetch_email_details)
            for msg_ref in messages_refs[start:start + 50]:
                batch.add(service.users().messages().get(
                    userId="me",
                    id=msg_ref["id"],
                    format="metadata",
                    metadataHeaders=[
                        "Subject",
                        "From",
                        "To",
                        "Date",
                        "Authentication-Results",
                    ],
                ))
            batch.execute()
        # 4. Process the fetched details to extract relevant information
        export = {
            "total": 0,
            "read_count": 0,
            "unread_count": 0,
            "all": [],
        }
        for msg in fetched_details:
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            is_read = "UNREAD" not in msg.get("labelIds", [])
            item = {
                "id": msg.get("id"),
                "from_raw": headers.get("From", ""),
                "from_email": extract_email_address(headers.get("From", "")),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "auth_results": headers.get("Authentication-Results", ""),
                "read": is_read,
                "status": "read" if is_read else "unread",
            }
            if is_read:
                continue
            export["all"].append(item)
            export["unread_count"] += 1
        export["total"] = len(export["all"])
        # 5. Save the export to a local JSON file
        save_email_export(export)
        return jsonify(export)
    except Exception as error:
        return jsonify({"error": f"Error while retrieving mails : {str(error)}"}), 502

# Call the Gemini AI API to analyze the email content
@app.route("/ai/analyze", methods=["GET", "POST"])
def analyze_with_ai():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    sender_email = str(data.get("sender", "")).strip()
    subject = str(data.get("subject", "")).strip()

    if not text:
        return jsonify({"error": "The email text is required."}), 400

    prompt = f"""Analyze the following email content for potential phishing threats. 
    Provide a detailed analysis, including any suspicious elements, and suggest whether it is likely to be a phishing attempt or not. 
    MAIL INFORMATION:\n
    - Sender: {sender_email}\n
    - Subject: {subject}\n
    - Content: {text}\n\n
    Answer EXCLUSIVELY in JSON format with the following structure:
    {{
        "risk_level": "low" | "medium" | "high",
        "risk score": 0-100,
        "suspicious_elements": ["list of suspicious elements found in the email (e.g., links, attachments, sender address anomalies),sense of urgency, grammatical errors, etc."],
        "summary": "A brief summary of the analysis and recommendation."
    }}
    """

    try:
        response = get_gemini_client().models.generate_content(
            model="gemini-3.6-flash",
            contents = prompt,
            config = { "response_mime_type": "application/json" }

        )
        output_filename = Path(__file__).with_name("ai_analysis_output.json")
        with output_filename.open("w", encoding="utf-8") as f:
            json.dump(response, f, ensure_ascii=False, indent=2)
        return jsonify(response)

    except Exception as e:
        return jsonify({"error": f"Error while analyzing email with AI: {str(e)}"}), 500

        


if __name__ == "__main__":
    app.run(debug=False, port=5000)
 