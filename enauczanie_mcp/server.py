from functools import lru_cache, wraps
from pathlib import Path
from urllib.parse import unquote

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .assignments import summarize_assignment, summarize_status
from .config import Settings, load_settings
from .formatting import build_course_tree, format_timestamp, safe_filename, slugify, strip_multilang
from .h5p import flatten_content, read_package
from .moodle_client import MoodleApiError, MoodleClient

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_H5P_CHARS = 20_000
MAX_TEXT_CHARS = 20_000
_HINTS = {
    "invalidtoken": " Run get_token.py to obtain a new token.",
    "nopermissions": " The account has no access to this item.",
    "accessexception": " The account has no access to this item.",
    "noreview": " The teacher disabled the review of this quiz.",
}

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
    return ToolError(f"Moodle error {exc.errorcode}: {exc.message}{_HINTS.get(exc.errorcode, '')}")


@lru_cache(maxsize=1)
def _userid() -> int:
    return _client().call("core_webservice_get_site_info")["userid"]


def _moodle_tool(func):
    """Map Moodle errors and unexpected response shapes to ToolError, the only exception the SDK shows the model."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except MoodleApiError as exc:
            raise _tool_error(exc) from None
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"Unexpected Moodle response in {func.__name__}: {exc!r}") from None

    return wrapper


@server.tool()
def list_courses(include_hidden: bool = False) -> list[dict]:
    """List the user's enrolled courses on eNauczanie. Returns id, shortname, fullname and end_date."""
    try:
        courses = _client().call("core_enrol_get_users_courses", userid=_userid())
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
    """Return the sections, modules and files of one course as a compact tree with module dates, completion and
    availability. Descriptions are cut at 200 chars; get_module_text gives the full text. Use fileurl with download_file."""
    try:
        sections = _client().call("core_course_get_contents", courseid=course_id)
    except MoodleApiError as exc:
        raise _tool_error(exc) from None
    return build_course_tree(sections)


@server.tool()
def download_file(fileurl: str, course_id: int) -> dict:
    """Download a course file (fileurl from get_course_contents) into the local downloads directory and return its path."""
    try:
        return _download(fileurl, course_id)
    except MoodleApiError as exc:
        raise _tool_error(exc) from None
    except OSError as exc:
        raise ToolError(f"File system error: {exc}") from None


@server.tool()
def get_h5p_content(course_id: int, module_id: int) -> dict:
    """Return the text of an H5P exercise (module of type h5pactivity, id from get_course_contents): questions, answers, flashcards."""
    try:
        activities = _client().call("mod_h5pactivity_get_h5pactivities_by_courses", **{"courseids[0]": course_id})
        activity = next((a for a in activities["h5pactivities"] if a["coursemodule"] == module_id), None)
        if activity is None:
            raise ToolError(f"No H5P activity with module id {module_id} in course {course_id}")
        package = _download(activity["package"][0]["fileurl"], course_id)
        meta, content = read_package(Path(package["path"]))
    except MoodleApiError as exc:
        raise _tool_error(exc) from None
    except (OSError, KeyError, ValueError) as exc:
        raise ToolError(f"Cannot read H5P package: {exc}") from None
    items, used = [], 0
    for item in flatten_content(content):
        used += len(item["text"])
        if used > MAX_H5P_CHARS:
            break
        items.append(item)
    return {
        "name": strip_multilang(activity["name"]),
        "library": meta.get("mainLibrary"),
        "items": items,
        "truncated": used > MAX_H5P_CHARS,
    }


@server.tool()
@_moodle_tool
def list_assignments(course_id: int) -> dict:
    """List the assignments of one course (id from list_courses) with instructions, deadlines and attachments.
    The assignment_id values feed get_assignment_status."""
    payload = _client().call("mod_assign_get_assignments", **{"courseids[0]": course_id})
    if not payload["courses"] and payload.get("warnings"):
        raise ToolError(payload["warnings"][0].get("message", f"No assignments for course {course_id}"))
    return {"assignments": [summarize_assignment(a) for c in payload["courses"] for a in c["assignments"]]}


@server.tool()
@_moodle_tool
def get_assignment_status(assignment_id: int) -> dict:
    """Return the user's submission state, submitted files, grade and teacher feedback for one assignment
    (assignment_id from list_assignments, not the module id)."""
    return summarize_status(_client().call("mod_assign_get_submission_status", assignid=assignment_id))


def _download(fileurl: str, course_id: int) -> dict:
    segments = _client().file_segments(fileurl)
    course = _client().call("core_course_get_courses_by_field", field="id", value=course_id)["courses"]
    shortname = course[0]["shortname"] if course else str(course_id)
    # pluginfile layout: contextid/component/filearea/itemid/<filepath...>/filename
    relative = [safe_filename(unquote(segment)) for segment in [segments[0], *segments[4:]]]
    dest = _settings().download_dir.joinpath(slugify(shortname), *relative)
    if dest.exists():
        return {"path": str(dest), "size": dest.stat().st_size, "skipped": True}
    size = _client().download(fileurl, dest, MAX_DOWNLOAD_BYTES)
    return {"path": str(dest), "size": size, "skipped": False}
