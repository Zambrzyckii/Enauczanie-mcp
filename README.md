# enauczanie-mcp

Local MCP server that lets Claude Code browse and download course materials from
eNauczanie PG (Moodle) with the user's own web service token. Three tools:
`list_courses`, `get_course_contents`, `download_file`. Design and verified facts
about the platform are in `docs/superpowers/specs/`.

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

Downloads land in `downloads/<course-slug>/`; delete a file to re-download it.
