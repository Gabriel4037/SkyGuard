import os
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

import io
from flask import send_file
_frame_cache = {}  # {frame_camera_id: {data, user_id, timestamp}}

import database
import detector_runtime
import model_registry


MODEL_PATH = detector_runtime.MODEL_PATH
DB_PATH = os.environ.get("DETECTIONS_DB", detector_runtime.resource_path("detections.db"))
CROPS_DIR = detector_runtime.CROPS_DIR

# Clips saved on server here
CLIPS_DIR = os.environ.get("CLIPS_DIR", detector_runtime.resource_path("detection_clips"))
os.makedirs(CLIPS_DIR, exist_ok=True)

STATIC_DIR = detector_runtime.resource_path("static")

app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app, supports_credentials=True)


app.secret_key = os.environ.get("FLASK_SECRET_KEY", "123")

app.config.update(
    SESSION_COOKIE_SECURE=False,      
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=None,     
    PERMANENT_SESSION_LIFETIME=86400
)

_db_conn = None

# log in
def current_user():
    user = None
    uid = session.get("user_id")
    if uid:
        u = database.get_user_by_id(init_db_conn(), uid) 
        if u:
            user = u
    return user

from functools import wraps
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def load_model():
    return detector_runtime.load_model()


def init_db_conn():
    global _db_conn
    if _db_conn is None:
        print("Initializing DB:", DB_PATH)
        _db_conn = database.init_db(DB_PATH)
    return _db_conn

def default_user():
    conn = init_db_conn()
    from database import get_user_by_username, insert_user
    if not get_user_by_username(conn, "admin"):
        pwd_hash = generate_password_hash("admin")
        insert_user(conn, "admin", pwd_hash)
        print("Created default user: admin / admin")
    else:
        print("Default user already exists.")

#log in
@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "invalid payload"}), 400
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    conn = init_db_conn()
    if database.get_user_by_username(conn, username): 
        return jsonify({"error": "username already exists"}), 400

    pwd_hash = generate_password_hash(password)  
    try:
        user_id = database.insert_user(conn, username, pwd_hash)
    except Exception as e:
        return jsonify({"error": "database error"}), 500

    session["user_id"] = user_id
    return jsonify({"ok": True, "user_id": user_id})

# Login endpoint
@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "invalid payload"}), 400
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    conn = init_db_conn()
    user = database.get_user_by_username(conn, username)
    if not user:
        return jsonify({"error": "invalid credentials"}), 401

    if not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "invalid credentials"}), 401

    # success: set session
    session["user_id"] = user["id"]
    database.update_last_login(conn, user["id"])
    app.logger.debug("session after login: %s", dict(session))
    return jsonify({"ok": True, "user": {"id": user["id"], "username": user["username"], "role": user.get("role")}})

# Logout
@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def api_me():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"user": None})
    conn = init_db_conn()
    user = database.get_user_by_id(conn, uid)
    if not user:
        return jsonify({"user": None})
    return jsonify({"user": {"id": user["id"], "username": user["username"], "role": user.get("role")}})

#--
@app.route("/api/drone/detect", methods=["POST"])
def api_detect():
    load_model()

    data = request.get_json(force=True, silent=True) or {}
    if "frame" not in data:
        return jsonify({"error": "no frame provided"}), 400

    frame_b64 = data["frame"]
    timestamp = data.get("timestamp")
    frame_no = int(data.get("frame_no", 0))
    persist = bool(data.get("persist", False))

    try:
        frame = detector_runtime.decode_base64_image(frame_b64)
        if frame is None:
            return jsonify({"error": "failed to decode image"}), 400
    except Exception:
        app.logger.exception("Image decode error")
        return jsonify({"error": "image decode error"}), 400

    result = detector_runtime.detect_frame(
        frame,
        frame_no=frame_no,
        timestamp=timestamp,
        persist=persist
        ,
        conf=float(data.get("conf", 0.4)),
        max_dim=int(data.get("max_dim", 640)),
        db_conn=init_db_conn() if persist else None,
    )

    return jsonify(result)


# -------------------------
# Clip upload + download
# -------------------------
@app.route("/api/clip/save", methods=["POST"])
def api_save_clip():
    """
    multipart/form-data:
      - file: webm blob
      - source: CAM/FILE
      - event_id: optional
    Saves to CLIPS_DIR and returns filename.
    """
    if "file" not in request.files:
        return jsonify({"error": "missing file"}), 400

    f = request.files["file"]
    if not f or f.filename == "":
        return jsonify({"error": "empty file"}), 400

    source = request.form.get("source", "UNK")
    event_id = request.form.get("event_id", "")

    safe_source = "".join([c for c in source if c.isalnum() or c in ("_", "-")])[:16] or "UNK"
    safe_event = "".join([c for c in event_id if c.isalnum() or c in ("_", "-")])[:32]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = f"{ts}_{safe_source}"
    if safe_event:
        name += f"_E{safe_event}"
    filename = secure_filename(name + ".webm")

    path = os.path.join(CLIPS_DIR, filename)
    try:
        f.save(path)
    except Exception:
        app.logger.exception("Failed saving clip")
        return jsonify({"error": "save failed"}), 500

    return jsonify({"ok": True, "filename": filename})


@app.route("/api/clip/download", methods=["GET"])
def api_download_clip():
    """
    GET /api/clip/download?file=clip_....webm
    Downloads a single clip file.
    """
    fname = request.args.get("file", "") or ""
    fname = secure_filename(fname)
    if not fname:
        return jsonify({"error": "missing file"}), 400

    full = os.path.join(CLIPS_DIR, fname)
    if not os.path.isfile(full):
        return jsonify({"error": "not found"}), 404

    return send_from_directory(CLIPS_DIR, fname, as_attachment=True)


# -------------------------
# NEW: Stored Logs APIs
# -------------------------
@app.route("/api/logs", methods=["GET"])
def api_logs_list():
    """
    GET -> [{id,time,event,source,clip}, ...]
    """
    conn = init_db_conn()
    try:
        limit = int(request.args.get("limit", 500))
    except Exception:
        limit = 500
    items = database.list_logs(conn, limit=limit)
    return jsonify(items)


@app.route("/api/logs/create", methods=["POST"])
def api_logs_create():
    """
    POST JSON: {time,event,source,clip}
    -> {ok:true, id:<new_id>}
    """
    conn = init_db_conn()
    data = request.get_json(force=True, silent=True) or {}
    time_text = str(data.get("time", "") or "")
    event = str(data.get("event", "") or "")
    source = str(data.get("source", "") or "")
    clip = str(data.get("clip", "") or "")
    try:
        new_id = database.create_log(conn, time_text, event, source, clip)
        return jsonify({"ok": True, "id": new_id})
    except Exception:
        app.logger.exception("create_log failed")
        return jsonify({"ok": False, "error": "create failed"}), 500


@app.route("/api/logs/update", methods=["POST"])
def api_logs_update():
    """
    POST JSON: {id,time,event,source,clip}
    -> {ok:true}
    """
    conn = init_db_conn()
    data = request.get_json(force=True, silent=True) or {}
    try:
        log_id = int(data.get("id"))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400

    time_text = str(data.get("time", "") or "")
    event = str(data.get("event", "") or "")
    source = str(data.get("source", "") or "")
    clip = str(data.get("clip", "") or "")

    try:
        database.update_log(conn, log_id, time_text, event, source, clip)
        return jsonify({"ok": True})
    except Exception:
        app.logger.exception("update_log failed")
        return jsonify({"ok": False, "error": "update failed"}), 500


@app.route("/api/logs/delete", methods=["POST"])
def api_logs_delete():
    """
    POST JSON: {id}
    -> {ok:true}
    """
    conn = init_db_conn()
    data = request.get_json(force=True, silent=True) or {}
    try:
        log_id = int(data.get("id"))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400

    try:
        database.delete_log(conn, log_id)
        return jsonify({"ok": True})
    except Exception:
        app.logger.exception("delete_log failed")
        return jsonify({"ok": False, "error": "delete failed"}), 500


# -------------------------
# Static
# -------------------------
@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/index.html")
def serve_web():
    return send_from_directory(STATIC_DIR, "index.html")

@app.route("/login.html")
def serve_login():
    return send_from_directory(STATIC_DIR, "login.html")

@app.route("/register.html")
def serve_register():
    return send_from_directory(STATIC_DIR, "register.html")


@app.route("/favicon.ico")
def favicon():
    ico_path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(ico_path):
        return send_from_directory(STATIC_DIR, "favicon.ico")
    return ("", 204)

# User management

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        uid = session.get("user_id")
        if not uid:
            return jsonify({"error": "authentication required"}), 401
        conn = init_db_conn()
        u = database.get_user_by_id(conn, uid)
        if not u or u.get("role") != "admin":
            return jsonify({"error": "admin required"}), 403
        return f(*args, **kwargs)
    return decorated


@app.route("/api/users", methods=["GET"])
@admin_required
def api_users_list():
    conn = init_db_conn()
    users = database.list_users(conn)
    return jsonify({"ok": True, "users": users})


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

    pwd_hash = generate_password_hash(password)
    try:
        new_id = database.insert_user(conn, username, pwd_hash, role=role)
        return jsonify({"ok": True, "id": new_id})
    except Exception:
        app.logger.exception("create user failed")
        return jsonify({"ok": False, "error": "create failed"}), 500


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
    conn = init_db_conn()
    try:
        database.update_user(conn, user_id, username, role)
        return jsonify({"ok": True})
    except Exception:
        app.logger.exception("update_user failed")
        return jsonify({"ok": False, "error": "update failed"}), 500


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
    pwd_hash = generate_password_hash(password)
    conn = init_db_conn()
    try:
        database.set_user_password_hash(conn, user_id, pwd_hash)
        return jsonify({"ok": True})
    except Exception:
        app.logger.exception("set_password failed")
        return jsonify({"ok": False, "error": "set password failed"}), 500


@app.route("/api/users/delete", methods=["POST"])
@admin_required
def api_users_delete():
    data = request.get_json(force=True, silent=True) or {}
    try:
        user_id = int(data.get("id"))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    conn = init_db_conn()
    try:
        database.delete_user(conn, user_id)
        return jsonify({"ok": True})
    except Exception:
        app.logger.exception("delete_user failed")
        return jsonify({"ok": False, "error": "delete failed"}), 500
    
@app.route("/users.html")
def serve_users():
    return send_from_directory(STATIC_DIR, "users.html")

# Camera Management APIs

@app.route("/api/camera/register", methods=["POST"])
@login_required
def api_camera_register():
    """
    POST JSON: {camera_name, camera_id}
    """
    conn = init_db_conn()
    user_id = session.get("user_id")
    data = request.get_json(force=True, silent=True) or {}
    
    camera_name = (data.get("camera_name") or "").strip()
    camera_id = (data.get("camera_id") or "").strip()
    
    if not camera_name or not camera_id:
        return jsonify({"error": "camera_name and camera_id required"}), 400
    
    try:
        cam_db_id = database.register_camera(conn, user_id, camera_name, camera_id)
        return jsonify({"ok": True, "camera_db_id": cam_db_id})
    except Exception as e:
        app.logger.exception("camera register failed")
        return jsonify({"error": "registration failed"}), 500


@app.route("/api/camera/list", methods=["GET"])
@login_required
def api_camera_list():
    """
    GET -> {cameras: [{id, camera_name, camera_id, is_active, ...}]}
    """
    conn = init_db_conn()
    user_id = session.get("user_id")
    try:
        cameras = database.get_user_cameras(conn, user_id)
        return jsonify({"ok": True, "cameras": cameras})
    except Exception as e:
        app.logger.exception("camera list failed")
        return jsonify({"error": "list failed"}), 500


@app.route("/api/admin/cameras", methods=["GET"])
@admin_required
def api_admin_cameras_list():
    """
    GET -> {cameras: [{camera_id, user_id, camera_name, username, ...}]}
    """
    conn = init_db_conn()
    try:
        cameras = database.get_all_active_cameras(conn)
        return jsonify({"ok": True, "cameras": cameras})
    except Exception as e:
        app.logger.exception("admin cameras list failed")
        return jsonify({"error": "list failed"}), 500


@app.route("/api/camera/stream", methods=["POST"])
@login_required
def api_camera_stream():
    """
    POST multipart/form-data:
      - file: frame image (jpeg/png)
      - camera_id: camera identifier
      - frame_no: frame number
    """
    if "file" not in request.files:
        return jsonify({"error": "missing file"}), 400
    
    f = request.files["file"]
    if not f or f.filename == "":
        return jsonify({"error": "empty file"}), 400
    
    camera_id = request.form.get("camera_id", "")
    user_id = session.get("user_id")
    
    if not camera_id:
        return jsonify({"error": "camera_id required"}), 400
    
    try:
        frame_data = f.read()
        # Store frame in memory/cache for admin to retrieve
        # You can use Redis or in-memory dict
        frame_key = f"frame_{camera_id}"
        # For now, use a simple in-memory approach (NOT production-ready)
        # In production, use Redis for better performance
        _frame_cache[frame_key] = {
            "data": frame_data,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat()
        }
        return jsonify({"ok": True})
    except Exception as e:
        app.logger.exception("camera stream upload failed")
        return jsonify({"error": "upload failed"}), 500


@app.route("/api/admin/camera/frame", methods=["GET"])
@admin_required
def api_admin_camera_frame():
    """
    GET ?camera_id=xxx -> returns image data
    """
    camera_id = request.args.get("camera_id", "")
    if not camera_id:
        return jsonify({"error": "camera_id required"}), 400
    
    frame_key = f"frame_{camera_id}"
    if frame_key not in _frame_cache:
        return jsonify({"error": "no frame available"}), 404
    
    frame_info = _frame_cache[frame_key]
    return send_file(
        io.BytesIO(frame_info["data"]),
        mimetype="image/jpeg"
    )

@app.route("/admin_monitor.html")
def serve_admin_monitor():
    return send_from_directory(STATIC_DIR, "admin_monitor.html")


@app.route("/model_manager.html")
def serve_model_manager():
    return send_from_directory(STATIC_DIR, "model_manager.html")


@app.route("/api/models/current", methods=["GET"])
@login_required
def api_models_current():
    meta = model_registry.get_current_model_info()
    if not meta:
        return jsonify({"ok": False, "error": "no released model"}), 404
    return jsonify({"ok": True, "model": meta})


@app.route("/api/models/download/current", methods=["GET"])
@login_required
def api_models_download_current():
    path = model_registry.get_current_model_path()
    if not path:
        return jsonify({"ok": False, "error": "no released model"}), 404
    return send_file(path, as_attachment=True)


@app.route("/api/models/release", methods=["POST"])
@admin_required
def api_models_release():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "missing file"}), 400

    file_storage = request.files["file"]
    if not file_storage or not file_storage.filename:
        return jsonify({"ok": False, "error": "empty file"}), 400

    user = current_user() or {}
    released_by = user.get("username", "admin")

    try:
        meta = model_registry.release_uploaded_model(file_storage, released_by=released_by)
        return jsonify({"ok": True, "model": meta})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        app.logger.exception("model release failed")
        return jsonify({"ok": False, "error": "release failed"}), 500


if __name__ == "__main__":
    load_model()
    init_db_conn()
    default_user()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
