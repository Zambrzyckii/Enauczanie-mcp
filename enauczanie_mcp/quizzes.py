from .formatting import compact, format_datetime, html_to_text, strip_multilang

# Moodle wraps each reviewed question in header, flag, control and screen-reader blocks that repeat the metadata.
QUESTION_NOISE = frozenset({"info", "questionflag", "im-controls", "accesshide", "sr-only", "visually-hidden"})


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
