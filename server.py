import json
import os
from pathlib import Path

from flask import Flask, jsonify, redirect, request, session
from google import genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

app = Flask(__name__)
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
        creds.refresh(Request())
        save_credentials(creds)
    return creds


def save_credentials(creds):
    with CREDENTIALS_FILE.open("w", encoding="utf-8") as f:
        json.dump(json.loads(creds.to_json()), f, ensure_ascii=False, indent=2)


def save_oauth_state(state):
    with OAUTH_STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump({"state": state}, f)


def load_oauth_state():
    if not OAUTH_STATE_FILE.exists():
        return None

    try:
        with OAUTH_STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f).get("state")
    except (OSError, json.JSONDecodeError):
        return None


def clear_oauth_state():
    OAUTH_STATE_FILE.unlink(missing_ok=True)


def extract_sender(value):
    if not value:
        return ""
    if "<" in value and ">" in value:
        start = value.index("<") + 1
        end = value.index(">")
        return value[start:end].strip()
    return value.strip()


@app.route("/")
def index():
    return {
        "message": "Serveur Gmail OAuth OK",
        "endpoints": {
            "login": "/login",
            "callback": "/oauth/callback",
            "emails": "/emails",
            "ai_analyze": "/ai/analyze",
            "logout": "/logout",
        },
    }


@app.route("/ai/analyze", methods=["POST"])
def analyze_with_ai():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()

    if not text:
        return jsonify({"error": "The email text is required."}), 400

    try:
        response = get_gemini_client().models.generate_content(
            model="gemini-3.6-flash",
            contents=(
                "Analyse cet email en français. Indique le niveau de risque de phishing "
                "(faible, moyen ou élevé) et donne une explication concise.\n\n"
                f"{text}"
            ),
        )
    except Exception as error:
        return jsonify({"error": f"Gemini API error: {error}"}), 502

    return jsonify({"analysis": response.text})


@app.route("/login")
def login():
    flow = get_flow()
    flow.redirect_uri = REDIRECT_URI
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["state"] = state
    save_oauth_state(state)
    return redirect(auth_url)


@app.route("/oauth/callback")
def oauth_callback():
    if request.args.get("error"):
        return jsonify({
            "error": "OAuth error",
            "details": request.args.get("error_description", request.args["error"]),
        }), 400

    state = load_oauth_state() or session.get("state")
    if not state:
        return jsonify({
            "error": "OAuth session missing",
            "details": (
                "Open /login from the same address used for the callback "
                "(http://localhost:5000), then try again."
            ),
        }), 400

    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Code OAuth missing"}), 400

    flow = get_flow()
    flow.redirect_uri = REDIRECT_URI
    flow.fetch_token(state=state, code=code)
    clear_oauth_state()
    session.pop("state", None)
    save_credentials(flow.credentials)

    return jsonify({
        "message": "Gmail authentication successful",
        "status": "connected",
        "credentials_file": str(CREDENTIALS_FILE),
    })


@app.route("/logout")
def logout():
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()
    session.clear()
    clear_oauth_state()
    return jsonify({"message": "Logged out from Gmail", "status": "disconnected"})


@app.route("/emails")
def emails():
    creds = load_credentials()
    if creds is None:
        return jsonify({"error": "You are not connected. Go to /login first."}), 401

    service = build("gmail", "v1", credentials=creds)
    results = service.users().messages().list(userId="me", labelIds=["INBOX"]).execute()
    messages = results.get("messages", [])

    export = {
        "total": 0,
        "read": 0,
        "unread": 0,
        "read_senders": [],
        "unread_senders": [],
        "all": [],
    }

    for message in messages:
        details = service.users().messages().get(
            userId="me",
            id=message["id"],
            format="metadata",
            metadataHeaders=["From", "Date"],
        ).execute()

        headers = {h["name"]: h["value"] for h in details.get("payload", {}).get("headers", [])}
        sender = extract_sender(headers.get("From", ""))
        is_read = "UNREAD" not in details.get("labelIds", [])

        item = {
            "id": details.get("id"),
            "from": sender,
            "date": headers.get("Date", ""),
            "read": is_read,
            "status": "read" if is_read else "unread",
        }

        export["all"].append(item)
        if is_read:
            export["read_senders"].append(sender)
            export["read"] += 1
        else:
            export["unread_senders"].append(sender)
            export["unread"] += 1

    export["total"] = len(export["all"])

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)

    return jsonify(export)


if __name__ == "__main__":
    app.run(debug=False, port=5000)
