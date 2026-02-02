import sys
import os
import base64
from datetime import datetime

import cv2
import numpy as np
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

import database

def resource_path(relative_path: str) -> str:
    """Support PyInstaller builds."""
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)


MODEL_PATH = os.environ.get("YOLO_MODEL", resource_path("best_v11.pt"))
DB_PATH = os.environ.get("DETECTIONS_DB", resource_path("detections.db"))
CROPS_DIR = os.environ.get("CROPS_DIR", resource_path("detection_crops"))

# Clips saved on server here
CLIPS_DIR = os.environ.get("CLIPS_DIR", resource_path("detection_clips"))
os.makedirs(CLIPS_DIR, exist_ok=True)

STATIC_DIR = resource_path("static")

app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app, supports_credentials=True)


app.secret_key = os.environ.get("FLASK_SECRET_KEY", "123")

app.config.update(
    SESSION_COOKIE_SECURE=False,      
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=None,     
    PERMANENT_SESSION_LIFETIME=86400
)

_model = None
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
    global _model
    if _model is None:
        print("Loading YOLO model from:", MODEL_PATH)
        from ultralytics import YOLO
        _model = YOLO(MODEL_PATH)
    return _model


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

def decode_base64_image(data_url: str):
    if data_url.startswith("data:"):
        _, b64 = data_url.split(",", 1)
    else:
        b64 = data_url
    img_bytes = base64.b64decode(b64)
    arr = np.frombuffer(img_bytes, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def results_to_list(results, original_frame, frame_no=None, timestamp=None, scale=1.0, persist=False):
    """
    persist=False: FAST (no DB, no crops)
    persist=True:  save crops + DB detections
    """
    model = load_model()
    detections = []

    for r in results:
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            continue

        for idx, box in enumerate(boxes):
            try:
                xyxy = box.xyxy[0].cpu().numpy() if hasattr(box.xyxy, "cpu") else box.xyxy[0].numpy()
            except Exception:
                xyxy = box.xyxy[0].numpy()

            x1, y1, x2, y2 = map(float, xyxy[:4])

            try:
                confidence = float(box.conf[0]) if hasattr(box, "conf") else float(box.conf)
            except Exception:
                confidence = float(getattr(box, "confidence", 0.0))

            try:
                class_id = int(box.cls[0]) if hasattr(box, "cls") else int(box.cls)
            except Exception:
                class_id = int(getattr(box, "class_id", 0))

            label = model.names[class_id] if hasattr(model, "names") and class_id in model.names else str(class_id)

            # scale back to original coords
            if scale and scale != 1.0:
                inv = 1.0 / scale
                x1, y1, x2, y2 = (x1 * inv, y1 * inv, x2 * inv, y2 * inv)

            x1_i, y1_i, x2_i, y2_i = map(lambda v: int(round(v)), (x1, y1, x2, y2))
            w = max(0, x2_i - x1_i)
            h = max(0, y2_i - y1_i)

            crop_path = None
            ts = timestamp or datetime.now().isoformat(sep=" ", timespec="seconds")

            if persist:
                init_db_conn()
                try:
                    if original_frame is not None and w > 1 and h > 1:
                        sy1, sy2, sx1, sx2 = y1_i, y2_i, x1_i, x2_i
                        sy1 = max(0, sy1)
                        sx1 = max(0, sx1)
                        sy2 = max(sy1 + 1, sy2)
                        sx2 = max(sx1 + 1, sx2)
                        crop = original_frame[sy1:sy2, sx1:sx2].copy()
                        if crop is not None and crop.size != 0:
                            base_name = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + f"_{frame_no or 0}_{idx}"
                            crop_path = database.save_crop(crop, CROPS_DIR, base_name)
                except Exception:
                    crop_path = None

                try:
                    database.insert_detection(
                        init_db_conn(),
                        timestamp=ts,
                        frame_no=frame_no or 0,
                        x1=x1_i, y1=y1_i, x2=x2_i, y2=y2_i,
                        width=w, height=h,
                        confidence=confidence,
                        class_id=class_id,
                        label=label,
                        crop_path=crop_path,
                        model=os.path.basename(MODEL_PATH),
                        video_path=None
                    )
                except Exception:
                    app.logger.exception("DB insert failed")

            detections.append({
                "x1": x1_i, "y1": y1_i, "x2": x2_i, "y2": y2_i,
                "width": w, "height": h,
                "confidence": confidence,
                "class_id": class_id,
                "label": label,
                "crop_path": crop_path
            })

    return detections

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
        frame = decode_base64_image(frame_b64)
        if frame is None:
            return jsonify({"error": "failed to decode image"}), 400
    except Exception:
        app.logger.exception("Image decode error")
        return jsonify({"error": "image decode error"}), 400

    orig_h, orig_w = frame.shape[:2]
    max_dim = int(data.get("max_dim", 640))
    conf = float(data.get("conf", 0.4))

    scale = 1.0
    if max(orig_h, orig_w) > max_dim:
        scale = max_dim / float(max(orig_h, orig_w))
        proc_w = int(round(orig_w * scale))
        proc_h = int(round(orig_h * scale))
        frame_proc = cv2.resize(frame, (proc_w, proc_h), interpolation=cv2.INTER_AREA)
    else:
        frame_proc = frame
        proc_h, proc_w = orig_h, orig_w

    import time
    t0 = time.time()
    model = load_model()
    results = model.predict(frame_proc, conf=conf, verbose=False)
    t1 = time.time()

    detections = results_to_list(
        results,
        original_frame=frame,
        frame_no=frame_no,
        timestamp=timestamp,
        scale=scale,
        persist=persist
    )

    return jsonify({
        "detected": len(detections) > 0,
        "detections": detections,
        "processing_time": round(t1 - t0, 3),
        "orig_size": {"width": orig_w, "height": orig_h},
        "processed_size": {"width": proc_w, "height": proc_h},
        "scale": scale
    })


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


if __name__ == "__main__":
    load_model()
    init_db_conn()
    default_user()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
