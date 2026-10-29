# enauczanie-mcp

[![Release](https://img.shields.io/github/v/release/Zambrzyckii/Enauczanie-mcp)](https://github.com/Zambrzyckii/Enauczanie-mcp/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

![Python](https://img.shields.io/badge/Python_3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-000000?style=for-the-badge&logo=modelcontextprotocol&logoColor=white)
![Claude Code](https://img.shields.io/badge/Claude_Code-D97757?style=for-the-badge&logo=claude&logoColor=white)

Local MCP server that lets Claude Code use a student's account on eNauczanie PG
(the Moodle platform of Gdańsk University of Technology) with the student's own web
service token. By default it only reads. It never submits, posts, starts quiz attempts
or marks anything as read. An opt-in [write mode](#write-mode) adds tools that work like
the student's own buttons in Moodle.

Tools:

- Courses: `list_courses`, `get_course_contents` (tree with dates, completion and access
  restrictions), `get_module_text` (full description, page body or assignment
  instructions), `download_file`.
- Homework: `list_deadlines` (all courses), `list_assignments`, `get_assignment_status`
  (submission, grade, teacher feedback).
- Grades: `get_grades` (final grades, or the grade items of one course).
- Forums: `get_forum_discussions` (announcements are forums of type `news`),
  `get_discussion_posts`.
- Exercises: `get_quiz_review` (finished attempts with chosen and correct answers),
  `get_h5p_content` (text of H5P exercises).
- What is new: `get_recent_changes`, `get_notifications`.
- Write mode only: `save_assignment_submission`, `submit_assignment`, `remove_submission`,
  `list_group_choices`, `join_group`, `leave_group`, `list_vote_options`, `submit_vote`,
  `get_survey`, `submit_survey`, `mark_attendance`, `set_activity_done`,
  `mark_notification_read`.

## Requirements

- Python 3.10 or newer, Linux with `xdg-mime` for the automatic token capture (other
  systems work with the manual step below).
- Claude Code with MCP support.
- An eNauczanie account. Login is CAS-only (Moja PG), so the token comes from Moodle's
  mobile-app launch flow instead of a password.

## Install

```bash
git clone https://github.com/Zambrzyckii/enauczanie-mcp.git
cd enauczanie-mcp
python3 -m venv .venv && .venv/bin/pip install -e .
```

On Windows the venv keeps its programs in `.venv\Scripts` instead of `.venv/bin`, so use
`.venv\Scripts\pip` here. When registering the server, replace `"$(pwd)/.venv/bin/python"`
with the full path to `.venv\Scripts\python.exe`.

## Token

One-time: register the handler for `moodlemobile://` links (it writes a `.desktop` entry
pointing at this checkout).

```bash
python3 get_token.py --install-handler
```

Then, whenever the token expires or the academic year changes:

```bash
python3 get_token.py
```

Open the printed URL in your browser, log in, accept the prompt to open the link with
"Moodle Token Handler". The token lands in `.env` (`MOODLE_URL`, `MOODLE_TOKEN`, file
mode 600) and a summary in `token_result.txt`.

Without xdg (macOS, Windows, WSL): open the printed URL, log in, copy the
`moodlemobile://token=...` address the browser tries to open and run
`python3 get_token.py 'moodlemobile://token=...'`.

The instance is per academic year. For a new year set `MOODLE_URL` (for example
`https://enauczanie.pg.edu.pl/2026`) in the environment before running `get_token.py`.

## Register in Claude Code

```bash
claude mcp add enauczanie -s user -- "$(pwd)/.venv/bin/python" -m enauczanie_mcp
```

Start a new Claude Code session and ask, for example, what is due in the next two weeks,
for the grades of a course, for the latest announcements or for a review of a finished
quiz.

## Write mode

Write mode lets Claude act on the account the way the student does with Moodle's buttons.
It is off unless `ENAUCZANIE_ALLOW_WRITE=1` (or `true`) is set in `.env` or in the server's
environment.
`get_token.py` keeps that line when it stores a new token. Start a new Claude Code session
after turning it on or off.

```bash
echo "ENAUCZANIE_ALLOW_WRITE=1" >> .env
```

| Moodle button | Tools |
|---|---|
| Add submission, Save changes | `save_assignment_submission` (files and/or online text) |
| Submit assignment | `submit_assignment` |
| Remove submission | `remove_submission` |
| Group sign-up (`choicegroup`) | `list_group_choices`, `join_group`, `leave_group` |
| Poll (`choice`) | `list_vote_options`, `submit_vote` |
| Survey (`feedback`) | `get_survey`, `submit_survey` |
| Submit attendance (`attendance`) | `mark_attendance` |
| Mark as done | `set_activity_done` |
| Notification read | `mark_notification_read` |

- `save_assignment_submission`, `submit_assignment`, `remove_submission`, `join_group`,
  `leave_group`, `submit_vote`, `submit_survey` and `mark_attendance` change nothing without
  `confirm=true`. The first call only describes what would happen, so Claude can show it to you
  before anything reaches Moodle.
- `set_activity_done` and `mark_notification_read` act at once.
- `join_group` keeps the groups you are already in where the sign-up allows several. Where it
  allows one, joining moves you out of your current group.
- `mark_attendance` records your attendance at the session that is open for self-marking now,
  so use it only when you are at that class. It does not contact Moodle before `confirm=true`,
  because where the teacher set an automatic status without a password, opening the list of
  sessions already records it. Moodle still checks the password the teacher gives in class and
  may take marks only from the campus network. Where it offers several statuses, you choose one.
- Many assignments have no draft stage. There, saving already hands the work in and the
  teacher sees it. The description before saving says which case applies.
- New files replace every file already in the submission. On a team assignment the change
  applies to the shared submission of the whole group.
- Online text is plain text. A blank line starts a new paragraph, other formatting such as
  bold or lists is not kept.
- Quiz and H5P attempts are not available, because they are graded tests. Consent forms are
  not available, because the Moodle mobile app does not support them.
- When the server runs in Docker, `file_paths` are paths inside the container, so mount the
  folder that holds the files.

## Prebuilt image

Each release publishes an image to GHCR and attaches a wheel and an sdist. Both hold only the
server, so the token still comes from `get_token.py` in a clone. Mount the download folder at
the same path as on the host, so the paths that `download_file` returns open outside the
container. The token then also sits in the container's environment, where `docker inspect`
shows it.

```bash
mkdir -p ~/enauczanie-downloads
claude mcp add enauczanie -s user -- docker run -i --rm --env-file "$(pwd)/.env" \
  --user "$(id -u):$(id -g)" \
  -e ENAUCZANIE_DOWNLOAD_DIR="$HOME/enauczanie-downloads" \
  -v "$HOME/enauczanie-downloads:$HOME/enauczanie-downloads" \
  ghcr.io/zambrzyckii/enauczanie-mcp:0.1.0
```

## How it works

Claude Code starts the server as a child process and talks to it over stdio. Each tool makes
one or more calls to Moodle's REST web service (`/webservice/rest/server.php`), turns HTML into
plain text and cuts long texts and lists, so a course tree or a forum thread fits the model's
context. Write tools first upload files through `/webservice/upload.php` into the user's
private draft area, then call the same functions the Moodle mobile app uses for each button.
Attendance is a plugin whose pages the app gets as ready HTML through `tool_mobile_get_content`,
so `mark_attendance` reads the sessions and statuses from that HTML.

| Path | Role |
|---|---|
| `enauczanie_mcp/server.py` | read tools and the instructions sent to the model |
| `enauczanie_mcp/writing.py` | write tools, registered only in write mode |
| `enauczanie_mcp/moodle_client.py` | REST calls, concurrent calls, draft uploads and checked file downloads |
| `enauczanie_mcp/config.py` | reads `MOODLE_URL`, `MOODLE_TOKEN`, `ENAUCZANIE_DOWNLOAD_DIR` and `ENAUCZANIE_ALLOW_WRITE` from the environment, then `.env` |
| `enauczanie_mcp/formatting.py` | HTML to text, plain text to HTML, the course tree and size limits |
| `enauczanie_mcp/assignments.py`, `attendance.py`, `grades.py`, `forums.py`, `quizzes.py`, `surveys.py`, `updates.py` | shape Moodle's answers for each group of tools |
| `enauczanie_mcp/h5p.py` | reads the text out of a downloaded H5P package |
| `get_token.py` | gets the token through the mobile-app login flow |

There are no automated tests.

## Security

- The token is stored only in `.env`, which is gitignored together with
  `token_result.txt`, `functions.json` and `downloads/`. Never commit or share it. It
  gives the same access as the Moodle mobile app, including handing in work, whatever
  mode the server runs in.
- The server sends the token only to the configured `MOODLE_URL` and never puts it in a
  tool result or error message. File downloads accept only `pluginfile.php` URLs of that
  instance.
- Downloaded files land in `downloads/<course-slug>/<contextid>/...`, mirroring Moodle's
  pluginfile path. Delete a file to re-download it. `ENAUCZANIE_DOWNLOAD_DIR` overrides
  the location.
- Text returned by the tools was written by teachers and students. The server tells the
  model to treat it as content, not as instructions, and to use write tools only for what
  the user asked for.

## License

MIT, see `LICENSE`.
