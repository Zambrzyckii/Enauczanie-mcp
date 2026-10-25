from .formatting import compact, format_datetime, html_to_text, strip_multilang

_TOTAL_NAMES = {"course": "Course total", "category": "Category total"}


def summarize_course_grade(entry: dict, course_names: dict[int, str]) -> dict:
    return {
        "course_id": entry["courseid"],
        "course": course_names.get(entry["courseid"], str(entry["courseid"])),
        "grade": entry.get("grade"),
    }


def summarize_grade_item(item: dict) -> dict:
    name = item.get("itemname") or _TOTAL_NAMES.get(item.get("itemtype"), item.get("itemtype"))
    return compact(
        {
            "name": strip_multilang(name or ""),
            "type": item.get("itemmodule") or item.get("itemtype"),
            "module_id": item.get("cmid"),
            "grade": item.get("gradeformatted"),
            "range": html_to_text(item.get("rangeformatted") or ""),
            "percentage": item.get("percentageformatted"),
            "weight": item.get("weightformatted"),
            "feedback": html_to_text(item.get("feedback") or "", 500, links=True),
            "graded": format_datetime(item.get("gradedategraded")),
            "hidden": item.get("gradeishidden") or None,
        }
    )
