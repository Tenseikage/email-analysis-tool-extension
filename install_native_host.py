import json
import subprocess
import sys
from pathlib import Path
import winreg


HOST_NAME = "com.email_analysis.server"
PROJECT_DIR = Path(__file__).resolve().parent
HOST_EXE = PROJECT_DIR / "dist" / "email-analysis-host.exe"
HOST_MANIFEST = PROJECT_DIR / "native_host_manifest.json"
APP_DATA_DIR = Path.home() / "AppData" / "Local" / "EmailAnalysisTool"
HOST_CONFIG = APP_DATA_DIR / "native_host_config.json"


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python install_native_host.py EXTENSION_ID")

    extension_id = sys.argv[1]
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--onefile", "--name", "email-analysis-host", "native_host.py"],
        cwd=PROJECT_DIR,
        check=True,
    )

    HOST_MANIFEST.write_text(
        json.dumps(
            {
                "name": HOST_NAME,
                "description": "Starts the email analysis Flask server",
                "path": str(HOST_EXE),
                "type": "stdio",
                "allowed_origins": [f"chrome-extension://{extension_id}/"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    python_executable = Path(sys.executable)
    pythonw_executable = python_executable.with_name("pythonw.exe")
    if pythonw_executable.exists():
        python_executable = pythonw_executable

    HOST_CONFIG.write_text(
        json.dumps({"python": str(python_executable)}, indent=2),
        encoding="utf-8",
    )

    registry_path = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(HOST_MANIFEST))

    print(f"Native host installed for extension {extension_id}")
    print(f"Manifest: {HOST_MANIFEST}")


if __name__ == "__main__":
    main()