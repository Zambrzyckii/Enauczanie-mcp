# enauczanie-mcp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local stdio MCP server that lets Claude Code list eNauczanie PG courses, browse their contents and download course files with the user's Moodle web service token.

**Architecture:** Three layers in one Python package: `moodle_client.py` (HTTP + Moodle REST error shape), `formatting.py` (pure functions turning Moodle JSON into compact dicts), `server.py` (an `MCPServer` with three tools). Settings come from `.env`; the token never leaves the server process.

**Tech Stack:** Python 3.14 (system), `python -m venv`, `mcp==2.3.0` (`MCPServer` API), `urllib.request` from the standard library.

**Spec:** `docs/superpowers/specs/2026-10-07-enauczanie-mcp-design.md`

## Global Constraints

- Only dependency: `mcp==2.3.0`. No `httpx`, no `python-dotenv`, no `requests`.
- Python >= 3.10 (SDK floor); we run on 3.14.7.
- `MOODLE_URL` has no trailing slash; default instance `https://enauczanie.pg.edu.pl/2025`.
- The token appears in no tool result, no log line and no exception message.
- Code, comments and commit messages in English. No tests in this iteration (owner's rule); each task ends with a manual check.
- The owner runs every git command; the plan only proposes them.
- Code is written in small batches and each design decision is explained before it is written.

## Review Focus

1. `fileurl` pointing at a foreign host (phishing-style link in a course): `download_file` must refuse before any request is made. Owned by Task 3 (`MoodleClient.download`).
2. Moodle returning a JSON error body with HTTP 200 on `pluginfile.php` (expired token): the server must not save that JSON as if it were the PDF. Owned by Task 3.
3. A `filename` such as `../../.bashrc` or one containing `/`: the saved path must stay inside the course download directory. Owned by Task 4 (`safe_filename`).
4. `urllib.error.HTTPError` whose text includes the request URL with `token=`: messages must be redacted before reaching the model. Owned by Task 3 (`_redact`).
5. A course whose `fullname` mixes plain text with `{mlang xx}` blocks, or has only an `en` block: the Polish block wins, otherwise the first block, never raw markers. Owned by Task 4 (`strip_multilang`).

---

### Task 1: Environment and package skeleton

**Files:**
- Create: `.gitignore`, `enauczanie_mcp/__init__.py`, `.venv/` (generated)

**Interfaces:**
- Produces: importable package `enauczanie_mcp`; interpreter `/home/bob/enauczanie-mcp/.venv/bin/python` with `mcp` installed.

- [ ] **Step 1: Create the virtual environment and install the SDK**

```bash
cd /home/bob/enauczanie-mcp && python3 -m venv .venv && .venv/bin/pip install "mcp==2.3.0"
```

- [ ] **Step 2: Verify the import and the API names the plan relies on**

```bash
.venv/bin/python -c "from mcp.server import MCPServer; from mcp.server.mcpserver.exceptions import ToolError; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Write `.gitignore`**

```gitignore
.env
.venv/
downloads/
__pycache__/
.passport
token_result.txt
```

- [ ] **Step 4: Create the empty package**

```bash
mkdir -p enauczanie_mcp && : > enauczanie_mcp/__init__.py
```

- [ ] **Step 5: Owner commits**

```bash
git add .gitignore enauczanie_mcp/__init__.py get_token.py moodle-token-handler.desktop docs
git commit -m "chore: project skeleton, token helper and design docs"
```
Before committing confirm `git status` does **not** list `.env`.

---

### Task 2: Settings (`config.py`)

**Files:**
- Create: `enauczanie_mcp/config.py`

**Interfaces:**
- Produces: `Settings(moodle_url: str, moodle_token: str, download_dir: Path)` (frozen dataclass); `load_settings(env_file: Path = PROJECT_DIR / ".env") -> Settings`; constant `PROJECT_DIR: Path`.

- [ ] **Step 1: Write `config.py`**

```python
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    moodle_url: str
    moodle_token: str
    download_dir: Path


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_settings(env_file: Path = PROJECT_DIR / ".env") -> Settings:
    values = {**_read_env_file(env_file), **os.environ}
    missing = [key for key in ("MOODLE_URL", "MOODLE_TOKEN") if not values.get(key)]
    if missing:
        raise RuntimeError(f"Missing settings: {', '.join(missing)}. Set them in {env_file} or the environment.")
    return Settings(
        moodle_url=values["MOODLE_URL"].rstrip("/"),
        moodle_token=values["MOODLE_TOKEN"],
        download_dir=Path(values.get("ENAUCZANIE_DOWNLOAD_DIR", PROJECT_DIR / "downloads")),
    )
```

Design note: process environment overrides `.env` so `claude mcp add -e MOODLE_URL=...` can switch the instance without editing files.

- [ ] **Step 2: Manual check (prints no secret)**

```bash
.venv/bin/python -c "from enauczanie_mcp.config import load_settings as l; s=l(); print(s.moodle_url, s.download_dir, len(s.moodle_token)>10)"
```
Expected: `https://enauczanie.pg.edu.pl/2025 /home/bob/enauczanie-mcp/downloads True`

- [ ] **Step 3: Owner commits**

```bash
git add enauczanie_mcp/config.py && git commit -m "feat: load settings from .env or environment"
```

---

### Task 3: Moodle REST client (`moodle_client.py`)

**Files:**
- Create: `enauczanie_mcp/moodle_client.py`

**Interfaces:**
- Consumes: nothing from the package (receives `base_url`, `token` as plain strings).
- Produces: `class MoodleApiError(Exception)` with attributes `errorcode: str`, `message: str`; `class MoodleClient(base_url: str, token: str, timeout: float = 30.0)` with `call(function: str, **params) -> Any` and `download(fileurl: str, dest: Path, max_bytes: int) -> int` (bytes written).

- [ ] **Step 1: Write `moodle_client.py`**

```python
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
```

Design notes: `from None` drops the original traceback whose repr could contain the URL with the token. The JSON content-type check covers Review Focus 2. The host check covers Review Focus 1.

- [ ] **Step 2: Manual check against the live instance**

```bash
.venv/bin/python -c "
from enauczanie_mcp.config import load_settings
from enauczanie_mcp.moodle_client import MoodleClient, MoodleApiError
s = load_settings(); c = MoodleClient(s.moodle_url, s.moodle_token)
print(c.call('core_webservice_get_site_info')['release'])
try: c.download('https://example.com/x.pdf', s.download_dir / 'x.pdf', 10)
except MoodleApiError as e: print('refused:', e.errorcode)
try: MoodleClient(s.moodle_url, 'badtoken').call('core_webservice_get_site_info')
except MoodleApiError as e: print('bad token ->', e.errorcode)
"
```
Expected: `5.1.7 (Build: 20260914)`, `refused: foreignurl`, `bad token -> invalidtoken`

- [ ] **Step 3: Owner commits**

```bash
git add enauczanie_mcp/moodle_client.py && git commit -m "feat: Moodle REST client with error mapping and token-safe downloads"
```

---

### Task 4: Formatting helpers (`formatting.py`)

**Files:**
- Create: `enauczanie_mcp/formatting.py`

**Interfaces:**
- Produces: `strip_multilang(text: str, lang: str = "pl") -> str`; `html_to_text(html: str, limit: int = 200) -> str`; `slugify(text: str) -> str`; `safe_filename(name: str) -> str`; `format_timestamp(ts: int | None) -> str | None`; `summarize_module(module: dict) -> dict` (**Learn by Doing: owner writes this one**); `build_course_tree(sections: list[dict]) -> list[dict]`.

- [ ] **Step 1: Write the pure helpers**

```python
import re
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import PurePosixPath

_MLANG = re.compile(r"\{mlang\s+([\w-]+)\}(.*?)\{mlang\}", re.S)
_WS = re.compile(r"\s+")


def strip_multilang(text: str, lang: str = "pl") -> str:
    blocks = _MLANG.findall(text)
    if not blocks:
        return text.strip()
    keep = lang if any(code == lang for code, _ in blocks) else blocks[0][0]
    return _WS.sub(" ", _MLANG.sub(lambda m: m.group(2) if m.group(1) == keep else "", text)).strip()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str, limit: int = 200) -> str:
    extractor = _TextExtractor()
    extractor.feed(html or "")
    text = _WS.sub(" ", unescape(" ".join(extractor.parts))).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "course"


def safe_filename(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/")).name
    return base if base not in ("", ".", "..") else "file"


def format_timestamp(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
```

- [ ] **Step 2: Add `summarize_module` with a `TODO(human)` body and `build_course_tree` around it**

```python
def summarize_module(module: dict) -> dict:
    # TODO(human): decide what Claude needs to see about one module.
    raise NotImplementedError


def build_course_tree(sections: list[dict]) -> list[dict]:
    tree = []
    for section in sections:
        modules = [m for m in section.get("modules", []) if m.get("uservisible", True)]
        tree.append(
            {
                "section": strip_multilang(section.get("name", "")),
                "summary": html_to_text(section.get("summary", "")),
                "modules": [summarize_module(m) for m in modules],
            }
        )
    return tree
```

The owner then implements `summarize_module` (expected shape from the spec: `type`, `name`, `description`, `url` for `url` modules, `files` as `{filename, size, mime, fileurl, modified}`; `label` keeps only its text). Nesting of `subsection` modules is decided there too: Moodle 5 reports a subsection as a module in the parent section and as a separate section with `component == "mod_subsection"`; the simplest correct rendering is to keep both entries and mark the module entry with `type: "subsection"`.

- [ ] **Step 3: Manual check on a real course**

```bash
.venv/bin/python -c "
import json
from enauczanie_mcp.config import load_settings
from enauczanie_mcp.moodle_client import MoodleClient
from enauczanie_mcp.formatting import *
print(strip_multilang('{mlang pl}Systemy{mlang}{mlang en}Systems{mlang}'), '|', strip_multilang('{mlang en}Only EN{mlang}'))
print(safe_filename('../../.bashrc'), slugify('M. Adamczyk 2'), format_timestamp(1801868400))
s = load_settings(); c = MoodleClient(s.moodle_url, s.moodle_token)
tree = build_course_tree(c.call('core_course_get_contents', courseid=175))
print(json.dumps(tree[:2], ensure_ascii=False, indent=1)[:1200])
"
```
Expected: `Systemy | Only EN`, `.bashrc m-adamczyk-2 2027-02-09`, then two compact sections without HTML tags or `{mlang}`.

- [ ] **Step 4: Owner commits**

```bash
git add enauczanie_mcp/formatting.py && git commit -m "feat: formatting helpers and compact course tree"
```

---

### Task 5: MCP server and tools (`server.py`, `__main__.py`)

**Files:**
- Create: `enauczanie_mcp/server.py`, `enauczanie_mcp/__main__.py`

**Interfaces:**
- Consumes: `load_settings`, `Settings`; `MoodleClient.call/download`, `MoodleApiError`; `strip_multilang`, `format_timestamp`, `slugify`, `safe_filename`, `build_course_tree`.
- Produces: module-level `server: MCPServer` with tools `list_courses`, `get_course_contents`, `download_file`; `python -m enauczanie_mcp` runs it on stdio.

- [ ] **Step 1: Write `server.py`**

```python
from functools import lru_cache
from urllib.parse import unquote

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .config import Settings, load_settings
from .formatting import build_course_tree, format_timestamp, safe_filename, slugify, strip_multilang
from .moodle_client import MoodleApiError, MoodleClient

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

server = MCPServer(
    "enauczanie",
    instructions=(
        "Read-only access to the user's courses on eNauczanie PG (Moodle). "
        "Call list_courses first, then get_course_contents, then download_file for files to read."
    ),
)


@lru_cache(maxsize=1)
def _settings() -> Settings:
    return load_settings()


@lru_cache(maxsize=1)
def _client() -> MoodleClient:
    settings = _settings()
    return MoodleClient(settings.moodle_url, settings.moodle_token)


def _tool_error(exc: MoodleApiError) -> ToolError:
    hint = " Run get_token.py to obtain a new token." if exc.errorcode == "invalidtoken" else ""
    return ToolError(f"Moodle error {exc.errorcode}: {exc.message}{hint}")


@server.tool()
def list_courses(include_hidden: bool = False) -> list[dict]:
    """List the user's enrolled courses on eNauczanie. Returns id, shortname, fullname and end_date."""
    try:
        userid = _client().call("core_webservice_get_site_info")["userid"]
        courses = _client().call("core_enrol_get_users_courses", userid=userid)
    except MoodleApiError as exc:
        raise _tool_error(exc) from None
    return [
        {
            "id": c["id"],
            "shortname": c["shortname"],
            "fullname": strip_multilang(c["fullname"]),
            "end_date": format_timestamp(c.get("enddate")),
        }
        for c in courses
        if include_hidden or not c.get("hidden")
    ]


@server.tool()
def get_course_contents(course_id: int) -> list[dict]:
    """Return the sections, modules and files of one course as a compact tree. Use fileurl values with download_file."""
    try:
        sections = _client().call("core_course_get_contents", courseid=course_id)
    except MoodleApiError as exc:
        raise _tool_error(exc) from None
    return build_course_tree(sections)


@server.tool()
def download_file(fileurl: str, course_id: int) -> dict:
    """Download a course file (fileurl from get_course_contents) into the local downloads directory and return its path."""
    try:
        course = _client().call("core_course_get_courses_by_field", field="id", value=course_id)["courses"]
        shortname = course[0]["shortname"] if course else str(course_id)
        dest = _settings().download_dir / slugify(shortname) / safe_filename(unquote(fileurl.rsplit("/", 1)[-1].split("?")[0]))
        if dest.exists():
            return {"path": str(dest), "size": dest.stat().st_size, "skipped": True}
        size = _client().download(fileurl, dest, MAX_DOWNLOAD_BYTES)
    except MoodleApiError as exc:
        raise _tool_error(exc) from None
    return {"path": str(dest), "size": size, "skipped": False}
```

Design notes: `lru_cache` gives lazy singletons so importing the module (for checks) never needs a token; the first tool call does. Filenames in `fileurl` are percent-encoded by Moodle, hence `unquote` on the last path segment before `safe_filename`. "Same name and size" skipping from the spec is simplified to "same name exists" because the size is unknown before the request; the spec is updated accordingly in Task 6.

- [ ] **Step 2: Write `__main__.py`**

```python
from .server import server

server.run("stdio")
```

- [ ] **Step 3: Manual check without MCP transport (direct function calls)**

```bash
.venv/bin/python -c "
from enauczanie_mcp.server import list_courses, get_course_contents, download_file
cs = list_courses(); print(len(cs), cs[0])
tree = get_course_contents(cs[0]['id']); print(len(tree), 'sections')
files = [f for s in tree for m in s['modules'] for f in m.get('files', [])]
print(download_file(files[0]['fileurl'], cs[0]['id']) if files else 'no files in first course')
"
```
Expected: course count and first course dict, a section count, then `{'path': '/home/bob/enauczanie-mcp/downloads/<slug>/<file>', 'size': N, 'skipped': False}`.

- [ ] **Step 4: Protocol smoke test over stdio**

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' '{"jsonrpc":"2.0","method":"notifications/initialized"}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | .venv/bin/python -m enauczanie_mcp | head -c 1500
```
Expected: two JSON-RPC responses, the second listing `list_courses`, `get_course_contents`, `download_file`.

- [ ] **Step 5: Owner commits**

```bash
git add enauczanie_mcp/server.py enauczanie_mcp/__main__.py && git commit -m "feat: MCP server with course listing, contents and file download tools"
```

---

### Task 6: Registration in Claude Code and end-to-end check

**Files:**
- Modify: `docs/superpowers/specs/2026-10-07-enauczanie-mcp-design.md` (download_file skip rule)
- Modify: `~/.claude.json` via the CLI (user scope, done by the owner)

- [ ] **Step 1: Owner registers the server**

```bash
claude mcp add enauczanie -s user -- /home/bob/enauczanie-mcp/.venv/bin/python -m enauczanie_mcp
claude mcp list
```
Expected: `enauczanie` listed as connected.

- [ ] **Step 2: End-to-end check in a new Claude Code session**

Ask: "Jakie mam kursy na eNauczanie?", then "Pokaż materiały z kursu Metody Numeryczne", then "Pobierz pierwszy PDF i streść go". Expected: answers come from the tools; the PDF lands under `downloads/` and is read with the Read tool.

- [ ] **Step 3: Align the spec with the implemented skip rule**

Change "If a file of the same name and size already exists the download is skipped." to "If a file of the same name already exists the download is skipped (delete it to re-download)."

- [ ] **Step 4: Owner commits**

```bash
git add docs && git commit -m "docs: align download skip rule with implementation"
```
