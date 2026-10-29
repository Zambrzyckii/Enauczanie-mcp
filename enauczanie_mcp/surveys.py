from .formatting import compact, html_to_text

TYPE_SEP = ">>>>>"
LINE_SEP = "|"
ADJUST_SEP = "<<<<<"
RATED_SEP = "####"
CHOICE_TYPES = frozenset({"multichoice", "multichoicerated"})
TEXT_TYPES = frozenset({"textfield", "textarea"})


def parse_choices(presentation: str) -> tuple[str, list[str]]:
    """Return the subtype (r one option, c several, d dropdown) and the option labels of a multichoice item. Moodle
    numbers the options from 1; 0 means no answer."""
    subtype, _, rest = presentation.partition(TYPE_SEP)
    labels = rest.split(ADJUST_SEP)[0].split(LINE_SEP) if rest else []
    return subtype or "r", [html_to_text(label.split(RATED_SEP)[-1], None) for label in labels]


def numeric_range(presentation: str) -> tuple[float | None, float | None]:
    """Moodle stores a missing bound as "-" or leaves it empty."""
    low, _, high = presentation.partition(LINE_SEP)
    return _bound(low), _bound(high)


def _bound(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def range_text(low: float | None, high: float | None) -> str:
    return " to ".join("any" if bound is None else f"{bound:g}" for bound in (low, high))


def summarize_item(item: dict) -> dict:
    kind = item["typ"]
    summary = {
        "item_id": item["id"],
        "number": item.get("itemnumber"),
        "type": kind,
        "text": html_to_text(item.get("presentation" if kind == "label" else "name", ""), None),
        "required": bool(item.get("required")) or None,
    }
    if kind in CHOICE_TYPES:
        subtype, labels = parse_choices(item.get("presentation", ""))
        summary["answer"] = "list of option numbers" if subtype == "c" else "one option number"
        summary["options"] = [{"option": number, "text": label} for number, label in enumerate(labels, 1)]
    elif kind in TEXT_TYPES:
        summary["answer"] = "text"
    elif kind == "numeric":
        summary["answer"] = f"number from {range_text(*numeric_range(item.get('presentation', '')))}"
    elif kind == "captcha":
        summary["answer"] = "captcha, only in the browser"
    if item.get("dependitem"):
        summary["shown_if"] = f"item {item['dependitem']} is answered {item.get('dependvalue')}"
    return compact(summary)
