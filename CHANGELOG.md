# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Write mode, off unless `ENAUCZANIE_ALLOW_WRITE=1` is set, with tools that work like Moodle's buttons for assignment submissions (save, submit, remove), group sign-ups, polls, surveys of type `feedback`, attendance self-marking, manual completion and notifications. Tools that hand in, delete, send or free a spot first describe the change and act only with `confirm=true`.

### Changed
- `get_token.py` keeps the other lines of `.env`, such as `ENAUCZANIE_ALLOW_WRITE`, when it stores a new token.

## [0.1.0] - 2026-10-25

### Added
- Read-only MCP server for eNauczanie PG with 14 tools for courses, homework, grades, forums, quizzes, H5P exercises and recent changes.
- `get_token.py`, which gets the web service token through the mobile-app login flow and can register the `moodlemobile://` handler on Linux.
- File downloads limited to `pluginfile.php` URLs of the configured instance.
- Docker image on GHCR, plus a wheel and an sdist attached to the release.

[Unreleased]: https://github.com/Zambrzyckii/Enauczanie-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Zambrzyckii/Enauczanie-mcp/releases/tag/v0.1.0
