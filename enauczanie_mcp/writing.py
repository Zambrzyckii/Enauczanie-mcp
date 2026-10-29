"""Write tools, registered only when ENAUCZANIE_ALLOW_WRITE=1 is set.

Every tool here changes the user's real Moodle account. Content comes verbatim from the
user; irreversible steps require an explicit confirm argument. Quiz attempts are made only
for quizzes without marks, such as surveys; graded quizzes and H5P attempt results are
deliberately not supported.
"""
import math
from pathlib import Path

from .assignments import summarize_status
from .attendance import (
    APP_VERSION_CODE, MESSAGES, asks_password, can_submit, parse_messages, parse_sessions, parse_statuses
)
from .formatting import compact, cut, find, format_datetime, full_text, html_to_text, strip_multilang, text_to_html
from .moodle_client import MoodleApiError
from .quizzes import parse_question_form, summarize_attempt, summarize_form_question
from .server import ToolError, _client, _moodle_tool, _userid, server
from .surveys import CHOICE_TYPES, numeric_range, parse_choices, range_text, summarize_item

ONLINE_TEXT_HTML = 1
# grade_calculator::ALMOST_ZERO, the bound quiz_has_grades() uses for the maximum grade and the sum of question marks.
ALMOST_ZERO = 0.000005


def _module(course_id: int, module_id: int, modname: str) -> dict:
    sections = _client().call(
        "core_course_get_contents", courseid=course_id, **{"options[0][name]": "cmid", "options[0][value]": module_id}
    )
    module = find([m for s in sections for m in s.get("modules", [])], "id", module_id)
    if not module:
        raise ToolError(f"No module {module_id} in course {course_id}")
    if module["modname"] != modname:
        raise ToolError(f"Module {module_id} is a {module['modname']}, not a {modname}")
    return module


def _resolve_instance(course_id: int, module_id: int, modname: str) -> int:
    return _module(course_id, module_id, modname)["instance"]


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


def _chosen_options(where: str, answer, several: bool, count: int) -> list[int]:
    chosen = [] if answer is None else answer if isinstance(answer, list) else [answer]
    chosen = [int(n) if isinstance(n, str) and n.isdigit() else n for n in chosen]
    if not all(isinstance(n, int) and 1 <= n <= count for n in chosen) or (not several and len(chosen) > 1):
        wanted = "a list of option numbers" if several else "one option number"
        raise ToolError(f"{where}: answer with {wanted} from 1 to {count}")
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
        chosen = _chosen_options(f"Item {item['id']}", answer, subtype == "c", len(labels))
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


def _attendance_page(course_id: int, module_id: int, method: str, **args) -> str:
    args = {"cmid": module_id, "courseid": course_id, "appversioncode": APP_VERSION_CODE, **args}
    params = {}
    for index, (name, value) in enumerate((n, v) for n, v in args.items() if v is not None):
        params[f"args[{index}][name]"] = name
        params[f"args[{index}][value]"] = value
    payload = _client().call("tool_mobile_get_content", component="mod_attendance", method=method, **params)
    return "".join(template.get("html", "") for template in payload.get("templates", []))


def _attendance_status(offered: dict[int, str], status: str | None) -> int | None:
    """The status id to send. None leaves it to Moodle, which a session with an automatic status sets itself."""
    if not offered:
        return None
    if status:
        chosen = [stid for stid, text in offered.items() if text.casefold() == status.strip().casefold()]
        if not chosen:
            raise ToolError(f"No status {status!r} is offered now; Moodle offers: {', '.join(offered.values())}")
        return chosen[0]
    if len(offered) > 1:
        raise ToolError(
            f"Moodle offers several statuses: {', '.join(offered.values())}. Ask the user which one applies and pass "
            "it as status; nothing was sent"
        )
    return next(iter(offered))


def _recorded(session: dict) -> dict:
    return compact({"session": session["time"], "group": session["group"], "status": session["status"]})


@server.tool()
@_moodle_tool
def mark_attendance(
    course_id: int, module_id: int, status: str | None = None, password: str | None = None, confirm: bool = False
) -> dict:
    """WRITE: the "Submit attendance" button of an attendance activity (module of type attendance, id from
    get_course_contents). Records the user's attendance at its session open for self-marking now, so use it only when
    the user says they are at that class now. password is the one the teacher gives in class, status the name of the
    status the user picks where Moodle offers several. Without confirm=true the attendance activity is not opened,
    because opening its session list can already record attendance; show the error to the user and repeat with
    confirm=true only after their explicit yes."""
    module = _module(course_id, module_id, "attendance")
    if not confirm:
        lines = [
            f"Attendance: {strip_multilang(module['name'])}",
            "Marks the user at the session of this activity that is open for self-marking now",
            f"Status: {status}" if status else "Status: the one Moodle offers; if it offers several, nothing is sent",
            "Password: given" if password else "Password: none, so a session that asks for one is refused",
            "Only for a class the user is at now",
        ]
        raise _confirm_first("recorded", lines)
    page = _attendance_page(course_id, module_id, "mobile_view_activity")
    listed = parse_sessions(page)
    open_now = [s for s in listed if s["session_id"]]
    if len(open_now) > 1:
        raise ToolError(f"Several sessions are open for marking ({', '.join(s['time'] for s in open_now)}); use the app")
    if not open_now:
        # The plugin hides the session id but shows no status only where the session takes marks from other networks.
        if any(not s["status"] for s in listed):
            raise ToolError(f"Moodle does not take attendance from here: {MESSAGES['subnetwrong']}")
        if listed:
            note = "No session was waiting for a mark; Moodle shows these statuses, set earlier or when the list opened"
            return {"recorded": [_recorded(s) for s in listed], "note": note}
        reasons = "; ".join(parse_messages(page))
        raise ToolError("No session of this activity is open for self-marking now" + (f" ({reasons})" if reasons else ""))
    session = open_now[0]
    form = _attendance_page(course_id, module_id, "mobile_user_form", sessid=session["session_id"])
    if not can_submit(form):
        reasons = "; ".join(parse_messages(form)) or "the form has no submit button"
        raise ToolError(f"Moodle does not take attendance for {session['time']} now: {reasons}")
    if asks_password(form) and not password:
        raise ToolError("This session asks for the password the teacher gives in class; nothing was sent")
    status_id = _attendance_status(parse_statuses(form), status)
    page = _attendance_page(
        course_id, module_id, "mobile_view_activity", sessid=session["session_id"], status=status_id, studentpass=password
    )
    after = next((s for s in parse_sessions(page) if (s["time"], s["group"]) == (session["time"], session["group"])), {})
    if not after.get("status"):
        raise ToolError(f"Moodle did not record the attendance: {'; '.join(parse_messages(page)) or 'it gave no reason'}")
    return {"recorded": [_recorded(after)]}


def _survey_quiz(course_id: int, module_id: int) -> dict:
    quizzes = _client().call("mod_quiz_get_quizzes_by_courses", **{"courseids[0]": course_id})["quizzes"]
    quiz = find(quizzes, "coursemodule", module_id)
    if not quiz:
        raise ToolError(f"No quiz with module id {module_id} in course {course_id}")
    # Moodle's quiz_has_grades(): marks count only when both values reach ALMOST_ZERO. A missing value counts as marks.
    marks = (quiz.get("grade"), quiz.get("sumgrades"))
    if None in marks or min(float(m) for m in marks) >= ALMOST_ZERO:
        raise ToolError("This quiz gives marks, so it is a test; the write tools only fill in quizzes without marks")
    # Moodle sends the navigation setting only while the quiz is open to the user.
    if "navmethod" not in quiz:
        raise ToolError("Moodle does not open this quiz to the user now; it is closed or restricted")
    if quiz["navmethod"] != "free":
        raise ToolError("This quiz must be answered page by page in a fixed order; the user fills it in in the browser")
    return quiz


def _unfinished_attempt(quiz: dict) -> dict | None:
    attempts = _client().call("mod_quiz_get_user_attempts", quizid=quiz["id"], status="unfinished")["attempts"]
    return attempts[-1] if attempts else None


def _attempt_questions(attempt_id: int) -> dict[int, tuple[dict, dict]]:
    """Every question of the attempt by slot, with its parsed form."""
    questions, page = {}, 0
    while page >= 0:
        payload = _client().call("mod_quiz_get_attempt_data", attemptid=attempt_id, page=page)
        questions.update({q["slot"]: (q, parse_question_form(q.get("html", ""))) for q in payload["questions"]})
        page = payload["nextpage"]
    return questions


@server.tool()
@_moodle_tool
def get_quiz_survey(course_id: int, module_id: int, confirm: bool = False) -> dict:
    """WRITE: open a survey made as a quiz without marks (module of type quiz, id from get_course_contents) and list
    its questions with option numbers. It continues an attempt already in progress; starting a new one shows as an
    attempt in progress, so without confirm=true nothing starts and the error describes the survey. Quizzes that give
    marks are refused. submit_quiz_survey sends the answers."""
    quiz = _survey_quiz(course_id, module_id)
    attempt = _unfinished_attempt(quiz)
    if not attempt:
        access = _client().call("mod_quiz_get_quiz_access_information", quizid=quiz["id"])
        reasons = "; ".join(html_to_text(reason, None) for reason in access.get("preventaccessreasons", []))
        if not access.get("canattempt") or reasons:
            raise ToolError(f"Moodle does not let the user start this quiz now: {reasons or 'no reason given'}")
        if not confirm:
            lines = [f"Survey: {strip_multilang(quiz['name'])}", "Starts an attempt, in progress until it is sent"]
            if quiz.get("timelimit"):
                lines.append(f"Time limit: {quiz['timelimit'] // 60} min from the start")
            if quiz.get("timeclose"):
                lines.append(f"Closes: {format_datetime(quiz['timeclose'])}")
            raise _confirm_first("started", lines)
        started = _client().call("mod_quiz_start_attempt", quizid=quiz["id"])
        if started.get("warnings") or not started.get("attempt"):
            warning = (started.get("warnings") or [{}])[0]
            raise ToolError(f"Moodle did not start the attempt: {warning.get('message') or 'no reason given'}")
        attempt = started["attempt"]
    questions = _attempt_questions(attempt["id"])
    return compact(
        {
            "name": strip_multilang(quiz["name"]),
            "intro": full_text(quiz.get("intro", "")),
            "questions": [summarize_form_question(question, form) for question, form in questions.values()],
        }
    )


def _quiz_answer_data(slot: int, form: dict, answer) -> dict:
    """What the attempt page sends for one answered question: its hidden fields, sequence check included, and the
    answer itself."""
    if form["kind"] in ("none", "unsupported"):
        raise ToolError(f"Question {slot} takes no answer here; the user answers it in the browser if it has one")
    data = dict(form["hidden"])
    if form["kind"] == "text":
        if isinstance(answer, list):
            raise ToolError(f"Question {slot}: answer with a text")
        # Format 1 means the answer box is an HTML editor, as for online text.
        html = form["hidden"].get(form["name"] + "format") == str(ONLINE_TEXT_HTML)
        data[form["name"]] = text_to_html(str(answer)) if html else str(answer)
        return data
    several = form["kind"] == "several"
    chosen = _chosen_options(f"Question {slot}", answer, several, len(form["options"]))
    if several:
        data.update({option["name"]: "1" if n in chosen else "0" for n, option in enumerate(form["options"], 1)})
    else:
        data[form["name"]] = form["options"][chosen[0] - 1]["value"]
    return data


def _answer_kept(form: dict, data: dict) -> bool:
    """Whether the re-read question shows the answer that was sent. Its state alone does not tell: under immediate
    feedback and interactive behaviours a saved answer stays "not yet answered" until it is checked."""
    if form["kind"] == "one":
        return any(option["checked"] and option["value"] == data.get(form["name"]) for option in form["options"])
    if form["kind"] == "several":
        return all(option["checked"] == (data.get(option["name"]) == "1") for option in form["options"])
    return bool(form["current"])


def _process_attempt(attempt_id: int, data: dict, finish: bool) -> str:
    params = {}
    for index, (name, value) in enumerate(data.items()):
        params[f"data[{index}][name]"] = name
        params[f"data[{index}][value]"] = value
    payload = _client().call("mod_quiz_process_attempt", attemptid=attempt_id, finishattempt=int(finish), **params)
    return payload["state"]


def _quiz_survey_preview(quiz: dict, questions: dict[int, tuple[dict, dict]], answers: dict) -> list[str]:
    lines = [
        f"Survey: {strip_multilang(quiz['name'])}",
        "Not anonymous: a quiz keeps the user's name with the answers, and the teacher can see them",
    ]
    for slot, answer in answers.items():
        question, form = questions[slot]
        chosen = answer if isinstance(answer, list) else [answer]
        shown = str(answer) if form["kind"] == "text" else ", ".join(form["options"][int(n) - 1]["label"] for n in chosen)
        lines.append(f"{question.get('number') or slot}. {cut(form['text'], 80)[0]}: {shown}")
    left = [slot for slot, (_, form) in questions.items() if slot not in answers and form["kind"] != "none"]
    if left:
        numbers = ", ".join(str(questions[slot][0].get("number") or slot) for slot in left)
        lines.append(f"No answer is sent for questions {numbers}; anything saved there earlier stays")
    lines.append("Sending finishes the attempt, after which its answers cannot be changed")
    return lines


@server.tool()
@_moodle_tool
def submit_quiz_survey(
    course_id: int, module_id: int, answers: dict[int, int | str | list[int]], confirm: bool = False
) -> dict:
    """WRITE: answer and send a survey made as a quiz without marks, like its "Submit all and finish" button. answers
    maps question from get_quiz_survey to one option number, a list of option numbers or a text, all given by the
    user. Without confirm=true nothing is saved or sent and the error lists the answers for the user to approve.
    Quizzes that give marks are refused."""
    quiz = _survey_quiz(course_id, module_id)
    attempt = _unfinished_attempt(quiz)
    if not attempt:
        raise ToolError("No attempt of this survey is in progress; get_quiz_survey starts one")
    questions = _attempt_questions(attempt["id"])
    answers = {slot: answer for slot, answer in answers.items() if answer not in (None, "", [])}
    unknown = [str(slot) for slot in answers if slot not in questions]
    if unknown or not answers:
        raise ToolError(
            f"Give answers by question from get_quiz_survey; not in this survey: {', '.join(unknown) or 'none given'}"
        )
    data = {}
    for slot, answer in answers.items():
        data.update(_quiz_answer_data(slot, questions[slot][1], answer))
    if not confirm:
        raise _confirm_first("saved or sent", _quiz_survey_preview(quiz, questions, answers))
    state = _process_attempt(attempt["id"], data, finish=False)
    if state != "inprogress":
        # When the attempt's time is up, Moodle submits it or makes it overdue on any save.
        raise ToolError(f"Moodle ended the attempt itself because its time was up; its state is now {state}")
    saved = _attempt_questions(attempt["id"])
    rejected = [
        str(slot)
        for slot in answers
        if saved[slot][0].get("stateclass") == "invalidanswer" or not _answer_kept(saved[slot][1], data)
    ]
    if rejected:
        raise ToolError(
            f"Moodle did not accept the answers to questions {', '.join(rejected)}; the answers it took stay saved in "
            "the open attempt and the survey is not sent"
        )
    state = _process_attempt(attempt["id"], {}, finish=True)
    finished = _client().call("mod_quiz_get_user_attempts", quizid=quiz["id"], status="finished")["attempts"]
    done = find(finished, "id", attempt["id"])
    if not done:
        raise ToolError(f"Moodle did not finish the attempt; its state is {state}")
    return {"submitted": True, "attempt": summarize_attempt(done)}
