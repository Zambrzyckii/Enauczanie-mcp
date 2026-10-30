import re
from html.parser import HTMLParser

from .formatting import _VOID_TAGS, compact, format_datetime, html_to_text, strip_multilang

# Moodle wraps each reviewed question in header, flag, control and screen-reader blocks that repeat the metadata.
QUESTION_NOISE = frozenset({"info", "questionflag", "im-controls", "accesshide", "sr-only", "visually-hidden"})
# Question fields are named q<usage>:<slot>_<variable>. Variables starting with ":" belong to the question engine
# (sequence check, flag) and those starting with "-" to the behaviour, such as the Check button that grades at once.
_FIELD = re.compile(r"q\d+:\d+_(.+)")
_BUTTON_TYPES = frozenset({"submit", "button", "image", "reset"})
_WS = re.compile(r"\s+")
_ANSWER_HINTS = {
    "one": "one option number",
    "several": "list of option numbers",
    "text": "text",
    "unsupported": "not supported here; the user answers it in the browser",
}


def quiz_deadline(quiz: dict, course_names: dict[int, str]) -> tuple[int, dict]:
    return quiz.get("timeclose", 0), {
        "course_id": quiz["course"],
        "course": course_names.get(quiz["course"], str(quiz["course"])),
        "type": "quiz",
        "name": strip_multilang(quiz["name"]),
        "quiz_id": quiz["id"],
        "module_id": quiz["coursemodule"],
        "due": format_datetime(quiz.get("timeclose")),
    }


def summarize_attempt(attempt: dict) -> dict:
    return compact(
        {
            "attempt_id": attempt["id"],
            "number": attempt.get("attempt"),
            "state": attempt.get("state"),
            "finished": format_datetime(attempt.get("timefinish")),
            "sum_grades": attempt.get("sumgrades"),
        }
    )


def page_count(attempt: dict) -> int:
    # The layout lists the question slots of each page followed by a 0.
    return attempt.get("layout", "").split(",").count("0")


def time_left(quiz: dict, attempt: dict, now: float) -> str | None:
    """Time until the attempt must be sent, counted like Moodle's time limit rule: the earlier of its start plus the
    limit and the quiz's close date."""
    ends = [attempt["timestart"] + quiz["timelimit"]] if quiz.get("timelimit") else []
    if quiz.get("timeclose"):
        ends.append(quiz["timeclose"])
    if not ends:
        return None
    minutes, seconds = divmod(max(min(ends) - int(now), 0), 60)
    return f"{minutes} min {seconds} s"


def summarize_question(question: dict) -> dict:
    return compact(
        {
            "number": question.get("number"),
            "type": question.get("type"),
            "state": question.get("state"),
            "mark": question.get("mark"),
            "max_mark": question.get("maxmark"),
            "text": html_to_text(
                question.get("html", ""), None, controls=True, keep_breaks=True, skip_classes=QUESTION_NOISE
            ),
        }
    )


class _QuestionForm(HTMLParser):
    """The controls of one rendered question in page order, with the texts of its body, labels and options."""

    def __init__(self) -> None:
        super().__init__()
        self.controls: list[dict] = []
        self.texts: dict[str, str] = {}
        self._open: list[list] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        for frame in self._open:
            frame[1] += frame[0] == tag
        if tag in ("input", "textarea", "select"):
            self.controls.append({"tag": tag, **attributes})
        elif tag == "option" and self.controls:
            self.controls[-1].setdefault("options", []).append(attributes)
        if tag in _VOID_TAGS:
            return
        classes = set(attributes.get("class", "").split())
        targets: list[tuple[dict | None, str]] = []
        if classes & (QUESTION_NOISE | {"answernumber"}):
            targets.append((None, ""))
        if attributes.get("id"):
            targets.append((self.texts, "#" + attributes["id"]))
        if tag == "label" and attributes.get("for"):
            targets.append((self.texts, "for:" + attributes["for"]))
        if "qtext" in classes:
            targets.append((self.texts, "qtext"))
        if tag == "textarea" or (tag == "option" and self.controls):
            targets.append((self.controls[-1] if tag == "textarea" else self.controls[-1]["options"][-1], "text"))
        self._open += [[tag, 1, [], target, key] for target, key in targets]

    def handle_endtag(self, tag: str) -> None:
        for frame in self._open:
            frame[1] -= frame[0] == tag
            if frame[1] == 0 and frame[3] is not None:
                frame[3][frame[4]] = _WS.sub(" ", "".join(frame[2])).strip()
        self._open = [frame for frame in self._open if frame[1] > 0]

    def handle_data(self, data: str) -> None:
        if all(frame[3] is not None for frame in self._open):
            for frame in self._open:
                frame[2].append(data)


def _value(control: dict) -> str:
    if control["tag"] == "select":
        options = control.get("options") or [{}]
        chosen = next((o for o in options if "selected" in o), options[0])
        return chosen.get("value", chosen.get("text", ""))
    return control.get("text", "") if control["tag"] == "textarea" else control.get("value", "")


def parse_question_form(html: str) -> dict:
    """The answer field of one question of an attempt page and the hidden fields the page sends along with it. kind
    is one, several or text, none for a question without an answer and unsupported for any other shape."""
    form = _QuestionForm()
    form.feed(html)
    form.close()
    hidden, radios, boxes, texts, others = {}, [], [], [], []
    for control in form.controls:
        name, kind = control.get("name", ""), control.get("type", "text").lower()
        field = _FIELD.fullmatch(name)
        if not field or kind in _BUTTON_TYPES or field[1].startswith("-") or field[1] == ":flagged":
            continue
        if kind == "hidden" or field[1].startswith(":") or field[1].endswith("format"):
            hidden[name] = _value(control)
        elif kind == "radio":
            radios.append(control)
        elif kind == "checkbox":
            boxes.append(control)
        else:
            (others if control["tag"] == "select" else texts).append(control)

    def label(control: dict) -> str:
        by_aria = form.texts.get("#" + control.get("aria-labelledby", ""), "")
        return by_aria or form.texts.get("for:" + control.get("id", ""), "")

    result = {"text": form.texts.get("qtext", ""), "hidden": hidden}
    count = len({c["name"] for c in radios}) + bool(boxes) + len(texts) + len(others)
    if not count:
        # Drag-and-drop questions and attachment-only essays keep the answer in hidden fields that the page's script fills.
        stored = [name for name in hidden if ":" not in _FIELD.fullmatch(name)[1] and not name.endswith("format")]
        return {**result, "kind": "unsupported" if stored else "none"}
    if count > 1 or others:
        return {**result, "kind": "unsupported"}
    if radios:
        # Value -1 is the hidden radio behind "Clear my choice".
        choices = [c for c in radios if c.get("value") != "-1"]
        options = [{"value": c.get("value", ""), "label": label(c), "checked": "checked" in c} for c in choices]
        return {**result, "kind": "one", "name": radios[0]["name"], "options": options}
    if boxes:
        options = [{"name": c["name"], "label": label(c), "checked": "checked" in c} for c in boxes]
        return {**result, "kind": "several", "options": options}
    return {**result, "kind": "text", "name": texts[0]["name"], "current": _value(texts[0])}


def summarize_form_question(question: dict, form: dict) -> dict:
    summary = {
        "question": question["slot"],
        "number": question.get("number") or question.get("questionnumber"),
        "type": question.get("type"),
        "text": form["text"],
        "answer": _ANSWER_HINTS.get(form["kind"]),
    }
    if form["kind"] in ("one", "several"):
        summary["options"] = [
            compact({"option": number, "text": option["label"], "chosen": option["checked"] or None})
            for number, option in enumerate(form["options"], 1)
        ]
    elif form["kind"] == "text":
        summary["current"] = form["current"]
    return compact(summary)
