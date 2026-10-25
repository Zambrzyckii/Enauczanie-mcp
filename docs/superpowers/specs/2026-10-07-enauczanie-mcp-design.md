# enauczanie-mcp – design

Date: 2026-10-07
Status: implemented 2026-10-25

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
├── .venv/                      # python -m venv; mcp==2.3.0; package installed editable
├── pyproject.toml              # makes `python -m enauczanie_mcp` work from any directory
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
    ├── h5p.py                  # read .h5p packages, flatten content.json to text
    └── server.py               # MCPServer instance and the three tools
```

Layering: `moodle_client.py` knows HTTP and Moodle's response shape only.
`server.py` knows MCP only. `formatting.py` is pure functions between them.
`html_to_text` drops `<script>` and `<style>`, joins inline tags without extra
spaces and takes keyword flags: `links` appends absolute link targets, `controls`
marks checked radios and checkboxes as `[x]` (how a quiz review shows the chosen
answer), `keep_breaks` keeps paragraph and list breaks instead of one line.

Dependencies: `mcp` (2.3.0) only. The package is installed into the venv with
`pip install -e .`, because Claude Code starts the server from an arbitrary
working directory and `python -m` finds the package only when it is installed. HTTP via `urllib.request` from the standard
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
sections -> modules -> files. Per module: `id` (course module id), `type` (modname), `name`,
`description` (HTML converted to plain text, truncated to ~200 chars; `get_module_text`
returns the full text), `url` (for `url` modules), `files` (list of `{filename, size, mime,
fileurl, modified}`), `dates` (activity dates keyed by Moodle's `dataid` such as `duedate`
or `allowsubmissionsfromdate`, as local ISO date-times with minutes; they include per-user
overrides), `completion` (`incomplete`, `complete`, `pass` or `fail` when the activity
tracks completion) and, for modules locked by an access restriction, `available: false`
plus `availability` (the condition as text, up to 300 chars). Empty values are omitted.
`subsection` modules are listed flat inside their parent section. Modules hidden by the
teacher are not returned by Moodle at all; restricted ones are kept. `page` modules list
no files because their only file is a synthetic `index.html`.

### download_file(fileurl: str, course_id: int)
Accepts only URLs under `<MOODLE_URL>/webservice/pluginfile.php/` with no empty,
`.` or `..` segments (checked before any request), appends `token=` server-side
and streams the file to `downloads/<shortname-slug>/<contextid>/<filepath...>/<filename>`,
mirroring Moodle's pluginfile layout `contextid/component/filearea/itemid/filepath/filename`
so files with equal names in one course do not collide. The file is written to a
`.part` sibling and renamed on success. On any failure the partial file is removed.
Redirects are not followed. Files larger than 200 MB are refused, both by
`Content-Length` and by counting streamed bytes. If the destination already
exists the download is skipped (delete it to re-download). The token never
appears in any tool result.

### get_h5p_content(course_id: int, module_id: int)
Added 2026-10-25. Calls `mod_h5pactivity_get_h5pactivities_by_courses`, finds the
activity whose `coursemodule` equals `module_id` (the `id` shown by
`get_course_contents`), downloads its `.h5p` package through the same path as
`download_file` (cached in `downloads/`), reads `content/content.json` from the
zip and returns `{name, library, items, truncated}`. `items` is a flat list of
`{path, text}` in document order: strings under content keys (`text`, `answer`,
`answers`, `question`, `questions`, `title`, `description`, `tip`, feedback
fields) with HTML removed, plus `correct` booleans. UI strings, accessibility
texts, image and copyright subtrees are skipped. Output is cut at 20 000
characters of text with `truncated: true`. No per-library parsing: the key
names in the path tell the model what each text is.

### list_assignments(course_id: int)
Added 2026-10-25. Calls `mod_assign_get_assignments` for one course and returns
`{assignments: [...]}` with `assignment_id` (the id `get_assignment_status` takes),
`module_id`, `name`, `intro` (full text with link targets and paragraph breaks, cut at
2 000 chars with `intro_truncated: true`), `instructions` (the separate activity text,
when the teacher used it), `files` (intro attachments), `opens`, `due`, `cutoff`,
`grading_due` (local date-times), `max_grade` (when points are used) and
`submission_types` (enabled submission plugins such as `file` or `onlinetext`). Moodle
omits `intro` until the assignment opens unless "always show description" is set. An
unknown course surfaces Moodle's warning as the error. Empty values are omitted.

### get_assignment_status(assignment_id: int)
Added 2026-10-25. Calls `mod_assign_get_submission_status` for the current user and
returns `{status (new, draft, submitted, reopened), submitted, locked, can_submit,
extension_due, grading_status, my_text, my_files, grade, graded, feedback,
feedback_files}` with empty values omitted. `my_text` is the online text submission,
`my_files` the submitted files, `feedback` the teacher's comment, `feedback_files` the
files of the feedback plugins. Of the `editpdf` plugin only the `download` area is
listed, because it holds the annotated PDF the student sees; its other areas
(`combined`, `pages`, `stamps` and temporary ones) are internal.

## Error handling

Moodle answers HTTP 200 with `{"exception": ..., "errorcode": ..., "message": ...}`
on errors. `MoodleClient.call` raises `MoodleApiError(errorcode, message)`.
Tools catch it and return a one-line message. Hints are appended for `invalidtoken`
("run get_token.py to obtain a new token"), `nopermissions`, `accessexception` and
`noreview`. Tools added on 2026-10-25 are wrapped by `_moodle_tool`, which also maps
`KeyError`, `TypeError` and `ValueError` from an unexpected response shape to a
`ToolError`, because the SDK shows the model only `ToolError` text and hides every other
exception behind "Error executing tool". Network, HTTP-protocol, URL and JSON-decoding errors
(`OSError`, `http.client.HTTPException`, `ValueError`; 30 s per request) are
mapped to `MoodleApiError("network", ...)` with the token redacted from the text.
Missing settings fail fast at server start: `__main__.py` loads them before
`server.run` and exits with the message on stderr.

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
