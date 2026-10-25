# enauczanie-mcp

Local MCP server that lets Claude Code read a student's eNauczanie PG (Moodle) account
with the user's own web service token. Everything is read-only. Tools:

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

Design and verified facts about the platform are in `docs/superpowers/specs/`.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
claude mcp add enauczanie -s user -- /home/bob/enauczanie-mcp/.venv/bin/python -m enauczanie_mcp
```

## Token

Login is CAS-only, so the token comes from Moodle's mobile-app launch flow.
One-time: register the `moodlemobile://` scheme handler.

```bash
cp moodle-token-handler.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications
xdg-mime default moodle-token-handler.desktop x-scheme-handler/moodlemobile
```

Then, whenever the token expires or the academic year changes:

```bash
python3 get_token.py
```

Open the printed URL in Firefox, log in, accept the handler prompt. The token is
written to `.env` (`MOODLE_URL`, `MOODLE_TOKEN`) and the result to
`token_result.txt`. For a new year set `MOODLE_URL` (e.g. `.../2026`) in the
environment before running `get_token.py`.

Downloads land in `downloads/<course-slug>/<contextid>/...`, mirroring Moodle's
pluginfile path. Delete a file to re-download it.
