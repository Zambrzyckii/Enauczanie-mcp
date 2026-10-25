from .formatting import compact, format_datetime, html_to_text, strip_multilang


def summarize_notification(notification: dict) -> dict:
    return compact(
        {
            "id": notification["id"],
            "subject": notification.get("subject", ""),
            "text": html_to_text(notification.get("fullmessage") or notification.get("smallmessage", ""), 500, links=True),
            "created": format_datetime(notification.get("timecreated")),
            "read": bool(notification.get("read")),
            "url": notification.get("contexturl"),
            "source": notification.get("component"),
        }
    )


def summarize_change(course: dict, instance: dict, module: dict) -> tuple[int, dict]:
    updates = instance.get("updates", [])
    latest = max((u.get("timeupdated", 0) for u in updates), default=0)
    return latest, compact(
        {
            "course_id": course["id"],
            "course": course["shortname"],
            "module_id": instance["id"],
            "type": module.get("modname"),
            "name": strip_multilang(module.get("name", "")),
            "changed": [u["name"] for u in updates],
            "at": format_datetime(latest),
        }
    )
