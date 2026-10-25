"""Throwaway helper: obtain a Moodle web service token via the mobile-app launch flow.

Run without arguments to print the launch URL (and remember the passport).
Registered as the x-scheme-handler for moodlemobile://, it is then invoked by the
browser with the redirect URL, verifies the passport and stores the token in .env.
"""
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_URL = os.environ.get("MOODLE_URL", "https://enauczanie.pg.edu.pl/2025")
SERVICE = "moodle_mobile_app"
PASSPORT_FILE = os.path.join(HERE, ".passport")
RESULT_FILE = os.path.join(HERE, "token_result.txt")
REQUIRED_FUNCTIONS = ("core_course_get_contents", "core_enrol_get_users_courses")


def ws_call(token: str, function: str, **params) -> dict:
    data = urllib.parse.urlencode(
        {"wstoken": token, "wsfunction": function, "moodlewsrestformat": "json", **params}
    ).encode()
    with urllib.request.urlopen(f"{BASE_URL}/webservice/rest/server.php", data=data, timeout=30) as resp:
        return json.load(resp)


def print_launch_url() -> None:
    passport = str(secrets.randbelow(10**9))
    with open(PASSPORT_FILE, "w") as f:
        f.write(passport)
    query = urllib.parse.urlencode({"service": SERVICE, "passport": passport, "urlscheme": "moodlemobile"})
    print(f"LAUNCH_URL: {BASE_URL}/admin/tool/mobile/launch.php?{query}")


def handle_token(url: str) -> str:
    encoded = urllib.parse.unquote(url.split("token=", 1)[1])
    parts = base64.b64decode(encoded).decode().split(":::")
    if len(parts) < 2:
        return "Unexpected token format (expected siteid:::token[:::privatetoken])."
    with open(PASSPORT_FILE) as f:
        passport = f.read().strip()
    if parts[0] != hashlib.md5((BASE_URL + passport).encode()).hexdigest():
        return "Passport mismatch - token does not belong to this launch request. Ignored."
    token = parts[1]

    env_path = os.path.join(HERE, ".env")
    with open(env_path, "w") as f:
        f.write(f"MOODLE_URL={BASE_URL}\nMOODLE_TOKEN={token}\n")
    os.chmod(env_path, 0o600)

    info = ws_call(token, "core_webservice_get_site_info")
    if "exception" in info:
        return f"Token saved but site info failed: {info.get('errorcode')} {info.get('message')}"
    functions = sorted(f["name"] for f in info.get("functions", []))
    with open(os.path.join(HERE, "functions.json"), "w") as f:
        json.dump(functions, f, indent=2)
    missing = [fn for fn in REQUIRED_FUNCTIONS if fn not in functions]
    return (
        f"OK. user={info.get('fullname')} release={info.get('release')} "
        f"functions={len(functions)} missing_required={missing or 'none'}"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_launch_url()
        sys.exit(0)
    try:
        result = handle_token(sys.argv[1])
    except Exception as exc:  # the browser swallows stdout, so persist the outcome
        result = f"ERROR: {exc!r}"
    with open(RESULT_FILE, "w") as f:
        f.write(result + "\n")
    subprocess.run(["notify-send", "eNauczanie token", result], check=False)
