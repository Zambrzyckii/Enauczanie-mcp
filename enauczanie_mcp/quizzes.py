from .formatting import format_datetime, strip_multilang


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
