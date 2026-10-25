import sys

from .server import _settings, server

try:
    _settings()
except RuntimeError as exc:
    sys.exit(f"enauczanie-mcp: {exc}")
server.run("stdio")
