import io
import os
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_file, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import database

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("CENTRAL_DB_PATH", str(BASE_DIR / "data" / "central_server.db"))
STATIC_DIR = str(BASE_DIR / "static")
CLIPS_DIR = os.environ.get("CENTRAL_CLIPS_DIR", str(BASE_DIR / "clips"))
MODELS_DIR = os.environ.get("CENTRAL_MODELS_DIR", str(BASE_DIR / "models"))

os.makedirs(CLIPS_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app, supports_credentials=True)

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
FRAME_LIVE_WINDOW_SECONDS = int(os.environ.get("FRAME_LIVE_WINDOW_SECONDS", "5"))
ACTIVE_USER_WINDOW_SECONDS = int(os.environ.get("ACTIVE_USER_WINDOW_SECONDS", "300"))
MONITOR_ACTIVE_WINDOW_SECONDS = int(os.environ.get("MONITOR_ACTIVE_WINDOW_SECONDS", "10"))
CAMERA_ACTIVE_WINDOW_SECONDS = int(os.environ.get("CAMERA_ACTIVE_WINDOW_SECONDS", "10"))


def init_db_conn():
    global _db_conn
    if _db_conn is None:
        print("Initializing central DB:", DB_PATH)
        _db_conn = database.init_db(DB_PATH)
    return _db_conn


def init_server_state():
    init_db_conn()


def login_required(func):
    @wraps(func)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "authentication required"}), 401
        mark_user_activity(current_user())
        return func(*args, **kwargs)

    return decorated


def admin_required(func):
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


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return database.get_user_by_id(init_db_conn(), uid)


def current_admin_user():
    user = current_user()
    if not user or user.get("role") != "admin":
        return None
    return user


def mark_user_activity(user):
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
    if not user:
        return
    _client_activity[str(user["id"])] = {
        "id": user["id"],
        "username": user.get("username", ""),
        "role": user.get("role", "user"),
        "timestamp": datetime.now().isoformat(),
    }


def active_client_users():
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
    if not camera_id:
        return
    _camera_activity[str(camera_id)] = {
        "camera_id": str(camera_id),
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
        "is_detecting": bool(is_detecting),
    }


def active_camera_entries():
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
    if not viewer_id:
        return
    _monitor_viewers[viewer_id] = datetime.now().isoformat()


def active_monitor_viewers() -> int:
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
    if not current_admin_user():
        return redirect("/login.html")
    return send_from_directory(STATIC_DIR, filename)


def _save_uploaded_clip(upload, source: str, event_id: str = "") -> str:
    safe_source = "".join([c for c in source if c.isalnum() or c in ("_", "-")])[:32] or "NODE"
    safe_event = "".join([c for c in event_id if c.isalnum() or c in ("_", "-")])[:32]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = f"{ts}_{safe_source}"
    if safe_event:
        name += f"_E{safe_event}"
    filename = secure_filename(name + ".webm")
    upload.save(os.path.join(CLIPS_DIR, filename))
    return filename


@app.route("/")
def index():
    return admin_page_or_login("admin_dashboard.html")


@app.route("/index.html")
def serve_index():
    return admin_page_or_login("admin_dashboard.html")


@app.route("/login.html")
def serve_login():
    return send_from_directory(STATIC_DIR, "login.html")


@app.route("/register.html")
def serve_register():
    return send_from_directory(STATIC_DIR, "register.html")


@app.route("/users.html")
def serve_users():
    return admin_page_or_login("users.html")


@app.route("/admin_monitor.html")
def serve_admin_monitor():
    return admin_page_or_login("admin_monitor.html")


@app.route("/model_manager.html")
def serve_model_manager():
    return admin_page_or_login("model_manager.html")


@app.route("/admin_logs.html")
def serve_admin_logs():
    return admin_page_or_login("admin_logs.html")


@app.route("/favicon.ico")
def favicon():
    ico_path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(ico_path):
        return send_from_directory(STATIC_DIR, "favicon.ico")
    return ("", 204)


@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    conn = init_db_conn()
    if database.get_user_by_username(conn, username):
        return jsonify({"error": "username already exists"}), 400

    user_id = database.insert_user(conn, username, generate_password_hash(password))
    session["user_id"] = user_id
    return jsonify({"ok": True, "user_id": user_id})


@app.route("/api/auth/login", methods=["POST"])
def api_login():
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
    session.pop("user_id", None)
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def api_me():
    user = current_user()
    if not user:
        return jsonify({"user": None})
    mark_user_activity(user)
    return jsonify({"user": {"id": user["id"], "username": user["username"], "role": user.get("role")}})


@app.route("/api/users", methods=["GET"])
@admin_required
def api_users_list():
    return jsonify({"ok": True, "users": database.list_users(init_db_conn())})


@app.route("/api/users/create", methods=["POST"])
@admin_required
def api_users_create():
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
    data = request.get_json(force=True, silent=True) or {}
    try:
        user_id = int(data.get("id"))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    database.delete_user(init_db_conn(), user_id)
    return jsonify({"ok": True})


@app.route("/api/logs", methods=["GET"])
@admin_required
def api_logs():
    try:
        limit = int(request.args.get("limit", 500))
    except Exception:
        limit = 500
    return jsonify(database.list_logs(init_db_conn(), limit=limit))


@app.route("/api/logs/create", methods=["POST"])
@login_required
def api_logs_create():
    data = request.get_json(force=True, silent=True) or {}
    new_id = database.create_log(
        init_db_conn(),
        str(data.get("time", "") or ""),
        str(data.get("event", "") or ""),
        str(data.get("source", "") or ""),
        str(data.get("clip", "") or ""),
        sync_status="synced",
    )
    return jsonify({"ok": True, "id": new_id})


@app.route("/api/logs/update", methods=["POST"])
@admin_required
def api_logs_update():
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
    filename = secure_filename(request.args.get("file", "") or "")
    if not filename:
        return jsonify({"error": "missing file"}), 400
    full = os.path.join(CLIPS_DIR, filename)
    if not os.path.isfile(full):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(CLIPS_DIR, filename, as_attachment=False, mimetype="video/webm")


@app.route("/api/clip/save", methods=["POST"])
@login_required
def api_clip_save():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "missing file"}), 400

    upload = request.files["file"]
    if not upload or upload.filename == "":
        return jsonify({"ok": False, "error": "empty file"}), 400

    source = request.form.get("source", "NODE")
    event_id = request.form.get("event_id", "")
    filename = _save_uploaded_clip(upload, source, event_id)
    return jsonify({"ok": True, "filename": filename})


@app.route("/api/camera/register", methods=["POST"])
@login_required
def api_camera_register():
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


@app.route("/api/camera/list", methods=["GET"])
@login_required
def api_camera_list():
    user_id = session.get("user_id")
    return jsonify({"ok": True, "cameras": database.get_user_cameras(init_db_conn(), user_id)})


@app.route("/api/admin/cameras", methods=["GET"])
@admin_required
def api_admin_cameras():
    cameras = database.get_all_active_cameras(init_db_conn())
    live_lookup = {item["camera_id"]: item for item in active_camera_entries()}
    live_cameras = []
    for camera in cameras:
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
    payload = request.get_json(force=True, silent=True) or {}
    viewer_id = str(payload.get("viewer_id") or "").strip()
    if not viewer_id:
        return jsonify({"ok": False, "error": "viewer_id required"}), 400
    mark_monitor_viewer(viewer_id)
    return jsonify({"ok": True, "active_viewers": active_monitor_viewers()})


@app.route("/api/admin/monitor/status", methods=["GET"])
@login_required
def api_admin_monitor_status():
    viewers = active_monitor_viewers()
    return jsonify({"ok": True, "active": viewers > 0, "active_viewers": viewers})


@app.route("/api/node/heartbeat", methods=["POST"])
@login_required
def api_node_heartbeat():
    user = current_user()
    if user and user.get("role") != "admin":
        mark_client_activity(user)
    return jsonify({"ok": True, "timestamp": datetime.now().isoformat(timespec="seconds")})


@app.route("/api/camera/status", methods=["POST"])
@login_required
def api_camera_status():
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
    if "file" not in request.files:
        return jsonify({"error": "missing file"}), 400
    upload = request.files["file"]
    if not upload or upload.filename == "":
        return jsonify({"error": "empty file"}), 400

    camera_id = request.form.get("camera_id", "")
    if not camera_id:
        return jsonify({"error": "camera_id required"}), 400

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
    camera_id = request.args.get("camera_id", "")
    if not camera_id:
        return jsonify({"error": "camera_id required"}), 400
    frame_info = _frame_cache.get(f"frame_{camera_id}")
    if not frame_info:
        return jsonify({"error": "no frame available"}), 404
    return send_file(io.BytesIO(frame_info["data"]), mimetype="image/jpeg")


@app.route("/api/node/upload_event", methods=["POST"])
@login_required
def api_node_upload_event():
    payload_json = request.form.get("payload", "") or ""
    if not payload_json:
        return jsonify({"ok": False, "error": "missing payload"}), 400

    import json

    try:
        payload = json.loads(payload_json)
    except Exception:
        return jsonify({"ok": False, "error": "invalid payload"}), 400

    clip_name = str(payload.get("clip", "") or "")
    clip_file = request.files.get("file")
    if clip_file and clip_file.filename:
        clip_name = f"Saved: {_save_uploaded_clip(clip_file, payload.get('source', 'NODE'), str(payload.get('local_id', '')))}"

    conn = init_db_conn()
    new_id = database.create_log(
        conn,
        str(payload.get("time", "") or ""),
        str(payload.get("event", "") or ""),
        str(payload.get("source", "") or ""),
        clip_name,
        sync_status="synced",
    )
    return jsonify({"ok": True, "id": new_id})


@app.route("/api/models/releases", methods=["GET"])
@admin_required
def api_models_releases():
    return jsonify({"ok": True, "items": database.list_model_releases(init_db_conn())})


def _save_model_release_upload():
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
    release, error_response = _save_model_release_upload()
    if error_response:
        return error_response
    return jsonify({"ok": True, "model": _serialize_model_release(release)})


@app.route("/api/models/current", methods=["GET"])
@login_required
def api_models_current():
    release = database.get_active_model_release(init_db_conn())
    if not release:
        return jsonify({"ok": False, "error": "no released model"}), 404
    return jsonify({"ok": True, "model": _serialize_model_release(release)})


@app.route("/api/models/download/<filename>", methods=["GET"])
@login_required
def api_models_download(filename: str):
    safe_name = secure_filename(filename)
    full = os.path.join(MODELS_DIR, safe_name)
    if not os.path.isfile(full):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(MODELS_DIR, safe_name, as_attachment=True)


@app.route("/api/models/download/current", methods=["GET"])
@login_required
def api_models_download_current():
    release = database.get_active_model_release(init_db_conn())
    if not release:
        return jsonify({"ok": False, "error": "no released model"}), 404
    return send_from_directory(MODELS_DIR, release["filename"], as_attachment=True)


def run_server(host: str = "0.0.0.0", port: int = 5000, debug: bool = False):
    init_server_state()
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run_server(port=int(os.environ.get("PORT", 5000)), debug=True)
