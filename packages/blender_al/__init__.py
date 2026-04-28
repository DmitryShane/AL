bl_info = {
    "name": "AL Blender Activity Logger",
    "author": "AL",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "Preferences > Add-ons > AL Blender Activity Logger",
    "description": "Monitor Blender activity and submit raw activity events to the AL backend",
    "category": "System",
}

import datetime as dt
import getpass
import hashlib
import os
import subprocess
import time
import uuid

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator

from .client import get_plugin_config, submit_event_batch


SOURCE = "bal"
PLUGIN_VERSION = "0.1.0"
MAX_QUEUED_EVENTS = 1000
CONFIG_FETCH_SECONDS = 60
INPUT_THROTTLE_SECONDS = 5
TIMER_SECONDS = 5
INPUT_MOUSE_EVENTS = {
    "MOUSEMOVE",
    "INBETWEEN_MOUSEMOVE",
    "LEFTMOUSE",
    "RIGHTMOUSE",
    "MIDDLEMOUSE",
    "WHEELUPMOUSE",
    "WHEELDOWNMOUSE",
    "TRACKPADPAN",
    "TRACKPADZOOM",
}
IGNORED_INPUT_EVENTS = {
    "TIMER",
    "TIMER0",
    "TIMER1",
    "TIMER2",
    "TIMER_JOBS",
    "TIMER_AUTOSAVE",
    "TIMER_REPORT",
    "WINDOW_DEACTIVATE",
    "NONE",
}

_pending_events: list[dict] = []
_session_id = uuid.uuid4().hex
_last_activity_at: dt.datetime | None = None
_last_send_at = dt.datetime.now(dt.UTC)
_last_config_fetch_at = dt.datetime.min.replace(tzinfo=dt.UTC)
_last_input_event_at = dt.datetime.min.replace(tzinfo=dt.UTC)
_server_enabled = True
_send_interval_seconds = 300
_timer_registered = False
_modal_running = False
_device_id = ""


class AL_OT_submit_report_now(Operator):
    bl_idname = "al.submit_blender_activity_report"
    bl_label = "Submit AL Activity Report"
    bl_description = "Submit queued Blender activity events to the AL backend"

    def execute(self, context):
        queue_event("manual_report_requested", metadata={"reason": "manual"})
        result = send_events(force=True)

        if result:
            self.report({"INFO"}, "AL Blender report submitted")
        else:
            self.report({"WARNING"}, "AL Blender report was not submitted; check settings or console")

        return {"FINISHED"}


class AL_OT_activity_modal(Operator):
    bl_idname = "al.blender_activity_modal"
    bl_label = "AL Blender Activity Tracker"
    bl_description = "Track real Blender user input for AL activity reports"

    def modal(self, context, event):
        global _modal_running

        if not _modal_running:
            return {"CANCELLED"}

        if is_user_input_event(event):
            queue_input_activity(event.type)

        return {"PASS_THROUGH"}

    def invoke(self, context, event):
        global _modal_running

        _modal_running = True
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}


def queue_event(event_type: str, metadata: dict | None = None) -> None:
    global _last_activity_at

    now = dt.datetime.now().astimezone()
    _last_activity_at = now.astimezone(dt.UTC)
    _pending_events.append(
        {
            "eventId": uuid.uuid4().hex,
            "eventType": event_type,
            "occurredAtUtc": _last_activity_at.isoformat().replace("+00:00", "Z"),
            "occurredAtLocal": now.isoformat(),
            "metadata": metadata or {},
        }
    )

    if len(_pending_events) > MAX_QUEUED_EVENTS:
        del _pending_events[: len(_pending_events) - MAX_QUEUED_EVENTS]


def send_events(force: bool = False) -> bool:
    global _last_send_at

    if not _server_enabled:
        return False

    now = dt.datetime.now(dt.UTC)

    if not force and (now - _last_send_at).total_seconds() < _send_interval_seconds:
        return False

    if not _pending_events:
        return False

    events = list(_pending_events)
    payload = {
        "source": SOURCE,
        "pluginVersion": PLUGIN_VERSION,
        "author": resolve_author(),
        "authorEmail": resolve_author_email(),
        "projectId": project_id(),
        "sessionId": _session_id,
        "deviceId": device_id(),
        "timeZoneId": timezone_id(),
        "timeZoneDisplayName": dt.datetime.now().astimezone().tzname() or "",
        "sentAt": now.isoformat().replace("+00:00", "Z"),
        "events": events,
    }

    try:
        submit_event_batch(server_url(), payload, PLUGIN_VERSION, device_id())
    except Exception as exc:
        print(f"[AL Blender] Report submit failed: {exc}")
        return False

    del _pending_events[: len(events)]
    _last_send_at = now
    return True


def fetch_config() -> None:
    global _last_config_fetch_at, _server_enabled, _send_interval_seconds

    now = dt.datetime.now(dt.UTC)

    if (now - _last_config_fetch_at).total_seconds() < CONFIG_FETCH_SECONDS:
        return

    _last_config_fetch_at = now

    try:
        config = get_plugin_config(server_url(), SOURCE, resolve_author(), resolve_author_email(), project_id())
    except Exception as exc:
        print(f"[AL Blender] Config fetch failed: {exc}")
        return

    _server_enabled = bool(config.get("enabled", True))
    _send_interval_seconds = int(config.get("sendIntervalSeconds") or _send_interval_seconds)

    if config.get("submitReportNow"):
        queue_event("manual_report_requested", metadata={"reason": "server_request"})
        send_events(force=True)


def timer_tick():
    fetch_config()

    if _last_activity_at and (dt.datetime.now(dt.UTC) - _last_send_at).total_seconds() >= _send_interval_seconds:
        queue_event("heartbeat")

    send_events()
    return TIMER_SECONDS


def is_user_input_event(event) -> bool:
    if event.type in IGNORED_INPUT_EVENTS:
        return False

    if event.type in INPUT_MOUSE_EVENTS:
        return True

    if event.value in {"PRESS", "CLICK", "DOUBLE_CLICK"}:
        return True

    return False


def queue_input_activity(input_type: str) -> None:
    global _last_input_event_at

    now = dt.datetime.now(dt.UTC)

    if (now - _last_input_event_at).total_seconds() < INPUT_THROTTLE_SECONDS:
        return

    _last_input_event_at = now
    queue_event("scene_changed", metadata={"filepath": bpy.data.filepath or "", "inputType": input_type})


@persistent
def save_post_handler(filepath):
    queue_event("file_saved", metadata={"path": filepath or bpy.data.filepath or "", "name": os.path.basename(filepath or bpy.data.filepath or "")})
    send_events(force=True)


@persistent
def load_post_handler(filepath):
    queue_event("file_loaded", metadata={"path": filepath or bpy.data.filepath or "", "name": os.path.basename(filepath or bpy.data.filepath or "")})


def project_id() -> str:
    filepath = bpy.data.filepath

    if filepath:
        return hashlib.sha1(os.path.abspath(filepath).encode("utf-8")).hexdigest()[:16]

    return "unsaved-blend"


def server_url() -> str:
    return os.environ.get("AL_BACKEND_URL", "http://64.225.108.88:8000").rstrip("/")


def timezone_id() -> str:
    env_tz = os.environ.get("TZ", "").strip()

    if env_tz:
        return env_tz

    localtime_path = "/etc/localtime"

    try:
        resolved = os.path.realpath(localtime_path)
    except Exception:
        resolved = ""

    marker = "zoneinfo/"

    if marker in resolved:
        return resolved.split(marker, 1)[1]

    return dt.datetime.now().astimezone().tzname() or (time.tzname[0] if time.tzname else "")


def resolve_author() -> str:
    return os.environ.get("AL_AUTHOR") or run_git_config("user.name") or getpass.getuser() or "Unknown User"


def resolve_author_email() -> str:
    return os.environ.get("AL_AUTHOR_EMAIL") or run_git_config("user.email") or ""


def device_id() -> str:
    global _device_id

    if not _device_id:
        seed = f"{getpass.getuser()}|{os.uname().nodename if hasattr(os, 'uname') else ''}"
        _device_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()

    return _device_id


def run_git_config(key: str) -> str:
    try:
        result = subprocess.run(
            ["git", "config", "--global", key],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=False,
        )
    except Exception:
        return ""

    return result.stdout.strip()


def register():
    global _timer_registered

    bpy.utils.register_class(AL_OT_submit_report_now)
    bpy.utils.register_class(AL_OT_activity_modal)

    if save_post_handler not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(save_post_handler)

    if load_post_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(load_post_handler)

    if not bpy.app.timers.is_registered(timer_tick):
        bpy.app.timers.register(timer_tick, first_interval=TIMER_SECONDS, persistent=True)
        _timer_registered = True

    start_activity_modal()


def unregister():
    global _timer_registered, _modal_running

    _modal_running = False

    if bpy.app.timers.is_registered(timer_tick):
        bpy.app.timers.unregister(timer_tick)
        _timer_registered = False

    for handlers, handler in (
        (bpy.app.handlers.load_post, load_post_handler),
        (bpy.app.handlers.save_post, save_post_handler),
    ):
        if handler in handlers:
            handlers.remove(handler)

    bpy.utils.unregister_class(AL_OT_activity_modal)
    bpy.utils.unregister_class(AL_OT_submit_report_now)


def start_activity_modal() -> None:
    try:
        bpy.ops.al.blender_activity_modal("INVOKE_DEFAULT")
    except Exception as exc:
        print(f"[AL Blender] Activity tracker failed to start: {exc}")


if __name__ == "__main__":
    register()
