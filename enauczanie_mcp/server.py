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
        segments = _client().file_segments(fileurl)
        course = _client().call("core_course_get_courses_by_field", field="id", value=course_id)["courses"]
        shortname = course[0]["shortname"] if course else str(course_id)
        # pluginfile layout: contextid/component/filearea/itemid/<filepath...>/filename
        relative = [safe_filename(unquote(segment)) for segment in [segments[0], *segments[4:]]]
        dest = _settings().download_dir.joinpath(slugify(shortname), *relative)
        if dest.exists():
            return {"path": str(dest), "size": dest.stat().st_size, "skipped": True}
        size = _client().download(fileurl, dest, MAX_DOWNLOAD_BYTES)
    except MoodleApiError as exc:
        raise _tool_error(exc) from None
    except OSError as exc:
        raise ToolError(f"File system error: {exc}") from None
    return {"path": str(dest), "size": size, "skipped": False}
