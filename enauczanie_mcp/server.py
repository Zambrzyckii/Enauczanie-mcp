import time
from functools import lru_cache, wraps
from pathlib import Path
from urllib.parse import unquote

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .assignments import assignment_deadline, summarize_assignment, summarize_status
from .config import Settings, load_settings
from .formatting import (
    build_course_tree,
    compact,
    cut,
    format_timestamp,
    html_to_text,
    safe_filename,
    slugify,
    strip_multilang,
    summarize_files,
)
from .grades import summarize_course_grade, summarize_grade_item
from .h5p import flatten_content, read_package
from .moodle_client import MoodleApiError, MoodleClient
from .quizzes import quiz_deadline

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


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _find(items: list[dict], key: str, value) -> dict:
    return next((item for item in items if item.get(key) == value), {})


def _full_text(html: str) -> str:
    return html_to_text(html, None, links=True, keep_breaks=True)


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


@server.tool()
@_moodle_tool
def list_deadlines(days_ahead: int = 14, days_back: int = 1) -> dict:
    """List assignment due dates and quiz closing times across all courses, from days_back days ago to days_ahead
    days ahead, soonest first."""
    now = time.time()
    start, end = now - _clamp(days_back, 0, 365) * 86400, now + _clamp(days_ahead, 0, 365) * 86400
    assignments = _client().call("mod_assign_get_assignments")
    quizzes = _client().call("mod_quiz_get_quizzes_by_courses")["quizzes"]
    course_names = {c["id"]: c["shortname"] for c in assignments["courses"]}
    deadlines = [assignment_deadline(a, c) for c in assignments["courses"] for a in c["assignments"]]
    deadlines += [quiz_deadline(q, course_names) for q in quizzes]
    return {"deadlines": [d for ts, d in sorted(deadlines, key=lambda x: x[0]) if ts and start <= ts <= end]}


@server.tool()
@_moodle_tool
def get_grades(course_id: int | None = None) -> dict:
    """Return the user's grades: without course_id the final grade of every course, with a course_id (from
    list_courses) every grade item of that course with weight, range, feedback and grading time."""
    if course_id is None:
        overview = _client().call("gradereport_overview_get_course_grades", userid=_userid())["grades"]
        courses = _client().call("core_enrol_get_users_courses", userid=_userid())
        names = {c["id"]: c["shortname"] for c in courses}
        return {"courses": [summarize_course_grade(g, names) for g in overview]}
    report = _client().call("gradereport_user_get_grade_items", courseid=course_id, userid=_userid())
    return {"items": [summarize_grade_item(i) for g in report["usergrades"] for i in g["gradeitems"]]}


@server.tool()
@_moodle_tool
def get_module_text(course_id: int, module_id: int) -> dict:
    """Return the full text of one module (module_id from get_course_contents): the whole description, the body of
    a page or the instructions of an assignment, with link targets and the module's files."""
    sections = _client().call(
        "core_course_get_contents", courseid=course_id, **{"options[0][name]": "cmid", "options[0][value]": module_id}
    )
    module = _find([m for s in sections for m in s.get("modules", [])], "id", module_id)
    if not module:
        raise ToolError(f"No module {module_id} in course {course_id}")
    contents = module.get("contents") or []
    text = _full_text(module.get("description", ""))
    files = [c for c in contents if c.get("type") == "file"]
    if module["modname"] == "page":
        pages = _client().call("mod_page_get_pages_by_courses", **{"courseids[0]": course_id})["pages"]
        page = _find(pages, "coursemodule", module_id)
        text = "\n\n".join(part for part in (text, _full_text(page.get("content", ""))) if part)
        files = page.get("contentfiles", [])
    elif module["modname"] == "assign" and not text:
        payload = _client().call("mod_assign_get_assignments", **{"courseids[0]": course_id})
        assignment = _find([a for c in payload["courses"] for a in c["assignments"]], "cmid", module_id)
        text = _full_text(assignment.get("intro", ""))
        files += assignment.get("introattachments", []) + assignment.get("introfiles", [])
    text, truncated = cut(text, MAX_TEXT_CHARS)
    return compact(
        {
            "module_id": module_id,
            "type": module["modname"],
            "name": strip_multilang(module.get("name", "")),
            "text": text,
            "url": next((c["fileurl"] for c in contents if c.get("type") == "url"), None),
            "files": summarize_files(files),
            "truncated": truncated or None,
        }
    )


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
