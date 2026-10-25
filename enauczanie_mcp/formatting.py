import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath

_MLANG = re.compile(r"\{mlang\s+([\w-]+)\}(.*?)\{mlang\}", re.S)
_WS = re.compile(r"\s+")
_SPACES = re.compile(r"[ \t\r\f\v]+")

_SKIPPED_TAGS = frozenset({"script", "style"})
_BLOCK_TAGS = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "blockquote", "pre"})
_LINE_TAGS = frozenset({"br", "div", "li", "tr"})
_MARKERS = ("[x]", "[ ]")
_CELL_TAGS = frozenset({"td", "th"})
_HIDDEN_INPUTS = frozenset({"hidden", "submit", "button", "reset", "image"})
_COMPLETION_STATES = {0: "incomplete", 1: "complete", 2: "pass", 3: "fail"}


def strip_multilang(text: str, lang: str = "pl") -> str:
    blocks = _MLANG.findall(text)
    if not blocks:
        return text.strip()
    keep = lang if any(code == lang for code, _ in blocks) else blocks[0][0]
    return _SPACES.sub(" ", _MLANG.sub(lambda m: m.group(2) if m.group(1) == keep else "", text)).strip()


class _TextExtractor(HTMLParser):
    def __init__(self, links: bool, controls: bool, skip_classes: frozenset[str]) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._links = links
        self._controls = controls
        self._skip_classes = skip_classes
        self._skip_tag: str | None = None
        self._skip_depth = 0
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if self._skip_tag:
            self._skip_depth += tag == self._skip_tag
        elif tag in _SKIPPED_TAGS or self._skip_classes & set((attributes.get("class") or "").split()):
            self._skip_tag, self._skip_depth = tag, 1
        elif tag in _BLOCK_TAGS or tag in _LINE_TAGS:
            self.parts.append("\n")
        elif tag == "a" and self._links:
            self._href = attributes.get("href")
        elif tag == "input" and self._controls:
            self._handle_input(attributes)
        elif tag == "option" and self._controls and "selected" in attributes:
            self.parts.append("[x] ")

    def _handle_input(self, attributes: dict[str, str | None]) -> None:
        kind = attributes.get("type") or "text"
        if kind in ("radio", "checkbox"):
            self.parts.append("[x] " if "checked" in attributes else "[ ] ")
        elif kind not in _HIDDEN_INPUTS:
            self.parts.append(f"[{attributes.get('value') or ''}] ")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                self._skip_tag = self._skip_tag if self._skip_depth else None
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in _CELL_TAGS:
            self.parts.append(" ")
        elif tag == "a" and self._href:
            if self._href.startswith("http"):
                self.parts.append(f" ({self._href})")
            self._href = None

    def handle_data(self, data: str) -> None:
        if not self._skip_tag:
            self.parts.append(data)


def html_to_text(
    html: str,
    limit: int | None = 200,
    *,
    links: bool = False,
    controls: bool = False,
    keep_breaks: bool = False,
    skip_classes: frozenset[str] = frozenset(),
) -> str:
    extractor = _TextExtractor(links, controls, skip_classes)
    extractor.feed(strip_multilang(html or ""))
    text = "".join(extractor.parts)
    text = _join_lines(text) if keep_breaks else _WS.sub(" ", text).strip()
    return text if limit is None else cut(text, limit)[0]


def _join_lines(text: str) -> str:
    """Trim lines, drop blank ones and attach a lone [x] or [ ] marker to the line that follows it."""
    lines: list[str] = []
    for line in (_SPACES.sub(" ", raw).strip() for raw in text.split("\n")):
        if lines and lines[-1] in _MARKERS:
            if line:
                lines[-1] += " " + line
        elif line:
            lines.append(line)
    return "\n".join(lines)


def cut(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: limit - 1].rstrip() + "…", True


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "course"


def safe_filename(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/")).name
    return base if base not in ("", ".", "..") else "file"


def format_timestamp(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts).date().isoformat()


def format_datetime(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes")


def compact(data: dict) -> dict:
    return {key: value for key, value in data.items() if value not in (None, "", [], {})}


def summarize_files(items: list[dict]) -> list[dict]:
    return [
        {
            "filename": item["filename"],
            "size": item.get("filesize"),
            "mime": item.get("mimetype"),
            "fileurl": item["fileurl"],
            "modified": format_timestamp(item.get("timemodified")),
        }
        for item in items
    ]


def summarize_module(module: dict) -> dict:
    contents = module.get("contents") or []
    links = [c["fileurl"] for c in contents if c.get("type") == "url"]
    # A page module lists only a synthetic index.html; its body comes from get_module_text.
    files = [c for c in contents if c.get("type") == "file"] if module["modname"] != "page" else []
    completion = module.get("completiondata") or {}
    summary = {
        "id": module["id"],
        "type": module["modname"],
        "name": strip_multilang(module.get("name", "")),
        "description": html_to_text(module.get("description", "")),
        "url": links[0] if links else None,
        "files": summarize_files(files),
        "dates": {
            d.get("dataid") or d["label"].rstrip(": "): format_datetime(d["timestamp"]) for d in module.get("dates") or []
        },
        "completion": _COMPLETION_STATES.get(completion.get("state")) if completion.get("hascompletion") else None,
    }
    if not module.get("uservisible", True):
        summary["available"] = False
        summary["availability"] = html_to_text(module.get("availabilityinfo", ""), 300)
    return compact(summary)


def build_course_tree(sections: list[dict]) -> list[dict]:
    tree = []
    for section in sections:
        modules = [m for m in section.get("modules", []) if m.get("uservisible", True) or m.get("availabilityinfo")]
        tree.append(
            compact(
                {
                    "section": strip_multilang(section.get("name", "")),
                    "summary": html_to_text(section.get("summary", "")),
                    "modules": [summarize_module(m) for m in modules],
                }
            )
        )
    return tree
