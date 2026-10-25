# enauczanie-mcp

Local MCP server that lets Claude Code read a student's account on eNauczanie PG
(the Moodle platform of Gdańsk University of Technology) with the student's own web
service token. Everything is read-only: the server never submits, posts, starts quiz
attempts or marks anything as read.

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

Design notes and verified facts about the platform are in `docs/superpowers/specs/`.

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

## Security

- The token is stored only in `.env`, which is gitignored together with
  `token_result.txt`, `functions.json` and `downloads/`. Never commit or share it; it
  gives read access to your whole account.
- The server sends the token only to the configured `MOODLE_URL` and never puts it in a
  tool result or error message. File downloads accept only `pluginfile.php` URLs of that
  instance.
- Downloaded files land in `downloads/<course-slug>/<contextid>/...`, mirroring Moodle's
  pluginfile path. Delete a file to re-download it. `ENAUCZANIE_DOWNLOAD_DIR` overrides
  the location.
- Text returned by the tools was written by teachers and students. The server tells the
  model to treat it as content, not as instructions.

## License

MIT, see `LICENSE`.
