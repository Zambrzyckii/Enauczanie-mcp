import re
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import PurePosixPath

_MLANG = re.compile(r"\{mlang\s+([\w-]+)\}(.*?)\{mlang\}", re.S)
_WS = re.compile(r"\s+")


def strip_multilang(text: str, lang: str = "pl") -> str:
    blocks = _MLANG.findall(text)
    if not blocks:
        return text.strip()
    keep = lang if any(code == lang for code, _ in blocks) else blocks[0][0]
    return _WS.sub(" ", _MLANG.sub(lambda m: m.group(2) if m.group(1) == keep else "", text)).strip()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str, limit: int | None = 200) -> str:
    extractor = _TextExtractor()
    extractor.feed(html or "")
    text = _WS.sub(" ", unescape(" ".join(extractor.parts))).strip()
    if limit is None or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "course"


def safe_filename(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/")).name
    return base if base not in ("", ".", "..") else "file"


def format_timestamp(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def summarize_module(module: dict) -> dict:
    contents = module.get("contents") or []
    links = [c["fileurl"] for c in contents if c.get("type") == "url"]
    return {
        "id": module["id"],
        "type": module["modname"],
        "name": strip_multilang(module.get("name", "")),
        "description": html_to_text(module.get("description", "")),
        "url": links[0] if links else None,
        "files": [
            {
                "filename": c["filename"],
                "size": c.get("filesize"),
                "mime": c.get("mimetype"),
                "fileurl": c["fileurl"],
                "modified": format_timestamp(c.get("timemodified")),
            }
            for c in contents
            if c.get("type") == "file"
        ],
    }


def build_course_tree(sections: list[dict]) -> list[dict]:
    tree = []
    for section in sections:
        modules = [m for m in section.get("modules", []) if m.get("uservisible", True)]
        tree.append(
            {
                "section": strip_multilang(section.get("name", "")),
                "summary": html_to_text(section.get("summary", "")),
                "modules": [summarize_module(m) for m in modules],
            }
        )
    return tree
