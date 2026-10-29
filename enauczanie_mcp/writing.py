"""Write tools, registered only when ENAUCZANIE_ALLOW_WRITE=1 is set.

Every tool here changes the user's real Moodle account. Content comes verbatim from the
user; irreversible steps require an explicit confirm argument. Quiz attempts and H5P
attempt results are deliberately not implemented.
"""
from pathlib import Path

from .assignments import summarize_status
from .formatting import compact, find, full_text, html_to_text, strip_multilang, text_to_html
from .server import ToolError, _client, _moodle_tool, _userid, server
from .surveys import summarize_item

ONLINE_TEXT_HTML = 1


def _resolve_instance(course_id: int, module_id: int, modname: str) -> int:
    sections = _client().call(
        "core_course_get_contents", courseid=course_id, **{"options[0][name]": "cmid", "options[0][value]": module_id}
    )
    module = find([m for s in sections for m in s.get("modules", [])], "id", module_id)
    if not module:
        raise ToolError(f"No module {module_id} in course {course_id}")
    if module["modname"] != modname:
        raise ToolError(f"Module {module_id} is a {module['modname']}, not a {modname}")
    return module["instance"]


def _assignment_status(assignment_id: int) -> dict:
    return summarize_status(_client().call("mod_assign_get_submission_status", assignid=assignment_id))


def _assignment(course_id: int, assignment_id: int) -> dict:
    payload = _client().call("mod_assign_get_assignments", **{"courseids[0]": course_id})
    assignment = find([a for c in payload["courses"] for a in c["assignments"]], "id", assignment_id)
    if not assignment:
        raise ToolError(f"No assignment {assignment_id} in course {course_id}")
    return assignment


def _submission_setting(assignment: dict, plugin: str, name: str) -> str:
    configs = assignment.get("configs", [])
    wanted = ("assignsubmission", plugin, name)
    return next((c["value"] for c in configs if (c.get("subtype"), c["plugin"], c["name"]) == wanted), "")


def _check_submission(assignment: dict, payload: dict, paths: list[Path], text: str | None) -> None:
    if not (payload.get("lastattempt") or {}).get("canedit"):
        raise ToolError("Moodle does not let the user change this submission now (closed, locked or handed in)")
    if paths and _submission_setting(assignment, "file", "enabled") != "1":
        raise ToolError("This assignment does not accept files")
    max_files = int(_submission_setting(assignment, "file", "maxfilesubmissions") or 0)
    if max_files and len(paths) > max_files:
        raise ToolError(f"This assignment accepts at most {max_files} files")
    if text and _submission_setting(assignment, "onlinetext", "enabled") != "1":
        raise ToolError("This assignment does not accept online text")
    if paths and not text and summarize_status(payload).get("my_text"):
        raise ToolError("Saving only files would erase the online text already in the submission; pass it as text too")


def _confirm_first(done: str, lines: list[str]) -> ToolError:
    intro = f"Nothing {done} yet. Show the user this and call again with confirm=true only after their explicit yes."
    return ToolError("\n".join([intro, *lines]))


def _save_preview(assignment: dict, status: dict, paths: list[Path], text: str | None) -> list[str]:
    lines = [f"Assignment: {strip_multilang(assignment['name'])}"]
    if paths:
        lines.append(f"Files: {', '.join(p.name for p in paths)}")
        current = [f["filename"] for f in status.get("my_files", [])]
        if current:
            lines.append(f"They replace every file now in the submission: {', '.join(current)}")
    if text:
        replaces = " replaces the current one" if status.get("my_text") else ""
        lines.append(f"Online text: the given text{replaces}")
    if assignment.get("submissiondrafts"):
        lines.append("Saved as a draft; submit_assignment hands it in")
    else:
        lines.append("This assignment has no draft stage: saving hands it in for grading and the teacher sees it at once")
    if assignment.get("teamsubmission"):
        lines.append("Team assignment: this changes the shared submission of the whole group")
    return lines


def _group_options(instance: int) -> list[dict]:
    options = _client().call("mod_choicegroup_get_choicegroup_options", choicegroupid=instance, userid=_userid())["options"]
    return [
        compact(
            {
                "option_id": o["id"],
                "group": strip_multilang(o.get("name", "")),
                "taken": o.get("countanswers"),
                "max": o.get("maxanswers") or None,
                "chosen": o.get("checked") or None,
                "locked": o.get("disabled") or None,
            }
        )
        for o in options
    ]


@server.tool()
@_moodle_tool
def save_assignment_submission(
    course_id: int,
    assignment_id: int,
    file_paths: list[str] | None = None,
    text: str | None = None,
    confirm: bool = False,
) -> dict:
    """WRITE: save local files and/or plain text (online text) as the user's submission of an assignment (ids from
    list_assignments). New files replace every file already in the submission. Without confirm=true nothing is saved
    and the error describes the change, including whether saving already hands the work in; show it to the user and
    repeat with confirm=true only after their explicit yes."""
    if not file_paths and not text:
        raise ToolError("Nothing to save: give file_paths, text or both")
    paths = [Path(p).expanduser() for p in file_paths or []]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise ToolError(f"Not a file: {', '.join(missing)}")
    assignment = _assignment(course_id, assignment_id)
    payload = _client().call("mod_assign_get_submission_status", assignid=assignment_id)
    _check_submission(assignment, payload, paths, text)
    if not confirm:
        raise _confirm_first("saved", _save_preview(assignment, summarize_status(payload), paths, text))
    params: dict = {}
    if paths:
        params["plugindata[files_filemanager]"] = _client().upload_draft(paths)
    if text or _submission_setting(assignment, "onlinetext", "enabled") == "1":
        # Item id 0 stores the text as is; a fresh draft area would delete the files embedded in the old text.
        params["plugindata[onlinetext_editor][text]"] = text_to_html(text or "")
        params["plugindata[onlinetext_editor][format]"] = ONLINE_TEXT_HTML
        params["plugindata[onlinetext_editor][itemid]"] = 0
    warnings = _client().call("mod_assign_save_submission", assignmentid=assignment_id, **params)
    if warnings:
        raise ToolError(f"Moodle rejected the submission: {warnings[0].get('message', warnings[0])}")
    status = _assignment_status(assignment_id)
    kept = {f["filename"] for f in status.get("my_files", [])}
    return compact({"saved": True, "status": status, "files_not_kept": [p.name for p in paths if p.name not in kept]})


@server.tool()
@_moodle_tool
def submit_assignment(assignment_id: int, confirm: bool = False) -> dict:
    """WRITE: submit the saved draft of an assignment for grading. Irreversible for the student. Requires the user's
    explicit go-ahead, then confirm=true."""
    if not confirm:
        raise ToolError(
            "Submitting for grading cannot be undone by the student. Ask the user to confirm this exact assignment, "
            "then call again with confirm=true."
        )
    _client().call("mod_assign_submit_for_grading", assignmentid=assignment_id, acceptsubmissionstatement=1)
    return {"submitted": True, "status": _assignment_status(assignment_id)}


@server.tool()
@_moodle_tool
def remove_submission(course_id: int, assignment_id: int, confirm: bool = False) -> dict:
    """WRITE: the "Remove submission" button. Deletes the files and online text of the user's submission of an
    assignment (ids from list_assignments), which then counts as not submitted. Cannot be undone; without confirm=true
    nothing is removed and the error lists what would be deleted."""
    assignment = _assignment(course_id, assignment_id)
    status = _assignment_status(assignment_id)
    if status["status"] in ("new", "reopened"):
        raise ToolError("There is no submission to remove")
    if not confirm:
        lines = [
            f"Assignment: {strip_multilang(assignment['name'])}",
            f"Deletes the files: {', '.join(f['filename'] for f in status.get('my_files', [])) or 'none'}",
        ]
        if status.get("my_text"):
            lines.append("Deletes the online text")
        if status.get("group_submission"):
            lines.append("Team assignment: this empties the shared submission of the whole group")
        raise _confirm_first("removed", lines)
    result = _client().call("mod_assign_remove_submission", userid=_userid(), assignid=assignment_id)
    if not result.get("status"):
        warning = (result.get("warnings") or [{}])[0]
        raise ToolError(f"Moodle did not remove the submission: {warning.get('message') or warning.get('warningcode')}")
    return {"removed": True, "status": _assignment_status(assignment_id)}


@server.tool()
@_moodle_tool
def list_group_choices(course_id: int, module_id: int) -> dict:
    """List the options of a group sign-up (module of type choicegroup, id from get_course_contents) with places
    taken, limits and the user's current choice. Read-only; join_group and leave_group change it."""
    return {"options": _group_options(_resolve_instance(course_id, module_id, "choicegroup"))}


@server.tool()
@_moodle_tool
def join_group(course_id: int, module_id: int, option_id: int) -> dict:
    """WRITE: sign the user up for one option of a group sign-up (option_id from list_group_choices). A full or
    locked option is refused by Moodle."""
    instance = _resolve_instance(course_id, module_id, "choicegroup")
    # Both data forms at once: single-choice mode reads the "responses" entry, multiple mode the response_<id> one.
    _client().call(
        "mod_choicegroup_submit_choicegroup_response",
        choicegroupid=instance,
        **{
            "data[0][name]": "responses",
            "data[0][value]": str(option_id),
            "data[1][name]": f"response_{option_id}",
            "data[1][value]": "true",
        },
    )
    return {"options": _group_options(instance)}


@server.tool()
@_moodle_tool
def leave_group(course_id: int, module_id: int, confirm: bool = False) -> dict:
    """WRITE: withdraw the user's choice from a group sign-up. Someone else may take the spot, so it requires the
    user's explicit go-ahead, then confirm=true."""
    if not confirm:
        raise ToolError(
            "Leaving frees the spot and someone else may take it. Ask the user to confirm, then call again with "
            "confirm=true."
        )
    instance = _resolve_instance(course_id, module_id, "choicegroup")
    _client().call("mod_choicegroup_delete_choicegroup_responses", choicegroupid=instance)
    return {"options": _group_options(instance)}


def _vote_options(instance: int) -> list[dict]:
    options = _client().call("mod_choice_get_choice_options", choiceid=instance)["options"]
    return [
        compact(
            {
                "option_id": o["id"],
                "text": html_to_text(o.get("text", ""), None),
                "taken": o.get("countanswers"),
                "max": o.get("maxanswers") or None,
                "chosen": o.get("checked") or None,
                "locked": o.get("disabled") or None,
            }
        )
        for o in options
    ]


@server.tool()
@_moodle_tool
def list_vote_options(course_id: int, module_id: int) -> dict:
    """List the options of a poll (module of type choice, id from get_course_contents) with their option ids, answer
    counts and the user's current choice. Read-only; submit_vote answers the poll."""
    return {"options": _vote_options(_resolve_instance(course_id, module_id, "choice"))}


@server.tool()
@_moodle_tool
def submit_vote(course_id: int, module_id: int, option_ids: list[int]) -> dict:
    """WRITE: answer a poll (module of type choice, id from get_course_contents) with the option ids the user chose
    (from list_vote_options). Moodle validates them."""
    instance = _resolve_instance(course_id, module_id, "choice")
    params = {f"responses[{i}]": option_id for i, option_id in enumerate(option_ids)}
    _client().call("mod_choice_submit_choice_response", choiceid=instance, **params)
    return {"options": _vote_options(instance)}


@server.tool()
@_moodle_tool
def set_activity_done(module_id: int, done: bool = True) -> dict:
    """WRITE: the "Mark as done" button of an activity with manual completion (module_id from get_course_contents,
    which shows its completion). done=false clears the mark. Moodle refuses activities it tracks automatically."""
    result = _client().call(
        "core_completion_update_activity_completion_status_manually", cmid=module_id, completed=int(done)
    )
    if not result.get("status"):
        raise ToolError("Moodle did not change the completion mark")
    return {"module_id": module_id, "done": done}


@server.tool()
@_moodle_tool
def mark_notification_read(notification_id: int) -> dict:
    """WRITE: mark one Moodle notification as read (id from get_notifications)."""
    _client().call("core_message_mark_notification_read", notificationid=notification_id)
    return {"marked_read": notification_id}


def _survey(course_id: int, module_id: int) -> dict:
    feedbacks = _client().call("mod_feedback_get_feedbacks_by_courses", **{"courseids[0]": course_id})["feedbacks"]
    survey = find(feedbacks, "coursemodule", module_id)
    if not survey:
        raise ToolError(f"No survey (module of type feedback) with module id {module_id} in course {course_id}")
    return survey


def _survey_pages(survey_id: int) -> list[list[dict]]:
    pages: list[list[dict]] = []
    while True:
        payload = _client().call("mod_feedback_get_page_items", feedbackid=survey_id, page=len(pages))
        if payload.get("warnings") and not payload["items"]:
            raise ToolError(f"Moodle does not show the questions: {payload['warnings'][0].get('message')}")
        pages.append(payload["items"])
        if not payload.get("hasnextpage"):
            return pages


@server.tool()
@_moodle_tool
def get_survey(course_id: int, module_id: int) -> dict:
    """List the questions of a survey (module of type feedback, id from get_course_contents) page by page, with item
    ids, option numbers and required answers. Read-only; submit_survey sends the answers."""
    survey = _survey(course_id, module_id)
    access = _client().call("mod_feedback_get_feedback_access_information", feedbackid=survey["id"])
    result = {
        "name": strip_multilang(survey["name"]),
        "intro": full_text(survey.get("intro", "")),
        "anonymous": bool(access.get("isanonymous")),
        "open": bool(access.get("isopen")),
        "already_submitted": bool(access.get("isalreadysubmitted")),
        "can_submit": bool(access.get("cansubmit")),
    }
    if result["can_submit"]:
        result["pages"] = [[summarize_item(item) for item in page] for page in _survey_pages(survey["id"])]
    return compact(result)
