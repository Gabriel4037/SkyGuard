import io
import json
import os
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_file, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import database

# File and folder paths are kept relative to the server folder so the app can
# run from source code or from a packaged build without changing paths.
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("CENTRAL_DB_PATH", str(BASE_DIR / "data" / "central_server.db"))
STATIC_DIR = str(BASE_DIR / "static")
CLIPS_DIR = os.environ.get("CENTRAL_CLIPS_DIR", str(BASE_DIR / "clips"))
MODELS_DIR = os.environ.get("CENTRAL_MODELS_DIR", str(BASE_DIR / "models"))

# Default values used by the client nodes to classify medium/high threat events.
DEFAULT_THREAT_POLICY = {
    "detection_confidence_cap": 0.4,
    "medium_confidence": 0.75,
    "medium_box_pct": 8.0,
    "high_zone_seconds": 3,
}

os.makedirs(CLIPS_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app, supports_credentials=True)

# The demo runs on a local network, so the cookie is HTTP-only but not HTTPS-only.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "central-server-secret")
app.config.update(
    SESSION_COOKIE_NAME="central_session",
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=86400,
)

_db_conn = None
_frame_cache = {}
_admin_activity = {}
_client_activity = {}
_camera_activity = {}
_monitor_viewers = {}
ACTIVE_USER_WINDOW_SECONDS = int(os.environ.get("ACTIVE_USER_WINDOW_SECONDS", "300"))
MONITOR_ACTIVE_WINDOW_SECONDS = int(os.environ.get("MONITOR_ACTIVE_WINDOW_SECONDS", "10"))
CAMERA_ACTIVE_WINDOW_SECONDS = int(os.environ.get("CAMERA_ACTIVE_WINDOW_SECONDS", "10"))


# ---- Database and authentication helpers ----

def init_db_conn():
    """Create the central database connection on first use."""
    global _db_conn
    if _db_conn is None:
        print("Initializing central DB:", DB_PATH)
        _db_conn = database.init_db(DB_PATH)
    return _db_conn


def init_server_state():
    """Initialise central server state before the Flask app starts."""
    init_db_conn()


def login_required(func):
    """Require any logged-in user before allowing an API call."""
    @wraps(func)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "authentication required"}), 401
        mark_user_activity(current_user())
        return func(*args, **kwargs)

    return decorated


def admin_required(func):
    """Require an administrator session before allowing an API call."""
    @wraps(func)
    def decorated(*args, **kwargs):
        uid = session.get("user_id")
        if not uid:
            return jsonify({"error": "authentication required"}), 401
        user = database.get_user_by_id(init_db_conn(), uid)
        if not user or user.get("role") != "admin":
            return jsonify({"error": "admin required"}), 403
        mark_user_activity(user)
        return func(*args, **kwargs)

    return decorated


# ---- In-memory activity tracking ----

def current_user():
    """Return the currently logged-in central user, if any."""
    uid = session.get("user_id")
    if not uid:
        return None
    return database.get_user_by_id(init_db_conn(), uid)


def current_admin_user():
    """Return the current user only when the user is an admin."""
    user = current_user()
    if not user or user.get("role") != "admin":
        return None
    return user


def mark_user_activity(user):
    """Record recent admin activity for the dashboard summary."""
    if not user:
        return
    if user.get("role") != "admin":
        return
    _admin_activity[str(user["id"])] = {
        "id": user["id"],
        "username": user.get("username", ""),
        "role": user.get("role", "user"),
        "timestamp": datetime.now().isoformat(),
    }


def mark_client_activity(user):
    """Record recent client-node activity for the dashboard summary."""
    if not user:
        return
    _client_activity[str(user["id"])] = {
        "id": user["id"],
        "username": user.get("username", ""),
        "role": user.get("role", "user"),
        "timestamp": datetime.now().isoformat(),
    }


def clear_user_activity(user) -> None:
    """Remove a user from in-memory activity lists after logout."""
    if not user:
        return
    user_id = str(user.get("id"))
    if not user_id:
        return
    _admin_activity.pop(user_id, None)
    _client_activity.pop(user_id, None)


def active_client_users():
    """Return client users seen recently by heartbeat/camera updates."""
    now = datetime.now()
    active = []
    stale_keys = []
    for key, value in list(_client_activity.items()):
        try:
            ts = datetime.fromisoformat(value["timestamp"])
        except Exception:
            stale_keys.append(key)
            continue
        if (now - ts).total_seconds() <= ACTIVE_USER_WINDOW_SECONDS:
            active.append(value)
        else:
            stale_keys.append(key)
    for key in stale_keys:
        _client_activity.pop(key, None)
    return active


def active_admin_users():
    """Return admin users seen recently by admin page/API activity."""
    now = datetime.now()
    active = []
    stale_keys = []
    for key, value in list(_admin_activity.items()):
        try:
            ts = datetime.fromisoformat(value["timestamp"])
        except Exception:
            stale_keys.append(key)
            continue
        if (now - ts).total_seconds() <= ACTIVE_USER_WINDOW_SECONDS:
            active.append(value)
        else:
            stale_keys.append(key)
    for key in stale_keys:
        _admin_activity.pop(key, None)
    return active


def active_users_summary(conn):
    """Combine active admin, client, and camera users into one summary list."""
    user_map = {}
    for item in active_admin_users():
        user_map[int(item["id"])] = {
            "id": int(item["id"]),
            "username": item.get("username", ""),
            "role": item.get("role", "admin"),
        }

    for item in active_client_users():
        user_map[int(item["id"])] = {
            "id": int(item["id"]),
            "username": item.get("username", ""),
            "role": item.get("role", "user"),
        }

    for entry in active_camera_entries():
        user_id = entry.get("user_id")
        if not user_id or int(user_id) in user_map:
            continue
        user = database.get_user_by_id(conn, int(user_id))
        if not user:
            continue
        user_map[int(user_id)] = {
            "id": int(user_id),
            "username": user.get("username", ""),
            "role": user.get("role", "user"),
        }

    return list(user_map.values())


def mark_camera_activity(camera_id: str, user_id=None, is_detecting: bool = False) -> None:
    """Record the latest heartbeat/status update for one camera."""
    if not camera_id:
        return
    _camera_activity[str(camera_id)] = {
        "camera_id": str(camera_id),
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
        "is_detecting": bool(is_detecting),
    }


def active_camera_entries():
    """Return camera activity entries that are still inside the live window."""
    now = datetime.now()
    active_entries = []
    stale_keys = []
    for key, value in list(_camera_activity.items()):
        try:
            ts = datetime.fromisoformat(value["timestamp"])
        except Exception:
            stale_keys.append(key)
            continue
        if (now - ts).total_seconds() <= CAMERA_ACTIVE_WINDOW_SECONDS:
            active_entries.append(value)
        else:
            stale_keys.append(key)
    for key in stale_keys:
        _camera_activity.pop(key, None)
    return active_entries


def mark_monitor_viewer(viewer_id: str) -> None:
    """Record that one admin monitor browser is currently open."""
    if not viewer_id:
        return
    _monitor_viewers[viewer_id] = datetime.now().isoformat()


def active_monitor_viewers() -> int:
    """Count active admin monitor pages based on recent browser heartbeats."""
    now = datetime.now()
    stale_keys = []
    count = 0
    for key, ts_text in list(_monitor_viewers.items()):
        try:
            ts = datetime.fromisoformat(ts_text)
        except Exception:
            stale_keys.append(key)
            continue
        if (now - ts).total_seconds() <= MONITOR_ACTIVE_WINDOW_SECONDS:
            count += 1
        else:
            stale_keys.append(key)
    for key in stale_keys:
        _monitor_viewers.pop(key, None)
    return count


def admin_page_or_login(filename: str):
    """Serve an admin page or redirect unauthenticated users to login."""
    if not current_admin_user():
        return redirect("/login.html")
    return send_from_directory(STATIC_DIR, filename)


# ---- Shared policy and file helpers ----

def client_registration_enabled() -> bool:
    """Read whether clients are allowed to register themselves."""
    value = database.get_setting(init_db_conn(), "client_registration_enabled", "0")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def normalize_threat_policy(data: dict) -> dict:
    """Validate threat policy values before saving or returning them."""
    policy = dict(DEFAULT_THREAT_POLICY)
    policy.update(data or {})
    policy["detection_confidence_cap"] = max(0.05, min(0.95, float(policy["detection_confidence_cap"])))
    policy["medium_confidence"] = max(0.05, min(0.99, float(policy["medium_confidence"])))
    policy["medium_box_pct"] = max(0.1, min(80.0, float(policy["medium_box_pct"])))
    policy["high_zone_seconds"] = max(1, min(120, int(policy["high_zone_seconds"])))
    return policy


def get_threat_policy() -> dict:
    """Load the saved threat policy, falling back to defaults if needed."""
    raw = database.get_setting(init_db_conn(), "threat_policy", "")
    try:
        return normalize_threat_policy(json.loads(raw) if raw else {})
    except Exception:
        return dict(DEFAULT_THREAT_POLICY)


def set_threat_policy(data: dict) -> dict:
    """Validate and save the central threat policy."""
    policy = normalize_threat_policy(data)
    database.set_setting(init_db_conn(), "threat_policy", json.dumps(policy))
    return policy


def _save_uploaded_clip(upload, source: str, event_id: str = "") -> str:
    """Save an uploaded event clip with a clean timestamped filename."""
    safe_source = "".join([c for c in source if c.isalnum() or c in ("_", "-")])[:32] or "NODE"
    safe_event = "".join([c for c in event_id if c.isalnum() or c in ("_", "-")])[:32]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = f"{ts}_{safe_source}"
    if safe_event:
        name += f"_E{safe_event}"
    filename = secure_filename(name + ".webm")
    upload.save(os.path.join(CLIPS_DIR, filename))
    return filename


# ---- Page routes ----

@app.route("/")
def index():
    """Serve the central dashboard at the root URL."""
    return admin_page_or_login("admin_dashboard.html")


@app.route("/index.html")
def serve_index():
    """Serve the central dashboard at /index.html."""
    return admin_page_or_login("admin_dashboard.html")


@app.route("/login.html")
def serve_login():
    """Serve the central admin login page."""
    return send_from_directory(STATIC_DIR, "login.html")


@app.route("/register.html")
def serve_register():
    """Redirect unused central registration page requests to login."""
    return redirect("/login.html")


@app.route("/users.html")
def serve_users():
    """Serve the admin user-management page."""
    return admin_page_or_login("users.html")


@app.route("/admin_monitor.html")
def serve_admin_monitor():
    """Serve the live central camera monitor page."""
    return admin_page_or_login("admin_monitor.html")


@app.route("/model_manager.html")
def serve_model_manager():
    """Serve the admin model and threat-policy page."""
    return admin_page_or_login("model_manager.html")


@app.route("/admin_logs.html")
def serve_admin_logs():
    """Serve the central detection log review page."""
    return admin_page_or_login("admin_logs.html")


@app.route("/favicon.ico")
def favicon():
    """Return favicon when present, otherwise no content."""
    ico_path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(ico_path):
        return send_from_directory(STATIC_DIR, "favicon.ico")
    return ("", 204)


# ---- Authentication and user management APIs ----

@app.route("/api/auth/register", methods=["POST"])
def api_register():
    """Create a normal client user when registration is enabled."""
    if not client_registration_enabled():
        return jsonify({"error": "client registration is disabled"}), 403

    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    conn = init_db_conn()
    if database.get_user_by_username(conn, username):
        return jsonify({"error": "username already exists"}), 400

    user_id = database.insert_user(conn, username, generate_password_hash(password), role="user")
    session["user_id"] = user_id
    return jsonify({"ok": True, "user_id": user_id})


@app.route("/api/auth/register/status", methods=["GET"])
def api_register_status():
    """Return whether client self-registration is enabled."""
    return jsonify({"ok": True, "enabled": client_registration_enabled()})


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    """Authenticate a central user and create a Flask session."""
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    conn = init_db_conn()
    user = database.get_user_by_username(conn, username)
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "invalid credentials"}), 401

    session["user_id"] = user["id"]
    database.update_last_login(conn, user["id"])
    mark_user_activity(user)
    return jsonify({"ok": True, "user": {"id": user["id"], "username": user["username"], "role": user.get("role")}})


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    """Clear the current central session."""
    user = current_user()
    clear_user_activity(user)
    session.pop("user_id", None)
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def api_me():
    """Return the current central user session, if any."""
    user = current_user()
    if not user:
        return jsonify({"user": None})
    mark_user_activity(user)
    return jsonify({"user": {"id": user["id"], "username": user["username"], "role": user.get("role")}})


@app.route("/api/users", methods=["GET"])
@admin_required
def api_users_list():
    """Return all users for the admin user-management page."""
    return jsonify({"ok": True, "users": database.list_users(init_db_conn())})


@app.route("/api/admin/settings", methods=["GET"])
@admin_required
def api_admin_settings_get():
    """Return central admin settings used by the user page."""
    return jsonify(
        {
            "ok": True,
            "settings": {
                "client_registration_enabled": client_registration_enabled(),
            },
        }
    )


@app.route("/api/admin/settings", methods=["POST"])
@admin_required
def api_admin_settings_update():
    """Update central admin settings from the user page."""
    data = request.get_json(force=True, silent=True) or {}
    enabled = bool(data.get("client_registration_enabled"))
    database.set_setting(init_db_conn(), "client_registration_enabled", "1" if enabled else "0")
    return jsonify(
        {
            "ok": True,
            "settings": {
                "client_registration_enabled": enabled,
            },
        }
    )


@app.route("/api/users/create", methods=["POST"])
@admin_required
def api_users_create():
    """Create a user account from the central admin page."""
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = (data.get("role") or "user").strip()
    if not username or not password:
        return jsonify({"ok": False, "error": "username and password required"}), 400

    conn = init_db_conn()
    if database.get_user_by_username(conn, username):
        return jsonify({"ok": False, "error": "username already exists"}), 400

    new_id = database.insert_user(conn, username, generate_password_hash(password), role=role)
    return jsonify({"ok": True, "id": new_id})


@app.route("/api/users/update", methods=["POST"])
@admin_required
def api_users_update():
    """Update username or role for an existing central user."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        user_id = int(data.get("id"))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    username = (data.get("username") or "").strip()
    role = (data.get("role") or "user").strip()
    if not username:
        return jsonify({"ok": False, "error": "username required"}), 400

    database.update_user(init_db_conn(), user_id, username, role)
    return jsonify({"ok": True})


@app.route("/api/users/set_password", methods=["POST"])
@admin_required
def api_users_set_password():
    """Set a new password for an existing central user."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        user_id = int(data.get("id"))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    password = data.get("password") or ""
    if not password:
        return jsonify({"ok": False, "error": "password required"}), 400

    database.set_user_password_hash(init_db_conn(), user_id, generate_password_hash(password))
    return jsonify({"ok": True})


@app.route("/api/users/delete", methods=["POST"])
@admin_required
def api_users_delete():
    """Delete a central user account."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        user_id = int(data.get("id"))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    database.delete_user(init_db_conn(), user_id)
    return jsonify({"ok": True})


# ---- Log and clip APIs used by the admin pages ----

@app.route("/api/logs", methods=["GET"])
@admin_required
def api_logs():
    """Return central event logs for the admin log page."""
    try:
        limit = int(request.args.get("limit", 500))
    except Exception:
        limit = 500
    return jsonify(database.list_logs(init_db_conn(), limit=limit))


@app.route("/api/logs/update", methods=["POST"])
@admin_required
def api_logs_update():
    """Update one central event log row."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        log_id = int(data.get("id"))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    database.update_log(
        init_db_conn(),
        log_id,
        str(data.get("time", "") or ""),
        str(data.get("event", "") or ""),
        str(data.get("source", "") or ""),
        str(data.get("clip", "") or ""),
    )
    return jsonify({"ok": True})


@app.route("/api/logs/delete", methods=["POST"])
@admin_required
def api_logs_delete():
    """Delete one central event log row."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        log_id = int(data.get("id"))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    database.delete_log(init_db_conn(), log_id)
    return jsonify({"ok": True})


@app.route("/api/clip/download", methods=["GET"])
@admin_required
def api_clip_download():
    """Download an uploaded event clip from the central server."""
    filename = secure_filename(request.args.get("file", "") or "")
    if not filename:
        return jsonify({"error": "missing file"}), 400
    full = os.path.join(CLIPS_DIR, filename)
    if not os.path.isfile(full):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(CLIPS_DIR, filename, as_attachment=True)


@app.route("/api/clip/view", methods=["GET"])
@admin_required
def api_clip_view():
    """Stream an uploaded event clip for browser playback."""
    filename = secure_filename(request.args.get("file", "") or "")
    if not filename:
        return jsonify({"error": "missing file"}), 400
    full = os.path.join(CLIPS_DIR, filename)
    if not os.path.isfile(full):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(CLIPS_DIR, filename, as_attachment=False, mimetype="video/webm")


# ---- Camera registration and live monitor APIs ----

@app.route("/api/camera/register", methods=["POST"])
@login_required
def api_camera_register():
    """Register a client camera with the central server."""
    data = request.get_json(force=True, silent=True) or {}
    camera_name = (data.get("camera_name") or "").strip()
    camera_id = (data.get("camera_id") or "").strip()
    if not camera_name or not camera_id:
        return jsonify({"error": "camera_name and camera_id required"}), 400

    user_id = session.get("user_id")
    try:
        cam_db_id = database.register_camera(init_db_conn(), user_id, camera_name, camera_id)
        return jsonify({"ok": True, "camera_db_id": cam_db_id})
    except Exception:
        existing = next((item for item in database.get_user_cameras(init_db_conn(), user_id) if item["camera_id"] == camera_id), None)
        if existing:
            return jsonify({"ok": True, "camera_db_id": existing["id"], "existing": True})
        return jsonify({"error": "registration failed"}), 500


@app.route("/api/admin/cameras", methods=["GET"])
@admin_required
def api_admin_cameras():
    """Return cameras that are currently live for the monitor page."""
    cameras = database.get_all_active_cameras(init_db_conn())
    live_lookup = {item["camera_id"]: item for item in active_camera_entries()}
    live_cameras = []
    for camera in cameras:
        # Only show cameras that have sent a recent status/frame update. This
        # keeps the monitor page focused on currently available client streams.
        live = live_lookup.get(str(camera["internal_id"]))
        if not live:
            continue
        merged = dict(camera)
        merged["last_seen_at"] = live["timestamp"]
        merged["is_detecting"] = live["is_detecting"]
        live_cameras.append(merged)
    return jsonify({"ok": True, "cameras": live_cameras})


@app.route("/api/admin/summary", methods=["GET"])
@admin_required
def api_admin_summary():
    """Return dashboard counts for users, logs, and cameras."""
    conn = init_db_conn()
    cameras = database.get_all_active_cameras(conn)
    live_lookup = {item["camera_id"]: item for item in active_camera_entries()}
    active_camera_count = sum(1 for camera in cameras if str(camera["internal_id"]) in live_lookup)
    detecting_camera_count = sum(
        1
        for camera in cameras
        if (entry := live_lookup.get(str(camera["internal_id"]))) and entry.get("is_detecting")
    )
    active = active_users_summary(conn)
    return jsonify(
        {
            "ok": True,
            "summary": {
                "total_users": database.count_users(conn),
                "active_users": len(active),
                "stored_logs": database.count_logs(conn),
                "registered_cameras": database.count_active_cameras(conn),
                "active_cameras": active_camera_count,
                "detecting_cameras": detecting_camera_count,
                "active_usernames": [item["username"] for item in active],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        }
    )


@app.route("/api/admin/monitor/heartbeat", methods=["POST"])
@admin_required
def api_admin_monitor_heartbeat():
    """Record that an admin monitor page is actively open."""
    payload = request.get_json(force=True, silent=True) or {}
    viewer_id = str(payload.get("viewer_id") or "").strip()
    if not viewer_id:
        return jsonify({"ok": False, "error": "viewer_id required"}), 400
    mark_monitor_viewer(viewer_id)
    return jsonify({"ok": True, "active_viewers": active_monitor_viewers()})


@app.route("/api/admin/monitor/status", methods=["GET"])
@login_required
def api_admin_monitor_status():
    """Tell clients whether any admin monitor page is active."""
    viewers = active_monitor_viewers()
    return jsonify({"ok": True, "active": viewers > 0, "active_viewers": viewers})


@app.route("/api/node/heartbeat", methods=["POST"])
@login_required
def api_node_heartbeat():
    """Record that a client node is still connected."""
    user = current_user()
    if user and user.get("role") != "admin":
        mark_client_activity(user)
    return jsonify({"ok": True, "timestamp": datetime.now().isoformat(timespec="seconds")})


@app.route("/api/camera/status", methods=["POST"])
@login_required
def api_camera_status():
    """Receive live detecting/idle status for one client camera."""
    payload = request.get_json(force=True, silent=True) or {}
    camera_id = str(payload.get("camera_id") or "").strip()
    if not camera_id:
        return jsonify({"ok": False, "error": "camera_id required"}), 400
    mark_camera_activity(
        camera_id,
        user_id=session.get("user_id"),
        is_detecting=bool(payload.get("is_detecting")),
    )
    return jsonify({"ok": True, "timestamp": datetime.now().isoformat(timespec="seconds")})


@app.route("/api/camera/stream", methods=["POST"])
@login_required
def api_camera_stream():
    """Receive the latest monitor frame for one client camera."""
    if "file" not in request.files:
        return jsonify({"error": "missing file"}), 400
    upload = request.files["file"]
    if not upload or upload.filename == "":
        return jsonify({"error": "empty file"}), 400

    camera_id = request.form.get("camera_id", "")
    if not camera_id:
        return jsonify({"error": "camera_id required"}), 400

    # Frames are kept in memory because the monitor only needs the latest image.
    # Saving every JPEG would create unnecessary files during long demonstrations.
    _frame_cache[f"frame_{camera_id}"] = {
        "data": upload.read(),
        "user_id": session.get("user_id"),
        "timestamp": datetime.now().isoformat(),
        "is_detecting": str(request.form.get("is_detecting", "")).lower() in ("1", "true", "yes", "on"),
    }
    mark_camera_activity(
        camera_id,
        user_id=session.get("user_id"),
        is_detecting=str(request.form.get("is_detecting", "")).lower() in ("1", "true", "yes", "on"),
    )
    return jsonify({"ok": True})


@app.route("/api/admin/camera/frame", methods=["GET"])
@admin_required
def api_admin_camera_frame():
    """Return the latest cached frame for a monitor camera card."""
    camera_id = request.args.get("camera_id", "")
    if not camera_id:
        return jsonify({"error": "camera_id required"}), 400
    frame_info = _frame_cache.get(f"frame_{camera_id}")
    if not frame_info:
        return jsonify({"error": "no frame available"}), 404
    return send_file(io.BytesIO(frame_info["data"]), mimetype="image/jpeg")


# ---- Client-node sync API ----

@app.route("/api/node/upload_event", methods=["POST"])
@login_required
def api_node_upload_event():
    """Receive a client-node event log and optional clip during synchronisation."""
    payload_json = request.form.get("payload", "") or ""
    if not payload_json:
        return jsonify({"ok": False, "error": "missing payload"}), 400

    try:
        payload = json.loads(payload_json)
    except Exception:
        return jsonify({"ok": False, "error": "invalid payload"}), 400

    clip_name = str(payload.get("clip", "") or "")
    clip_file = request.files.get("file")
    if clip_file and clip_file.filename:
        # When a client sends the clip file, replace the local placeholder text
        # with the server-side filename used by the admin log viewer.
        clip_name = f"Saved: {_save_uploaded_clip(clip_file, payload.get('source', 'NODE'), str(payload.get('local_id', '')))}"

    conn = init_db_conn()
    central_log_id = payload.get("central_log_id")
    try:
        # central_log_id lets a client update a previously synced row instead
        # of creating duplicate central log entries.
        central_log_id = int(central_log_id) if central_log_id not in (None, "", 0, "0") else None
    except Exception:
        central_log_id = None

    if central_log_id and database.get_log_by_id(conn, central_log_id):
        database.update_log(
            conn,
            central_log_id,
            str(payload.get("time", "") or ""),
            str(payload.get("event", "") or ""),
            str(payload.get("source", "") or ""),
            clip_name,
        )
        return jsonify({"ok": True, "id": central_log_id, "updated": True})

    new_id = database.create_log(
        conn,
        str(payload.get("time", "") or ""),
        str(payload.get("event", "") or ""),
        str(payload.get("source", "") or ""),
        clip_name,
        sync_status="synced",
    )
    return jsonify({"ok": True, "id": new_id, "created": True})


# ---- Model release and threat-policy APIs ----

def _save_model_release_upload():
    """Store an uploaded YOLO model and mark it as the active release."""
    if "file" not in request.files:
        return None, (jsonify({"ok": False, "error": "missing file"}), 400)

    upload = request.files["file"]
    if not upload or upload.filename == "":
        return None, (jsonify({"ok": False, "error": "empty file"}), 400)

    version = (request.form.get("version") or "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    notes = (request.form.get("notes") or "").strip()
    filename = secure_filename(f"{version}_{upload.filename}")
    upload.save(os.path.join(MODELS_DIR, filename))

    release_id = database.create_model_release(init_db_conn(), version, filename, notes)
    release = database.get_active_model_release(init_db_conn()) or {
        "id": release_id,
        "version": version,
        "filename": filename,
        "released_at": datetime.now().isoformat(timespec="seconds"),
    }
    return release, None


def _serialize_model_release(release: dict) -> dict:
    """Convert a model-release database row into an API response."""
    return {
        "id": release["id"],
        "version": release["version"],
        "filename": release["filename"],
        "released_at": release["released_at"],
        "released_by": current_user().get("username", "admin") if current_user() else "admin",
        "size_bytes": os.path.getsize(os.path.join(MODELS_DIR, release["filename"])),
    }


@app.route("/api/models/release", methods=["POST"])
@admin_required
def api_models_release():
    """Upload and activate a new central YOLO model release."""
    release, error_response = _save_model_release_upload()
    if error_response:
        return error_response
    return jsonify({"ok": True, "model": _serialize_model_release(release)})


@app.route("/api/models/current", methods=["GET"])
@login_required
def api_models_current():
    """Return metadata for the active central model release."""
    release = database.get_active_model_release(init_db_conn())
    if not release:
        return jsonify({"ok": False, "error": "no released model"}), 404
    return jsonify({"ok": True, "model": _serialize_model_release(release)})


@app.route("/api/threat-policy", methods=["GET"])
@login_required
def api_threat_policy_get():
    """Return the central threat policy for clients/admin UI."""
    return jsonify({"ok": True, "policy": get_threat_policy()})


@app.route("/api/threat-policy", methods=["POST"])
@admin_required
def api_threat_policy_update():
    """Update the central threat policy from the admin model page."""
    data = request.get_json(force=True, silent=True) or {}
    return jsonify({"ok": True, "policy": set_threat_policy(data)})


@app.route("/api/models/download/current", methods=["GET"])
@login_required
def api_models_download_current():
    """Download the active central model file for client nodes."""
    release = database.get_active_model_release(init_db_conn())
    if not release:
        return jsonify({"ok": False, "error": "no released model"}), 404
    return send_from_directory(MODELS_DIR, release["filename"], as_attachment=True)


# ---- Application entry point ----

def run_server(host: str = "0.0.0.0", port: int = 5000, debug: bool = False):
    """Initialise state and run the central Flask server."""
    init_server_state()
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run_server(port=int(os.environ.get("PORT", 5000)), debug=False)
