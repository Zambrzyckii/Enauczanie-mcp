# Contributing to enauczanie-mcp

Thanks for taking the time to help. This page says how to run the project, how to propose a change and what a pull request needs.

## Run it locally

Follow [Install](README.md#install), [Token](README.md#token) and [Register in Claude Code](README.md#register-in-claude-code) in the README. With the editable install, a new Claude Code session picks up your changes.

## Propose a change

1. Open an issue first for anything larger than a typo, so the approach can be agreed before you write code.
2. Create a branch from `main`.
3. Keep commits small. A commit message is one imperative sentence, for example `Add grades tool`.
4. Open a pull request against `main` and describe what changed and why in a few sentences.

## Before you open the pull request

- There are no automated tests. Call every tool you changed on a real course and check its output.
- The server stays read-only. Do not call Moodle functions that submit, post, start attempts or mark anything as viewed or read.
- Update the README when a tool, a setting or the structure changes.
- Do not paste your token or other people's course data into issues or pull requests.

## Reporting security issues

Do not open a public issue. Follow [SECURITY.md](SECURITY.md).
