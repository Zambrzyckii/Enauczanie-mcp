import http.client
import json
import os
import secrets
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REST_PATH = "/webservice/rest/server.php"
UPLOAD_PATH = "/webservice/upload.php"
PLUGINFILE_PATH = "/webservice/pluginfile.php/"
CHUNK_SIZE = 64 * 1024


class MoodleApiError(Exception):
    def __init__(self, errorcode: str, message: str):
        super().__init__(f"{errorcode}: {message}")
        self.errorcode = errorcode
        self.message = message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MoodleApiError("redirect", "Moodle answered with a redirect, which is not followed")


_opener = urllib.request.build_opener(_NoRedirect)


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
            with _opener.open(self.base_url + REST_PATH, data=data, timeout=self.timeout) as resp:
                payload = json.load(resp)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise MoodleApiError("network", self._redact(str(exc))) from None
        if isinstance(payload, dict) and "exception" in payload:
            raise MoodleApiError(payload.get("errorcode", "unknown"), payload.get("message", ""))
        return payload

    def call_many(self, calls: list[tuple[str, dict[str, Any]]], workers: int = 4) -> list[Any]:
        """Run several REST calls concurrently; a failed call yields its MoodleApiError in place of the result."""

        def run(call: tuple[str, dict[str, Any]]) -> Any:
            function, params = call
            try:
                return self.call(function, **params)
            except MoodleApiError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(run, calls))

    def upload_draft(self, paths: list[Path]) -> int:
        """Upload local files into a new private draft area and return its item id (for *_filemanager params)."""
        boundary = secrets.token_hex(16)
        fields = {"token": self._token, "filearea": "draft", "itemid": "0"}
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in fields.items()
        ]
        for index, path in enumerate(paths):
            filename = path.name.replace('"', "")
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="file_{index}"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n".encode() + path.read_bytes() + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            self.base_url + UPLOAD_PATH,
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with _opener.open(request, timeout=self.timeout) as resp:
                payload = json.load(resp)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise MoodleApiError("network", self._redact(str(exc))) from None
        if isinstance(payload, dict):
            message = payload.get("error") or payload.get("message", "")
            raise MoodleApiError(payload.get("errorcode", "upload"), self._redact(str(message)))
        return payload[0]["itemid"]

    def file_segments(self, fileurl: str) -> list[str]:
        """Validate a pluginfile URL of this instance and return its path segments after pluginfile.php."""
        url = urllib.parse.urlsplit(fileurl)
        base = urllib.parse.urlsplit(self.base_url)
        prefix = base.path + PLUGINFILE_PATH
        if (url.scheme, url.netloc) != (base.scheme, base.netloc) or not url.path.startswith(prefix):
            raise MoodleApiError("foreignurl", "file URL is not a pluginfile URL of the configured Moodle instance")
        segments = url.path[len(prefix):].split("/")
        if any(segment in ("", ".", "..") for segment in segments):
            raise MoodleApiError("foreignurl", "file URL contains an invalid path segment")
        return segments

    def download(self, fileurl: str, dest: Path, max_bytes: int) -> int:
        self.file_segments(fileurl)
        separator = "&" if "?" in fileurl else "?"
        request = urllib.request.Request(fileurl + separator + urllib.parse.urlencode({"token": self._token}))
        part = dest.with_name(dest.name + ".part")
        written = 0
        try:
            with _opener.open(request, timeout=self.timeout) as resp:
                if resp.headers.get_content_type() == "application/json":
                    payload = json.load(resp)
                    raise MoodleApiError(payload.get("errorcode", "unknown"), payload.get("message", ""))
                length = resp.headers.get("Content-Length", "")
                if length.isdigit() and int(length) > max_bytes:
                    raise MoodleApiError("toolarge", f"file is {length} bytes, limit is {max_bytes}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(part, "wb") as fh:
                    while chunk := resp.read(CHUNK_SIZE):
                        written += len(chunk)
                        if written > max_bytes:
                            raise MoodleApiError("toolarge", f"file exceeds the limit of {max_bytes} bytes")
                        fh.write(chunk)
            os.replace(part, dest)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise MoodleApiError("network", self._redact(str(exc))) from None
        finally:
            part.unlink(missing_ok=True)
        return written

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***")
