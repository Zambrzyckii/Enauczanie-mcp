"""Write tools, registered only when ENAUCZANIE_ALLOW_WRITE=1 is set.

Every tool here changes the user's real Moodle account. Content comes verbatim from the
user; irreversible steps require an explicit confirm argument. Quiz attempts and H5P
attempt results are deliberately not implemented.
"""
from pathlib import Path

from .assignments import summarize_status
from .formatting import compact, find, strip_multilang
from .server import ToolError, _client, _moodle_tool, server

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


def _group_options(instance: int) -> list[dict]:
    options = _client().call("mod_choicegroup_get_choicegroup_options", choicegroupid=instance, userid=0)["options"]
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
def save_assignment_draft(assignment_id: int, file_paths: list[str] | None = None, text: str | None = None) -> dict:
    """WRITE: save local files and/or online text as the user's draft submission of an assignment (assignment_id
    from list_assignments). Only content the user provided. Does not submit; submit_assignment does."""
    if not file_paths and not text:
        raise ToolError("Nothing to save: give file_paths, text or both")
    params: dict = {}
    if file_paths:
        paths = [Path(p).expanduser() for p in file_paths]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise ToolError(f"Not a file: {', '.join(missing)}")
        params["plugindata[files_filemanager]"] = _client().upload_draft(paths)
    if text:
        params["plugindata[onlinetext_editor][text]"] = text
        params["plugindata[onlinetext_editor][format]"] = ONLINE_TEXT_HTML
        params["plugindata[onlinetext_editor][itemid]"] = _client().call("core_files_get_unused_draft_itemid")["itemid"]
    warnings = _client().call("mod_assign_save_submission", assignmentid=assignment_id, **params)
    if warnings:
        raise ToolError(f"Moodle rejected the submission: {warnings[0].get('message', warnings[0])}")
    return {"saved": True, "status": _assignment_status(assignment_id)}


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


@server.tool()
@_moodle_tool
def submit_vote(course_id: int, module_id: int, option_ids: list[int]) -> dict:
    """WRITE: answer a poll (module of type choice, id from get_course_contents) with the option ids the user chose.
    mod_choice option ids come from its results in the Moodle UI; Moodle validates them."""
    instance = _resolve_instance(course_id, module_id, "choice")
    params = {f"responses[{i}]": option_id for i, option_id in enumerate(option_ids)}
    _client().call("mod_choice_submit_choice_response", choiceid=instance, **params)
    return {"submitted": option_ids}


@server.tool()
@_moodle_tool
def mark_notification_read(notification_id: int) -> dict:
    """WRITE: mark one Moodle notification as read (id from get_notifications)."""
    _client().call("core_message_mark_notification_read", notificationid=notification_id)
    return {"marked_read": notification_id}
