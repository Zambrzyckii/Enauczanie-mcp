import sys

from .server import _settings, server

try:
    settings = _settings()
except RuntimeError as exc:
    sys.exit(f"enauczanie-mcp: {exc}")
if settings.allow_write:
    from . import writing  # noqa: F401  registers the write tools on the server
server.run("stdio")
