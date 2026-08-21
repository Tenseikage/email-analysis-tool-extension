import json
import os
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


if getattr(sys, "frozen", False):
    PROJECT_DIR = Path(sys.executable).resolve().parent.parent
else:
    PROJECT_DIR = Path(__file__).resolve().parent
SERVER_FILE = PROJECT_DIR / "server.py"
SERVER_URL = "http://localhost:5000/"
APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", PROJECT_DIR)) / "EmailAnalysisTool"
CONFIG_FILE = APP_DATA_DIR / "native_host_config.json"
LOG_FILE = APP_DATA_DIR / "native_host.log"


def log(message):
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def send_message(message):
    encoded = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def server_is_running():
    try:
        with urllib.request.urlopen(SERVER_URL, timeout=0.5):
            return True
    except Exception:
        return False


def start_server():
    if server_is_running():
        return

    python_command = os.environ.get("EMAIL_ANALYSIS_PYTHON")
    if not python_command and CONFIG_FILE.exists():
        with CONFIG_FILE.open("r", encoding="utf-8") as file:
            python_command = json.load(file).get("python")
    if not python_command:
        python_command = "python"

    log(f"Launching {python_command} {SERVER_FILE}")
    server_log = LOG_FILE.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [python_command, str(SERVER_FILE)],
        cwd=PROJECT_DIR,
        stdin=subprocess.DEVNULL,
        stdout=server_log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
    )

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if server_is_running():
            return
        time.sleep(0.2)

    server_log.close()
    log(f"server.py exited or did not answer (pid={process.pid})")
    raise RuntimeError("server.py n’a pas démarré sur http://localhost:5000")


log("Native host started")

while True:
    header = sys.stdin.buffer.read(4)
    if not header:
        break

    message_length = struct.unpack("<I", header)[0]
    message = json.loads(sys.stdin.buffer.read(message_length).decode("utf-8"))

    if message.get("action") == "start-server":
        try:
            start_server()
            send_message({"ok": True})
        except Exception as error:
            send_message({"ok": False, "error": str(error)})
    else:
        send_message({"ok": False, "error": "Action inconnue"})