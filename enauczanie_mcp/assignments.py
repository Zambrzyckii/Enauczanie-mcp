from .formatting import compact, cut, format_datetime, html_to_text, strip_multilang, summarize_files

INTRO_LIMIT = 2_000


def summarize_assignment(assignment: dict) -> dict:
    intro, truncated = cut(html_to_text(assignment.get("intro", ""), None, links=True, keep_breaks=True), INTRO_LIMIT)
    files = (
        assignment.get("introattachments", [])
        + assignment.get("introfiles", [])
        + assignment.get("activityattachments", [])
    )
    submission_types = [
        c["plugin"]
        for c in assignment.get("configs", [])
        if c.get("subtype") == "assignsubmission" and c.get("name") == "enabled" and c.get("value") == "1"
        and c["plugin"] != "comments"
    ]
    return compact(
        {
            "assignment_id": assignment["id"],
            "module_id": assignment["cmid"],
            "name": strip_multilang(assignment["name"]),
            "intro": intro,
            "intro_truncated": truncated or None,
            "instructions": html_to_text(assignment.get("activity", ""), None, links=True, keep_breaks=True),
            "files": summarize_files(files),
            "opens": format_datetime(assignment.get("allowsubmissionsfromdate")),
            "due": format_datetime(assignment.get("duedate")),
            "cutoff": format_datetime(assignment.get("cutoffdate")),
            "grading_due": format_datetime(assignment.get("gradingduedate")),
            "max_grade": assignment["grade"] if assignment.get("grade", 0) > 0 else None,
            "submission_types": submission_types,
        }
    )


def summarize_status(payload: dict) -> dict:
    attempt = payload.get("lastattempt") or {}
    submission = attempt.get("submission") or {}
    feedback = payload.get("feedback") or {}
    status = submission.get("status", "new")
    return compact(
        {
            "status": status,
            "submitted": format_datetime(submission.get("timemodified")) if status == "submitted" else None,
            "locked": attempt.get("locked") or None,
            "can_submit": attempt.get("cansubmit"),
            "extension_due": format_datetime(attempt.get("extensionduedate")),
            "grading_status": attempt.get("gradingstatus"),
            "my_text": plugin_text(submission.get("plugins", []), "onlinetext"),
            "my_files": plugin_files(submission.get("plugins", [])),
            "grade": html_to_text(feedback.get("gradefordisplay", "")),
            "graded": format_datetime(feedback.get("gradeddate")),
            "feedback": plugin_text(feedback.get("plugins", []), "comments"),
            "feedback_files": plugin_files(feedback.get("plugins", [])),
        }
    )


def plugin_text(plugins: list[dict], plugin_type: str) -> str:
    fields = [f.get("text", "") for p in plugins if p.get("type") == plugin_type for f in p.get("editorfields", [])]
    return html_to_text("\n".join(fields), None, links=True, keep_breaks=True)


def plugin_files(plugins: list[dict]) -> list[dict]:
    files = []
    for plugin in plugins:
        for area in plugin.get("fileareas", []):
            # Only the download area of editpdf holds the annotated PDF shown to the student.
            if plugin.get("type") == "editpdf" and area.get("area") != "download":
                continue
            files.extend(area.get("files", []))
    return summarize_files(files)


def assignment_deadline(assignment: dict, course: dict) -> tuple[int, dict]:
    return assignment.get("duedate", 0), {
        "course_id": course["id"],
        "course": course["shortname"],
        "type": "assign",
        "name": strip_multilang(assignment["name"]),
        "assignment_id": assignment["id"],
        "module_id": assignment["cmid"],
        "due": format_datetime(assignment.get("duedate")),
    }
