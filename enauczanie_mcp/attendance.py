"""Pages of the attendance plugin, which the Moodle app gets from tool_mobile_get_content as ready HTML."""
import re

from .formatting import html_to_text, strip_multilang

# The app sends its version code with every page request and the plugin picks the template by it; 53000 is app 5.3.0.
APP_VERSION_CODE = 53000

# A session row holds its time, group and recorded status, plus the session id while the user can still mark it.
_SESSION = re.compile(r"<h2>(.*?)</h2>\s*<h3>(.*?)</h3>\s*<h3>(.*?)</h3>(?:\s*<ion-button[^>]*?sessid: (\d+))?", re.S)
_MESSAGE = re.compile(r"<span class=\"messages\">\s*<ion-item[^>]*>\s*<ion-label>\{\{ 'plugin\.mod_attendance\.(\w*)'")
_STATUS = re.compile(r'<ion-radio value="(\d+)">(.*?)</ion-radio>', re.S)

MESSAGES = {
    "": "Moodle shows a message without its text, in this plugin a computer outside the allowed networks or a device "
    "already used to mark another student",
    "closed": "the session is not open for self-marking now",
    "subnetwrong": "attendance is taken only from allowed networks, usually the campus, and this computer is not in one",
    "preventsharederror": "self-marking is blocked because this device already marked another student",
    "attendance_no_status": "no status is available any more, it may be too late to mark",
    "incorrectpasswordshort": "incorrect password, attendance not recorded",
    "invalidstatus": "the chosen status is not available now",
}


def _text(html: str) -> str:
    return strip_multilang(html_to_text(html, None))


def parse_sessions(page: str) -> list[dict]:
    return [
        {"session_id": int(sessid) if sessid else None, "time": _text(time), "group": _text(group), "status": _text(status)}
        for time, group, status, sessid in _SESSION.findall(page)
    ]


def parse_messages(page: str) -> list[str]:
    # The plugin stores the network and shared-device messages under fixed numbers, which renders them with an empty key.
    return [MESSAGES.get(key, key) for key in _MESSAGE.findall(page)]


def parse_statuses(form: str) -> dict[int, str]:
    return {int(stid): _text(description) for stid, description in _STATUS.findall(form)}


def asks_password(form: str) -> bool:
    return 'name="studentpass"' in form


def can_submit(form: str) -> bool:
    return 'method="mobile_view_activity"' in form
