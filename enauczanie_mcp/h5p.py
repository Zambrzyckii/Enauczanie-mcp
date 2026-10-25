import json
import zipfile
from pathlib import Path

from .formatting import html_to_text

# Keys whose string values carry exercise content; everything else in content.json is UI text or metadata.
CONTENT_KEYS = frozenset(
    {
        "text",
        "answer",
        "answers",
        "question",
        "questions",
        "title",
        "description",
        "coverDescription",
        "tip",
        "feedback",
        "chosenFeedback",
        "notChosenFeedback",
        "overallFeedback",
        "introduction",
        "taskDescription",
    }
)
SKIPPED_SUBTREES = frozenset(
    {"metadata", "l10n", "a11y", "behaviour", "confirmCheck", "confirmRetry", "confirmFinish", "image", "copyright"}
)
STRUCTURAL_SEGMENTS = frozenset({"params", "content"})


def read_package(path: Path) -> tuple[dict, dict]:
    """Return (h5p.json, content/content.json) of an .h5p package."""
    with zipfile.ZipFile(path) as package:
        meta = json.loads(package.read("h5p.json"))
        content = json.loads(package.read("content/content.json"))
    return meta, content


def flatten_content(content: dict) -> list[dict]:
    """Walk content.json and return [{path, text}] for exercise text, in document order."""
    items: list[dict] = []

    def walk(node, path: str, key: str) -> None:
        if isinstance(node, dict):
            if str(node.get("library", "")).startswith("H5P.Image"):
                return
            for child_key, value in node.items():
                if child_key in SKIPPED_SUBTREES:
                    continue
                child_path = path if child_key in STRUCTURAL_SEGMENTS else f"{path}.{child_key}" if path else child_key
                walk(value, child_path, child_key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]", key)
        elif isinstance(node, bool):
            if key == "correct":
                items.append({"path": path, "text": str(node).lower()})
        elif isinstance(node, str) and key in CONTENT_KEYS:
            text = html_to_text(node, limit=None)
            if text:
                items.append({"path": path, "text": text})

    walk(content, "", "")
    return items
