# Contributing to enauczanie-mcp

This page says how to run the project, how to propose a change and what a pull request needs.

## Run it locally

Follow [Install](README.md#install), [Token](README.md#token) and [Register in Claude Code](README.md#register-in-claude-code) in the README. With the editable install, a new Claude Code session picks up your changes.

## Propose a change

1. Open an issue first for anything larger than a typo, so the approach can be agreed before you write code.
2. Create a branch from `main`.
3. Keep commits small. A commit message is one imperative sentence, for example `Add grades tool`.
4. Open a pull request against `main` and describe what changed and why in a few sentences.

## Before you open the pull request

- Run the tests with `.venv/bin/python -m unittest`. They cover only the quiz write tools and run them against `tests/fake_moodle.py`, so a change to how those tools call Moodle needs a matching change there. Record new question HTML for `tests/fixtures/` only on a demo site such as `sandbox.moodledemo.net`, never on a real course, and replace its `sesskey`. Call every tool you changed on a real course and check its output. For a write tool, use calls that change nothing (a nonexistent id, or the description it returns without `confirm=true`) unless you accept the change on your own account.
- Read tools in `server.py` change nothing. Do not call Moodle functions there that submit, post, start attempts or mark anything as viewed or read.
- Write tools live only in `writing.py`, which loads only in write mode. A tool that hands in, deletes, sends or frees a spot first describes the change and acts only with `confirm=true`.
- H5P attempts stay out of the project.
- Update the README when a tool, a setting or the structure changes.
- Do not paste your token or other people's course data into issues or pull requests.

## Reporting security issues

Do not open a public issue. Follow [SECURITY.md](SECURITY.md).
