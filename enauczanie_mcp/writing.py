"""Write tools, registered only when ENAUCZANIE_ALLOW_WRITE=1 is set.

Every tool here changes the user's real Moodle account. Content comes verbatim from the
user; deadlines and irreversible steps require an explicit confirm argument.
"""
from .moodle_client import MoodleApiError
from .server import ToolError, _client, _moodle_tool, server


@server.tool()
@_moodle_tool
def mark_notification_read(notification_id: int) -> dict:
    """Mark one Moodle notification as read (id from get_notifications). WRITE: changes the read state."""
    _client().call("core_message_mark_notification_read", notificationid=notification_id)
    return {"marked_read": notification_id}
