# enauczanie-mcp – design

Date: 2026-10-07
Status: approved in conversation, pending written review

## Goal

Let Claude Code read course materials from eNauczanie PG (a Moodle 5.1 instance)
through a local MCP server, using the user's own Moodle web service token.
First scope: browsing courses, browsing course contents, downloading files so
Claude can read them locally. Assignments, grades, calendar and any write
operations are out of scope for this iteration.

## Verified facts about the target

- Current-year instance: `https://enauczanie.pg.edu.pl/2025` (Moodle 5.1.7).
  A separate instance lives under `/moodle`; tokens are per instance.
- Login is CAS-only (Moja PG). Password-based `login/token.php` does not work.
- Web services and the `moodle_mobile_app` service are enabled; the token is
  obtained with the mobile launch flow (`admin/tool/mobile/launch.php`) and a
  registered `moodlemobile://` scheme handler (`get_token.py`).
- The token has 441 functions, including `core_enrol_get_users_courses`,
  `core_course_get_contents`, `core_course_get_courses_by_field`,
  `mod_resource_get_resources_by_courses`, `mod_folder_get_folders_by_courses`.
- File download works through `webservice/pluginfile.php/...?token=...`
  (the `fileurl` field returned by `core_course_get_contents`).
- Course names may contain `{mlang pl}...{mlang}` multilang markers.
- Moodle 5.x courses contain `subsection` modules (nested sections).

## Architecture

```
~/enauczanie-mcp/
├── .venv/                      # python -m venv; mcp==2.3.0
├── .env                        # MOODLE_URL, MOODLE_TOKEN (mode 600, never committed)
├── .gitignore                  # .env .venv/ downloads/ __pycache__/ .passport token_result.txt
├── get_token.py                # helper: obtain/renew the token (not part of the server)
├── moodle-token-handler.desktop# xdg scheme handler used by get_token.py
├── downloads/                  # files fetched by download_file
├── docs/superpowers/specs/     # this document
└── enauczanie_mcp/
    ├── __init__.py
    ├── __main__.py             # MCPServer.run("stdio")
    ├── config.py               # Settings loaded from env vars or the project .env
    ├── moodle_client.py        # MoodleClient: call(), download(); MoodleApiError
    ├── formatting.py           # strip multilang, HTML -> text, compact course tree
    └── server.py               # MCPServer instance and the three tools
```

Layering: `moodle_client.py` knows HTTP and Moodle's response shape only.
`server.py` knows MCP only. `formatting.py` is pure functions between them.

Dependencies: `mcp` (2.3.0) only. HTTP via `urllib.request` from the standard
library; tools are synchronous functions, which the SDK runs in a worker thread.

Settings resolution order: process environment, then `<project>/.env`.
Required keys: `MOODLE_URL` (no trailing slash), `MOODLE_TOKEN`.
Optional: `ENAUCZANIE_DOWNLOAD_DIR` (default `<project>/downloads`).

## Tools

### list_courses(include_hidden: bool = False)
Calls `core_webservice_get_site_info` (for `userid`) and
`core_enrol_get_users_courses`. Returns a list of
`{id, shortname, fullname, end_date}` where `fullname` has multilang markers
removed and `end_date` is ISO 8601 or null when Moodle returns 0.
Courses with `hidden == true` are omitted unless `include_hidden` is set.

### get_course_contents(course_id: int)
Calls `core_course_get_contents`. Returns a compact tree:
sections -> modules -> files. Per module: `type` (modname), `name`,
`description` (HTML converted to plain text, truncated to ~200 chars),
`url` (for `url` modules), `files` (list of `{filename, size, mime, fileurl,
modified}`). `subsection` modules are rendered as nested sections.
Modules with `uservisible == false` are omitted. `label` modules keep only
their text.

### download_file(fileurl: str, course_id: int)
Validates that `fileurl` starts with `MOODLE_URL`, appends `token=` server-side,
streams the file to `downloads/<shortname-slug>/<filename>` and returns
`{path, size, skipped}`. If a file of the same name and size already exists
the download is skipped. Files larger than 200 MB are refused. The token never
appears in any tool result.

## Error handling

Moodle answers HTTP 200 with `{"exception": ..., "errorcode": ..., "message": ...}`
on errors. `MoodleClient.call` raises `MoodleApiError(errorcode, message)`.
Tools catch it and return a one-line message. For `invalidtoken` the message
adds: "run get_token.py to obtain a new token". Network errors
(`URLError`, timeouts; 30 s per request) are reported the same way.
Missing settings fail fast at server start with a clear message.

## Registration and verification

```
claude mcp add enauczanie -s user -- /home/bob/enauczanie-mcp/.venv/bin/python -m enauczanie_mcp
```

No automated tests in this iteration (user's rule). Manual verification:
start a new Claude Code session, ask for the course list, open one course,
download one PDF and read it.

## Future work (not in this iteration)

- `search_materials(query)` across courses.
- `sync_course(course_id)` mirroring all files of a course.
- Assignments / deadlines (`mod_assign_*`, `core_calendar_*`), grades.
- Yearly instance switch: change `MOODLE_URL` and re-run `get_token.py`.
