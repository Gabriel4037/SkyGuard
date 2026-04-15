import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request, send_file, send_from_directory, session
from flask_cors import CORS
from werkzeug.utils import secure_filename

import database
import detector_runtime
from client_service import DetectorNodeService


STATIC_DIR = detector_runtime.resource_path("static")
LOCAL_DB_PATH = os.environ.get("DETECTOR_NODE_DB", detector_runtime.resource_path("data/detector_node.db"))
LOCAL_CLIPS_DIR = os.environ.get("DETECTOR_NODE_CLIPS_DIR", detector_runtime.resource_path("clips"))
SETTINGS_PATH = Path(detector_runtime.resource_path("data/client_settings.json"))
CONNECTION_PATH = Path(detector_runtime.resource_path("data/client_connection.json"))
SYNC_INTERVAL_SECONDS = max(30, int(os.environ.get("DETECTOR_SYNC_INTERVAL_SECONDS", "30")))
DEFAULT_CENTRAL_SERVER_URL = os.environ.get("CENTRAL_SERVER_URL", "http://127.0.0.1:5000").rstrip("/")
BACKGROUND_TICK_SECONDS = 5
HEARTBEAT_INTERVAL_SECONDS = 15

DEFAULT_SETTINGS = {
    "fps": 6,
    "conf": 0.4,
    "max_dim": 640,
    "auto_clip": True,
    "clip_mode": "event",
    "clip_sec": 8,
    "model_check_interval_seconds": 30,
}

os.makedirs(LOCAL_CLIPS_DIR, exist_ok=True)
os.makedirs(SETTINGS_PATH.parent, exist_ok=True)
os.makedirs(CONNECTION_PATH.parent, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app, supports_credentials=True)
app.secret_key = os.environ.get("DETECTOR_NODE_SECRET_KEY", "detector-node-secret")
app.config.update(
    SESSION_COOKIE_NAME="detector_session",
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=86400,
)

_db_conn = None
_sync_lock = threading.Lock()
_background_started = False
_state_lock = threading.RLock()
_detector_client = DetectorNodeService(
    db_conn=None,
    server_url=DEFAULT_CENTRAL_SERVER_URL,
    clips_dir=LOCAL_CLIPS_DIR,
)
_runtime_state = {
    "settings": {},
    "server_ip": "",
    "server_url": DEFAULT_CENTRAL_SERVER_URL,
    "monitor_active": False,
    "monitor_active_viewers": 0,
    "last_monitor_status_at": None,
    "active_detectors": set(),
    "pending_model": None,
    "last_model_check_at": None,
    "last_sync_at": None,
    "last_heartbeat_at": None,
    "model_message": "",
}


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            settings.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return settings


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def normalize_server_ip(server_ip: str) -> str:
    value = (server_ip or "").strip().rstrip("/")
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if ":" in value:
        return f"http://{value}"
    return f"http://{value}:5000"


def extract_server_ip(server_url: str) -> str:
    value = (server_url or "").strip().rstrip("/")
    if value.startswith("http://"):
        value = value[len("http://") :]
    elif value.startswith("https://"):
        value = value[len("https://") :]
    if value.endswith(":5000"):
        value = value[:-5]
    return value.rstrip("/")


def load_connection() -> dict:
    if CONNECTION_PATH.exists():
        try:
            payload = json.loads(CONNECTION_PATH.read_text(encoding="utf-8"))
            server_ip = str(payload.get("server_ip") or "").strip()
            server_url = normalize_server_ip(server_ip) or normalize_server_ip(str(payload.get("server_url") or ""))
            if server_url:
                return {"server_ip": extract_server_ip(server_url), "server_url": server_url}
        except Exception:
            pass
    return {
        "server_ip": extract_server_ip(DEFAULT_CENTRAL_SERVER_URL),
        "server_url": DEFAULT_CENTRAL_SERVER_URL,
    }


def save_connection(server_ip: str, server_url: str) -> None:
    CONNECTION_PATH.write_text(
        json.dumps({"server_ip": server_ip, "server_url": server_url}, indent=2),
        encoding="utf-8",
    )


def get_server_url() -> str:
    with _state_lock:
        return str(_runtime_state.get("server_url") or "").rstrip("/")


def get_server_ip() -> str:
    with _state_lock:
        return str(_runtime_state.get("server_ip") or "")


def set_server_connection(server_ip: str) -> dict:
    server_url = normalize_server_ip(server_ip)
    normalized_ip = extract_server_ip(server_url)
    with _state_lock:
        _runtime_state["server_ip"] = normalized_ip
        _runtime_state["server_url"] = server_url
    _detector_client.server_url = server_url
    save_connection(normalized_ip, server_url)
    return {"server_ip": normalized_ip, "server_url": server_url}


def test_server_connection(server_url: str) -> dict:
    import requests

    target = (server_url or "").rstrip("/")
    if not target:
        return {"ok": False, "error": "server address required"}

    try:
        response = requests.get(f"{target}/api/auth/me", timeout=5)
        if response.ok:
            return {"ok": True}
        return {"ok": False, "error": f"server returned {response.status_code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_settings(settings: dict) -> dict:
    normalized = dict(DEFAULT_SETTINGS)
    normalized.update(settings or {})
    normalized["fps"] = max(1, min(15, int(normalized["fps"])))
    normalized["conf"] = max(0.05, min(0.95, float(normalized["conf"])))
    normalized["max_dim"] = max(320, min(1280, int(normalized["max_dim"])))
    normalized["clip_sec"] = max(3, min(60, int(normalized["clip_sec"])))
    normalized["model_check_interval_seconds"] = max(10, min(3600, int(normalized["model_check_interval_seconds"])))
    normalized["auto_clip"] = bool(normalized.get("auto_clip", True))
    normalized["clip_mode"] = "fixed" if normalized.get("clip_mode") == "fixed" else "event"

    _detector_client.update_settings(
        conf=normalized["conf"],
        max_dim=normalized["max_dim"],
    )

    with _state_lock:
        _runtime_state["settings"] = normalized
    save_settings(normalized)
    return normalized


def init_runtime_state() -> None:
    connection = load_connection()
    set_server_connection(connection["server_ip"])
    settings = apply_settings(load_settings())
    current_model = detector_runtime.get_loaded_model_info()
    with _state_lock:
        _runtime_state["settings"] = settings
        _runtime_state["model_message"] = (
            f"Runtime ready on model {current_model.get('version', current_model.get('filename', 'unknown'))}"
            if current_model
            else "Runtime ready"
        )


def current_model_status() -> dict:
    with _state_lock:
        return {
            "current": detector_runtime.get_loaded_model_info(),
            "pending": dict(_runtime_state["pending_model"]) if _runtime_state["pending_model"] else None,
            "last_model_check_at": _runtime_state["last_model_check_at"],
            "last_sync_at": _runtime_state["last_sync_at"],
            "message": _runtime_state["model_message"],
            "active_detectors": sorted(_runtime_state["active_detectors"]),
            "idle": len(_runtime_state["active_detectors"]) == 0,
        }


def refresh_monitor_status(force: bool = False) -> dict:
    if not get_server_url():
        return {"ok": False, "error": "CENTRAL_SERVER_URL not configured"}

    with _state_lock:
        last_check = _runtime_state.get("last_monitor_status_at")
        cached_active = bool(_runtime_state.get("monitor_active"))
        cached_viewers = int(_runtime_state.get("monitor_active_viewers") or 0)

    if not force and last_check:
        try:
            if (datetime.now() - datetime.fromisoformat(str(last_check))).total_seconds() < 3:
                return {"ok": True, "active": cached_active, "active_viewers": cached_viewers, "cached": True}
        except Exception:
            pass

    try:
        if not _detector_client.login():
            return {"ok": False, "error": "central login failed"}
        response = _detector_client.session.get(f"{get_server_url()}/api/admin/monitor/status", timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    active = bool(payload.get("active"))
    viewers = int(payload.get("active_viewers") or 0)
    with _state_lock:
        _runtime_state["monitor_active"] = active
        _runtime_state["monitor_active_viewers"] = viewers
        _runtime_state["last_monitor_status_at"] = datetime.now().isoformat(timespec="seconds")
    return {"ok": True, "active": active, "active_viewers": viewers}


def set_model_message(message: str) -> None:
    with _state_lock:
        _runtime_state["model_message"] = message
    print(message)


def is_client_idle() -> bool:
    with _state_lock:
        return len(_runtime_state["active_detectors"]) == 0


def apply_pending_model_if_idle(force: bool = False) -> Optional[dict]:
    with _state_lock:
        pending = dict(_runtime_state["pending_model"]) if _runtime_state["pending_model"] else None

    if not pending:
        return None
    if not force and not is_client_idle():
        return None

    info = detector_runtime.reload_model(pending["path"], version=pending.get("version"))
    with _state_lock:
        _runtime_state["pending_model"] = None
    set_model_message(f"Applied model {info.get('version', info.get('filename', 'unknown'))}")
    return info


def check_for_model_update(force: bool = False) -> dict:
    if not get_server_url():
        return {"ok": False, "error": "CENTRAL_SERVER_URL not configured"}

    try:
        if not _detector_client.login():
            return {"ok": False, "error": "central login failed"}
        remote = _detector_client.get_remote_model_info()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    with _state_lock:
        _runtime_state["last_model_check_at"] = datetime.now().isoformat(timespec="seconds")

    if not remote:
        set_model_message("No released model found on server")
        return {"ok": True, "updated": False, "message": "no remote model"}

    if not _detector_client.is_newer_model_available(remote):
        if force:
            set_model_message(f"Client already on latest model {remote.get('version', remote.get('filename', 'unknown'))}")
        return {"ok": True, "updated": False, "model": remote}

    path = _detector_client.download_model(remote)
    pending = {
        "version": remote.get("version"),
        "filename": remote.get("filename"),
        "path": str(path),
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _state_lock:
        _runtime_state["pending_model"] = pending

    if is_client_idle():
        applied = apply_pending_model_if_idle(force=True)
        return {"ok": True, "updated": True, "applied": True, "model": applied}

    set_model_message(f"Downloaded model {pending['version']} and queued apply for next idle moment")
    return {"ok": True, "updated": True, "applied": False, "model": pending}


def init_db_conn():
    global _db_conn
    if _db_conn is None:
        print("Initializing detector-client DB:", LOCAL_DB_PATH)
        _db_conn = database.init_db(LOCAL_DB_PATH)
        _detector_client.db_conn = _db_conn
    return _db_conn


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return database.get_user_by_id(init_db_conn(), uid)


def sync_local_user_record(username: str, password: str, role: str) -> int:
    conn = init_db_conn()
    user = database.get_user_by_username(conn, username)
    from werkzeug.security import generate_password_hash

    if user:
        if user.get("role") != role:
            database.update_user(conn, user["id"], username, role)
        database.set_user_password_hash(conn, user["id"], generate_password_hash(password))
        return user["id"]

    return database.insert_user(conn, username, generate_password_hash(password), role=role)


def node_login_required(handler):
    from functools import wraps

    @wraps(handler)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "authentication required"}), 401
        return handler(*args, **kwargs)

    return wrapped


def node_admin_required(handler):
    from functools import wraps

    @wraps(handler)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "authentication required"}), 401
        if user.get("role") != "admin":
            return jsonify({"error": "admin required"}), 403
        return handler(*args, **kwargs)

    return wrapped


def configure_sync_client(username: str, password: str):
    _detector_client.username = username
    _detector_client.password = password
    _detector_client.server_url = get_server_url()


def try_central_login(username: str, password: str):
    import requests

    response = requests.post(
        f"{get_server_url()}/api/auth/login",
        json={"username": username, "password": password},
        timeout=15,
    )
    if not response.ok:
        return None
    return response.json()


def get_central_registration_status() -> dict:
    import requests

    response = requests.get(
        f"{get_server_url()}/api/auth/register/status",
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return {"ok": True, "enabled": bool(payload.get("enabled"))}


def ensure_central_login() -> bool:
    if not _detector_client.server_url:
        return False
    try:
        return _detector_client.login()
    except Exception:
        return False


def send_client_heartbeat() -> dict:
    if not get_server_url():
        return {"ok": False, "error": "CENTRAL_SERVER_URL not configured"}
    try:
        if not _detector_client.login():
            return {"ok": False, "error": "central login failed"}
        response = _detector_client.session.post(f"{get_server_url()}/api/node/heartbeat", json={}, timeout=10)
        response.raise_for_status()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def run_sync_once() -> dict:
    if not get_server_url():
        return {"ok": False, "error": "CENTRAL_SERVER_URL not configured"}

    with _sync_lock:
        try:
            if not _detector_client.login():
                return {"ok": False, "error": "central login failed"}
            synced_count = _detector_client.sync_pending_logs()
            with _state_lock:
                _runtime_state["last_sync_at"] = datetime.now().isoformat(timespec="seconds")
            return {"ok": True, "synced_logs": synced_count}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def background_sync_loop():
    while True:
        try:
            if _detector_client.server_url and _detector_client.username and _detector_client.password:
                now = time.time()
                with _state_lock:
                    settings = dict(_runtime_state["settings"])
                    last_sync_at = _runtime_state["last_sync_at"]
                    last_model_check_at = _runtime_state["last_model_check_at"]

                last_sync_ts = datetime.fromisoformat(last_sync_at).timestamp() if last_sync_at else 0
                last_check_ts = datetime.fromisoformat(last_model_check_at).timestamp() if last_model_check_at else 0

                last_heartbeat_at = _runtime_state["last_heartbeat_at"]

                if now - last_sync_ts >= SYNC_INTERVAL_SECONDS:
                    result = run_sync_once()
                    if not result.get("ok"):
                        print("Background sync skipped/failed:", result.get("error"))

                last_heartbeat_ts = datetime.fromisoformat(last_heartbeat_at).timestamp() if last_heartbeat_at else 0
                if now - last_heartbeat_ts >= HEARTBEAT_INTERVAL_SECONDS:
                    heartbeat_result = send_client_heartbeat()
                    if heartbeat_result.get("ok"):
                        with _state_lock:
                            _runtime_state["last_heartbeat_at"] = datetime.now().isoformat(timespec="seconds")
                    else:
                        print("Background client heartbeat failed:", heartbeat_result.get("error"))

                if now - last_check_ts >= settings["model_check_interval_seconds"]:
                    result = check_for_model_update(force=False)
                    if not result.get("ok"):
                        print("Background model check failed:", result.get("error"))

                monitor_result = refresh_monitor_status(force=False)
                if not monitor_result.get("ok"):
                    print("Background monitor status check failed:", monitor_result.get("error"))

                apply_pending_model_if_idle(force=False)
        except Exception as exc:
            print("Background client loop error:", exc)
        time.sleep(BACKGROUND_TICK_SECONDS)


def start_background_sync():
    global _background_started
    if _background_started:
        return
    _background_started = True
    thread = threading.Thread(target=background_sync_loop, daemon=True)
    thread.start()


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    if not get_server_url():
        return jsonify({"error": "central server not configured"}), 503

    try:
        payload = try_central_login(username, password)
    except Exception:
        return jsonify({"error": "central server unavailable"}), 503

    if not payload:
        return jsonify({"error": "invalid credentials"}), 401

    remote_user = payload.get("user") or {}
    local_id = sync_local_user_record(username, password, remote_user.get("role", "user"))
    session["user_id"] = local_id
    configure_sync_client(username, password)
    send_client_heartbeat()
    return jsonify({"ok": True, "user": {"id": local_id, "username": username, "role": remote_user.get("role", "user")}})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    if get_server_url() and _detector_client.session:
        try:
            _detector_client.session.post(f"{get_server_url()}/api/auth/logout", json={}, timeout=10)
        except Exception:
            pass
    session.pop("user_id", None)
    _detector_client.username = ""
    _detector_client.password = ""
    _detector_client.session.cookies.clear()
    with _state_lock:
        _runtime_state["last_heartbeat_at"] = None
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    user = current_user()
    if not user:
        return jsonify({"user": None})
    return jsonify({"user": {"id": user["id"], "username": user["username"], "role": user.get("role")}})


@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    if not get_server_url():
        return jsonify({"error": "central server not configured"}), 503

    import requests

    try:
        response = requests.post(
            f"{get_server_url()}/api/auth/register",
            json={"username": username, "password": password},
            timeout=15,
        )
    except Exception:
        return jsonify({"error": "central server unavailable"}), 503

    if not response.ok:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        return jsonify({"error": payload.get("error", "registration failed")}), response.status_code

    remote_user_id = sync_local_user_record(username, password, "user")
    session["user_id"] = remote_user_id
    configure_sync_client(username, password)
    return jsonify({"ok": True, "user_id": remote_user_id})


@app.route("/api/auth/register/status", methods=["GET"])
def api_auth_register_status():
    if not get_server_url():
        return jsonify({"ok": False, "enabled": False, "error": "central server not configured"}), 503
    try:
        return jsonify(get_central_registration_status())
    except Exception:
        return jsonify({"ok": False, "enabled": False, "error": "central server unavailable"}), 503


@app.route("/api/client/settings", methods=["GET"])
@node_login_required
def api_client_settings_get():
    with _state_lock:
        settings = dict(_runtime_state["settings"])
    return jsonify({"ok": True, "settings": settings})


@app.route("/api/client/connection", methods=["GET"])
def api_client_connection_get():
    return jsonify({"ok": True, "server_ip": get_server_ip(), "server_url": get_server_url()})


@app.route("/api/client/connection", methods=["POST"])
def api_client_connection_set():
    payload = request.get_json(force=True, silent=True) or {}
    server_ip = str(payload.get("server_ip") or "").strip()
    if not server_ip:
        return jsonify({"ok": False, "error": "server ip required"}), 400
    connection = set_server_connection(server_ip)
    test_result = test_server_connection(connection["server_url"])
    if not test_result.get("ok"):
        return jsonify({"ok": False, "error": test_result.get("error", "connection failed"), **connection}), 502
    return jsonify({"ok": True, **connection})


@app.route("/api/client/connection/test", methods=["POST"])
def api_client_connection_test():
    payload = request.get_json(force=True, silent=True) or {}
    server_ip = str(payload.get("server_ip") or "").strip()
    server_url = normalize_server_ip(server_ip)
    result = test_server_connection(server_url)
    if result.get("ok"):
        return jsonify({"ok": True, "server_ip": extract_server_ip(server_url), "server_url": server_url})
    return jsonify({"ok": False, "error": result.get("error", "connection failed")}), 502


@app.route("/api/client/settings", methods=["POST"])
@node_login_required
def api_client_settings_update():
    payload = request.get_json(force=True, silent=True) or {}
    updated = apply_settings(payload)
    return jsonify({"ok": True, "settings": updated})


@app.route("/api/client/model/status", methods=["GET"])
@node_login_required
def api_client_model_status():
    return jsonify({"ok": True, "status": current_model_status()})


@app.route("/api/client/monitor-status", methods=["GET"])
@node_login_required
def api_client_monitor_status():
    result = refresh_monitor_status(force=False)
    if not result.get("ok"):
        return jsonify(result), 503
    return jsonify(result)


@app.route("/api/client/detector_state", methods=["POST"])
@node_login_required
def api_client_detector_state():
    payload = request.get_json(force=True, silent=True) or {}
    source = str(payload.get("source") or "").strip() or "unknown"
    is_detecting = bool(payload.get("is_detecting"))

    with _state_lock:
        active = _runtime_state["active_detectors"]
        if is_detecting:
            active.add(source)
        else:
            active.discard(source)

    applied = apply_pending_model_if_idle(force=False)
    return jsonify({"ok": True, "idle": is_client_idle(), "applied_model": applied})


@app.route("/api/drone/detect", methods=["POST"])
@node_login_required
def api_detect():
    data = request.get_json(force=True, silent=True) or {}
    frame_b64 = data.get("frame")
    if not frame_b64:
        return jsonify({"error": "no frame provided"}), 400

    try:
        frame = detector_runtime.decode_base64_image(frame_b64)
    except Exception:
        return jsonify({"error": "image decode error"}), 400

    result = _detector_client.detect_frame(
        frame,
        frame_no=int(data.get("frame_no", 0)),
        timestamp=data.get("timestamp"),
    )
    return jsonify(result)


@app.route("/api/logs", methods=["GET"])
@node_login_required
def api_logs_list():
    limit = int(request.args.get("limit", 500))
    return jsonify(database.list_logs(init_db_conn(), limit=limit))


@app.route("/api/logs/create", methods=["POST"])
@node_login_required
def api_logs_create():
    data = request.get_json(force=True, silent=True) or {}
    new_id = database.create_log(
        init_db_conn(),
        str(data.get("time", "") or ""),
        str(data.get("event", "") or ""),
        str(data.get("source", "") or ""),
        str(data.get("clip", "") or ""),
        sync_status="pending",
    )
    return jsonify({"ok": True, "id": new_id})


@app.route("/api/logs/update", methods=["POST"])
@node_login_required
def api_logs_update():
    data = request.get_json(force=True, silent=True) or {}
    database.update_log(
        init_db_conn(),
        int(data.get("id")),
        str(data.get("time", "") or ""),
        str(data.get("event", "") or ""),
        str(data.get("source", "") or ""),
        str(data.get("clip", "") or ""),
    )
    return jsonify({"ok": True})


@app.route("/api/logs/delete", methods=["POST"])
@node_admin_required
def api_logs_delete():
    data = request.get_json(force=True, silent=True) or {}
    database.delete_log(init_db_conn(), int(data.get("id")))
    return jsonify({"ok": True})


@app.route("/api/clip/save", methods=["POST"])
@node_login_required
def api_clip_save():
    if "file" not in request.files:
        return jsonify({"error": "missing file"}), 400

    file_storage = request.files["file"]
    if not file_storage or not file_storage.filename:
        return jsonify({"error": "empty file"}), 400

    source = request.form.get("source", "NODE")
    event_id = request.form.get("event_id", "")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = secure_filename(f"{ts}_{source}_{event_id}.webm")
    save_path = os.path.join(LOCAL_CLIPS_DIR, safe_name)
    file_storage.save(save_path)
    return jsonify({"ok": True, "filename": safe_name})


@app.route("/api/clip/download", methods=["GET"])
@node_login_required
def api_clip_download():
    filename = secure_filename(request.args.get("file", "") or "")
    if not filename:
        return jsonify({"error": "missing file"}), 400
    path = os.path.join(LOCAL_CLIPS_DIR, filename)
    if not os.path.isfile(path):
        return jsonify({"error": "not found"}), 404
    return send_file(path, as_attachment=True)


@app.route("/api/node/sync", methods=["POST"])
@node_login_required
def api_node_sync():
    result = run_sync_once()
    if result.get("ok"):
        model_result = check_for_model_update(force=True)
        result["model"] = model_result
    return jsonify(result)


@app.route("/api/node/model/update", methods=["POST"])
@node_login_required
def api_node_model_update():
    result = check_for_model_update(force=True)
    error_text = str(result.get("error", "")).lower()
    if result.get("ok"):
        status_code = 200
    elif "configured" in error_text:
        status_code = 503
    elif "login" in error_text or "auth" in error_text:
        status_code = 401
    else:
        status_code = 500
    return jsonify(result), status_code


@app.route("/api/camera/register", methods=["POST"])
@node_login_required
def api_camera_register():
    data = request.get_json(force=True, silent=True) or {}
    camera_name = (data.get("camera_name") or "").strip()
    camera_id = (data.get("camera_id") or "").strip()
    if not camera_name or not camera_id:
        return jsonify({"error": "camera_name and camera_id required"}), 400

    user = current_user() or {}
    conn = init_db_conn()
    try:
        camera_db_id = database.register_camera(conn, int(user["id"]), camera_name, camera_id)
    except Exception:
        existing = next((item for item in database.get_user_cameras(conn, int(user["id"])) if item["camera_id"] == camera_id), None)
        camera_db_id = existing["id"] if existing else None

    if ensure_central_login():
        try:
            _detector_client.session.post(
                f"{get_server_url()}/api/camera/register",
                json={"camera_name": camera_name, "camera_id": camera_id},
                timeout=15,
            )
        except Exception:
            pass

    return jsonify({"ok": True, "camera_db_id": camera_db_id})


@app.route("/api/camera/list", methods=["GET"])
@node_login_required
def api_camera_list():
    user = current_user() or {}
    cameras = database.get_user_cameras(init_db_conn(), int(user["id"]))
    return jsonify({"ok": True, "cameras": cameras})


@app.route("/api/camera/status", methods=["POST"])
@node_login_required
def api_camera_status():
    payload = request.get_json(force=True, silent=True) or {}
    camera_id = str(payload.get("camera_id") or "").strip()
    if not camera_id:
        return jsonify({"ok": False, "error": "camera_id required"}), 400

    if ensure_central_login():
        try:
            response = _detector_client.session.post(
                f"{get_server_url()}/api/camera/status",
                json={
                    "camera_id": camera_id,
                    "is_detecting": bool(payload.get("is_detecting")),
                },
                timeout=15,
            )
            if response.ok:
                return jsonify({"ok": True})
        except Exception:
            pass

    return jsonify({"ok": True, "local_only": True})


@app.route("/api/camera/stream", methods=["POST"])
@node_login_required
def api_camera_stream():
    if "file" not in request.files:
        return jsonify({"error": "missing file"}), 400

    file_storage = request.files["file"]
    if not file_storage or not file_storage.filename:
        return jsonify({"error": "empty file"}), 400

    monitor_status = refresh_monitor_status(force=False)
    if not monitor_status.get("ok"):
        return jsonify({"ok": False, "error": monitor_status.get("error", "monitor status unavailable")}), 503
    if not monitor_status.get("active"):
        return jsonify({"ok": True, "skipped": True, "reason": "monitor inactive"})

    if ensure_central_login():
        try:
            file_storage.stream.seek(0)
            files = {"file": (file_storage.filename or "frame.jpg", file_storage.stream.read(), file_storage.mimetype or "image/jpeg")}
            data = {
                "camera_id": request.form.get("camera_id", ""),
                "is_detecting": request.form.get("is_detecting", "false"),
            }
            response = _detector_client.session.post(
                f"{get_server_url()}/api/camera/stream",
                files=files,
                data=data,
                timeout=30,
            )
            if response.ok:
                return jsonify({"ok": True})
        except Exception:
            pass

    return jsonify({"ok": True, "local_only": True})


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/index.html")
def serve_index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/login.html")
def serve_login():
    return send_from_directory(STATIC_DIR, "login.html")


@app.route("/register.html")
def serve_register():
    return send_from_directory(STATIC_DIR, "register.html")


def run_detector_node(host: str = "127.0.0.1", port: int = 5050, debug: bool = False):
    init_db_conn()
    init_runtime_state()
    start_background_sync()
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run_detector_node(port=int(os.environ.get("DETECTOR_NODE_PORT", "5050")), debug=False)
