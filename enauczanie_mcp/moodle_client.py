import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REST_PATH = "/webservice/rest/server.php"


class MoodleApiError(Exception):
    def __init__(self, errorcode: str, message: str):
        super().__init__(f"{errorcode}: {message}")
        self.errorcode = errorcode
        self.message = message


class MoodleClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout

    def call(self, function: str, **params: Any) -> Any:
        data = urllib.parse.urlencode(
            {"wstoken": self._token, "wsfunction": function, "moodlewsrestformat": "json", **params}
        ).encode()
        try:
            with urllib.request.urlopen(self.base_url + REST_PATH, data=data, timeout=self.timeout) as resp:
                payload = json.load(resp)
        except urllib.error.URLError as exc:
            raise MoodleApiError("network", self._redact(str(exc))) from None
        if isinstance(payload, dict) and "exception" in payload:
            raise MoodleApiError(payload.get("errorcode", "unknown"), payload.get("message", ""))
        return payload

    def download(self, fileurl: str, dest: Path, max_bytes: int) -> int:
        if not fileurl.startswith(self.base_url + "/"):
            raise MoodleApiError("foreignurl", "file URL does not belong to the configured Moodle instance")
        separator = "&" if "?" in fileurl else "?"
        request = urllib.request.Request(fileurl + separator + urllib.parse.urlencode({"token": self._token}))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                if resp.headers.get_content_type() == "application/json":
                    payload = json.load(resp)
                    raise MoodleApiError(payload.get("errorcode", "unknown"), payload.get("message", ""))
                length = resp.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise MoodleApiError("toolarge", f"file is {length} bytes, limit is {max_bytes}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as fh:
                    shutil.copyfileobj(resp, fh)
        except urllib.error.URLError as exc:
            raise MoodleApiError("network", self._redact(str(exc))) from None
        return dest.stat().st_size

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***")
