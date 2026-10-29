"""Write tools, registered only when ENAUCZANIE_ALLOW_WRITE=1 is set.

Every tool here changes the user's real Moodle account. Content comes verbatim from the
user; irreversible steps require an explicit confirm argument. Quiz attempts and H5P
attempt results are deliberately not implemented.
"""
import math
from pathlib import Path

from .assignments import summarize_status
from .formatting import compact, cut, find, full_text, html_to_text, strip_multilang, text_to_html
from .moodle_client import MoodleApiError
from .server import ToolError, _client, _moodle_tool, _userid, server
from .surveys import CHOICE_TYPES, numeric_range, parse_choices, range_text, summarize_item

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


def _submit_group_answer(instance: int, option_id: int | None, keep: list[dict]) -> None:
    # A single-choice sign-up reads only the "responses" entry. One that allows several groups reads the
    # response_<id> entries and drops the user from every group missing there, so the kept groups always go along.
    data = [("responses", str(option_id))] if option_id is not None else []
    data += [(f"response_{o['option_id']}", "true") for o in keep]
    params = {}
    for index, (name, value) in enumerate(data):
        params[f"data[{index}][name]"] = name
        params[f"data[{index}][value]"] = value
    _client().call("mod_choicegroup_submit_choicegroup_response", choicegroupid=instance, **params)


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
def submit_assignment(course_id: int, assignment_id: int, confirm: bool = False) -> dict:
    """WRITE: the "Submit assignment" button: hand the saved draft of an assignment in for grading (ids from
    list_assignments). The student cannot undo it. Without confirm=true nothing is handed in and the error describes
    the draft; show it to the user and repeat with confirm=true only after their explicit yes."""
    assignment = _assignment(course_id, assignment_id)
    status = _assignment_status(assignment_id)
    if status["status"] != "draft":
        raise ToolError(f"Only a draft can be handed in; this submission is {status['status']}")
    if not confirm:
        lines = [
            f"Assignment: {strip_multilang(assignment['name'])}",
            f"Files: {', '.join(f['filename'] for f in status.get('my_files', [])) or 'none'}",
        ]
        if status.get("my_text"):
            lines.append("Online text included")
        if assignment.get("teamsubmission"):
            lines.append("Team assignment: this hands in the submission of the whole group")
        if assignment.get("requiresubmissionstatement"):
            lines.append(f"It also accepts this statement: {full_text(assignment.get('submissionstatement', ''))}")
        lines.append("The student cannot undo this")
        raise _confirm_first("handed in", lines)
    warnings = _client().call("mod_assign_submit_for_grading", assignmentid=assignment_id, acceptsubmissionstatement=1)
    if warnings:
        raise ToolError(f"Moodle did not hand the work in: {warnings[0].get('message') or warnings[0].get('warningcode')}")
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
def join_group(course_id: int, module_id: int, option_id: int, confirm: bool = False) -> dict:
    """WRITE: sign the user up for one option of a group sign-up (option_id from list_group_choices). The choice may
    be final and may move the user out of a current group, so without confirm=true nothing changes and the error
    describes the change; repeat with confirm=true only after the user's explicit yes."""
    instance = _resolve_instance(course_id, module_id, "choicegroup")
    options = _group_options(instance)
    target = find(options, "option_id", option_id)
    if not target:
        raise ToolError(f"No option {option_id} in this group sign-up")
    if target.get("chosen"):
        raise ToolError(f"The user is already in {target['group']}")
    if target.get("locked"):
        raise ToolError(f"{target['group']} is full or closed")
    current = [o for o in options if o.get("chosen")]
    if not confirm:
        lines = [f"Joins: {target['group']}"]
        if current:
            lines.append(
                f"Now in: {', '.join(o['group'] for o in current)}. Where the sign-up allows one group, joining moves "
                "the user out of it and frees that place; where it allows several, the user stays there too"
            )
        lines.append("If the sign-up does not allow changes, the choice is final")
        raise _confirm_first("changed", lines)
    _submit_group_answer(instance, option_id, [target, *current])
    options = _group_options(instance)
    chosen = {o["option_id"] for o in options if o.get("chosen")}
    now_in = ", ".join(o["group"] for o in options if o.get("chosen")) or "no group"
    if option_id not in chosen:
        raise ToolError(f"Moodle did not sign the user up for {target['group']}; the user is now in: {now_in}")
    return compact({"options": options, "left": [o["group"] for o in current if o["option_id"] not in chosen]})


@server.tool()
@_moodle_tool
def leave_group(course_id: int, module_id: int, option_id: int, confirm: bool = False) -> dict:
    """WRITE: withdraw the user from one option of a group sign-up (option_id from list_group_choices). Someone else
    may take the place, so without confirm=true nothing changes and the error names the group; repeat with
    confirm=true only after the user's explicit yes."""
    instance = _resolve_instance(course_id, module_id, "choicegroup")
    chosen = [o for o in _group_options(instance) if o.get("chosen")]
    target = find(chosen, "option_id", option_id)
    if not target:
        raise ToolError(f"The user is not in option {option_id} of this group sign-up")
    if not confirm:
        raise _confirm_first("changed", [f"Leaves: {target['group']}", "Someone else may take the place"])
    keep = [o for o in chosen if o["option_id"] != option_id]
    if keep:
        _submit_group_answer(instance, None, keep)
    else:
        try:
            _client().call("mod_choicegroup_delete_choicegroup_responses", choicegroupid=instance)
        except MoodleApiError as exc:
            # Deleting is refused where several groups are allowed; there an empty answer withdraws instead.
            if exc.errorcode != "missingrequiredcapability":
                raise
            _submit_group_answer(instance, None, [])
    options = _group_options(instance)
    if find(options, "option_id", option_id).get("chosen"):
        raise ToolError(f"Moodle did not withdraw the user from {target['group']} (changes may be locked)")
    return {"options": options}


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
def submit_vote(course_id: int, module_id: int, option_ids: list[int], confirm: bool = False) -> dict:
    """WRITE: answer a poll (module of type choice, id from get_course_contents) with the option ids the user chose
    (from list_vote_options). Without confirm=true nothing is sent and the error shows the answer and any earlier one
    it replaces; repeat with confirm=true only after the user's explicit yes."""
    instance = _resolve_instance(course_id, module_id, "choice")
    options = _vote_options(instance)
    texts = {o["option_id"]: o["text"] for o in options}
    unknown = [str(option_id) for option_id in option_ids if option_id not in texts]
    if unknown or not option_ids:
        raise ToolError(f"Give option ids from list_vote_options; not in this poll: {', '.join(unknown) or 'none given'}")
    if not confirm:
        lines = [f"Answer: {', '.join(texts[option_id] for option_id in option_ids)}"]
        earlier = [o["text"] for o in options if o.get("chosen")]
        if earlier:
            lines.append(f"Replaces the earlier answer: {', '.join(earlier)}")
        raise _confirm_first("sent", lines)
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


def _chosen_options(item: dict, answer, subtype: str, count: int) -> list[int]:
    chosen = [] if answer is None else answer if isinstance(answer, list) else [answer]
    chosen = [int(n) if isinstance(n, str) and n.isdigit() else n for n in chosen]
    if not all(isinstance(n, int) and 1 <= n <= count for n in chosen) or (subtype != "c" and len(chosen) > 1):
        wanted = "a list of option numbers" if subtype == "c" else "one option number"
        raise ToolError(f"Item {item['id']}: answer with {wanted} from 1 to {count}")
    return chosen


def _numeric_answer(item: dict, answer) -> str:
    low, high = numeric_range(item.get("presentation", ""))
    try:
        value = float(answer)
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value):
        raise ToolError(f"Item {item['id']}: answer with a number")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ToolError(f"Item {item['id']}: the number must be from {range_text(low, high)}")
    return str(int(value)) if value.is_integer() else repr(value)


def _survey_responses(item: dict, answer) -> list[tuple[str, object]]:
    """The name/value pairs the Moodle app sends for one question; answer None leaves it empty."""
    name, kind = f"{item['typ']}_{item['id']}", item["typ"]
    if kind == "info":
        return [(name, "__CURRENT__TIMESTAMP__" if item.get("presentation") == "1" else item.get("otherdata", ""))]
    if kind in CHOICE_TYPES:
        subtype, labels = parse_choices(item.get("presentation", ""))
        chosen = _chosen_options(item, answer, subtype, len(labels))
        if subtype == "c":
            return [(f"{name}[0]", 0)] + [(f"{name}[{n}]", n if n in chosen else 0) for n in range(1, len(labels) + 1)]
        key = f"{name}[0]" if kind == "multichoice" and subtype == "d" else name
        return [(key, chosen[0] if chosen else 0)]
    if kind == "numeric":
        return [(name, "" if answer is None else _numeric_answer(item, answer))]
    return [(name, "" if answer is None else str(answer))]


def _survey_preview(survey: dict, access: dict, items: dict[int, dict], answers: dict) -> list[str]:
    lines = [
        f"Survey: {strip_multilang(survey['name'])}",
        "Anonymous: the teacher does not see who answered" if access.get("isanonymous")
        else "Not anonymous: the teacher sees the user's name next to the answers",
    ]
    for item_id, answer in answers.items():
        summary = summarize_item(items[item_id])
        labels = {o["option"]: o["text"] for o in summary.get("options", [])}
        chosen = answer if isinstance(answer, list) else [answer]
        shown = ", ".join(labels.get(int(n), str(n)) for n in chosen) if labels else str(answer)
        lines.append(f"{cut(summary.get('text', ''), 80)[0]}: {shown}")
    if not survey.get("multiple_submit"):
        lines.append("The survey takes one submission; it cannot be changed afterwards")
    return lines


@server.tool()
@_moodle_tool
def submit_survey(
    course_id: int, module_id: int, answers: dict[int, int | float | str | list[int]], confirm: bool = False
) -> dict:
    """WRITE: fill in and send a survey (module of type feedback), like its submit button. answers maps item_id from
    get_survey to one option number, a list of option numbers (several-choice questions), a number or a text, all
    given by the user. Without confirm=true nothing is sent and the error lists the answers for the user to approve."""
    survey = _survey(course_id, module_id)
    access = _client().call("mod_feedback_get_feedback_access_information", feedbackid=survey["id"])
    if not access.get("cansubmit"):
        raise ToolError("Moodle does not take answers to this survey now (closed or already submitted)")
    pages = _survey_pages(survey["id"])
    items = {item["id"]: item for page in pages for item in page}
    unknown = [str(item_id) for item_id in answers if item_id not in items]
    if unknown:
        raise ToolError(f"No question with item id {', '.join(unknown)} in this survey")
    if any(item["typ"] == "captcha" for item in items.values()):
        raise ToolError("This survey has a captcha, so the user has to send it in the browser")
    missing = [
        str(item_id)
        for item_id, item in items.items()
        if item.get("required") and not item.get("dependitem") and answers.get(item_id) in (None, "", [])
    ]
    if missing:
        raise ToolError(f"Required questions without an answer, item ids: {', '.join(missing)}")
    # Building every page first validates all answers before anything reaches Moodle.
    responses = [
        [pair for item in page if item.get("hasvalue") for pair in _survey_responses(item, answers.get(item["id"]))]
        for page in pages
    ]
    if not confirm:
        raise _confirm_first("sent", _survey_preview(survey, access, items, answers))
    page = 0
    for _ in pages:
        params = {}
        for index, (name, value) in enumerate(responses[page]):
            params[f"responses[{index}][name]"] = name
            params[f"responses[{index}][value]"] = value
        result = _client().call("mod_feedback_process_page", feedbackid=survey["id"], page=page, **params)
        if result.get("completed"):
            check = _client().call("mod_feedback_get_feedback_access_information", feedbackid=survey["id"])
            message = full_text(result.get("completionpagecontents", ""))
            return compact({"submitted": True, "confirmed": bool(check.get("isalreadysubmitted")), "message": message})
        jumpto = result.get("jumpto")
        if jumpto == page or not isinstance(jumpto, int) or not 0 <= jumpto < len(pages):
            raise ToolError(
                f"Moodle did not accept the answers on page {page + 1} (a required answer or a value range); "
                "earlier pages stay saved as unfinished and the survey is not sent"
            )
        page = jumpto
    raise ToolError("Moodle did not finish the survey")
