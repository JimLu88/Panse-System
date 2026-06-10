from __future__ import annotations


class Topics:
    # Core -> UI (PUB/SUB)
    CHANNEL_STATUS = "channel.status"
    ALERT_EVENT = "alert.event"

    # UI -> Core (REQ/REP): command names
    CMD_RESUME_AUTO = "resume_auto"
    CMD_PAUSE_CHANNEL = "pause_channel"
    CMD_RESUME_CHANNEL = "resume_channel"
    CMD_OPEN_WINDOW = "open_window"

