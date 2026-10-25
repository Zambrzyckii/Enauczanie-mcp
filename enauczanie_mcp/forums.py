from .formatting import compact, cut, format_datetime, html_to_text, strip_multilang, summarize_files

MESSAGE_LIMIT = 1_500


def _message(html: str) -> str:
    return html_to_text(html, None, links=True, keep_breaks=True)


def summarize_forum(forum: dict) -> dict:
    return {
        "forum_id": forum["id"],
        "module_id": forum["cmid"],
        "name": strip_multilang(forum["name"]),
        "type": forum["type"],
    }


def summarize_discussion(discussion: dict) -> dict:
    message, truncated = cut(_message(discussion.get("message", "")), MESSAGE_LIMIT)
    return compact(
        {
            "discussion_id": discussion["discussion"],
            "subject": discussion.get("subject", ""),
            "author": discussion.get("userfullname"),
            "created": format_datetime(discussion.get("created")),
            "replies": discussion.get("numreplies"),
            "pinned": discussion.get("pinned") or None,
            "message": message,
            "message_truncated": truncated or None,
            "files": summarize_files(discussion.get("attachments") or []),
        }
    )


def summarize_post(post: dict) -> dict:
    return compact(
        {
            "post_id": post["id"],
            "parent_id": post.get("parentid"),
            "subject": post.get("subject", ""),
            "author": (post.get("author") or {}).get("fullname"),
            "created": format_datetime(post.get("timecreated")),
            "message": _message(post.get("message", "")),
            "files": summarize_files(post.get("attachments") or []),
        }
    )
