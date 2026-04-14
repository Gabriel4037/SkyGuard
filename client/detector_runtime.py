import base64
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

import database


def resource_path(relative_path: str) -> str:
    """Support PyInstaller builds."""
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)


DEFAULT_MODEL_PATH = os.environ.get("YOLO_MODEL", resource_path("models/best_v11.pt"))
MODEL_META_PATH = resource_path("models/current_model.json")

_model = None
_model_lock = threading.RLock()
_loaded_model_info: Dict[str, Any] = {}


def _ensure_parent(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)


def read_model_meta() -> Optional[Dict[str, Any]]:
    if not os.path.isfile(MODEL_META_PATH):
        return None
    try:
        with open(MODEL_META_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def write_model_meta(info: Dict[str, Any]) -> None:
    _ensure_parent(MODEL_META_PATH)
    with open(MODEL_META_PATH, "w", encoding="utf-8") as handle:
        json.dump(info, handle, indent=2)


def resolve_initial_model_path() -> str:
    meta = read_model_meta()
    if meta:
        path = meta.get("path") or ""
        if path and os.path.isfile(path):
            return path
    return DEFAULT_MODEL_PATH


def build_model_info(model_path: str, *, version: Optional[str] = None, filename: Optional[str] = None) -> Dict[str, Any]:
    path = os.path.abspath(model_path)
    stat_size = os.path.getsize(path) if os.path.isfile(path) else None
    return {
        "path": path,
        "filename": filename or os.path.basename(path),
        "version": version or os.path.splitext(os.path.basename(path))[0],
        "size_bytes": stat_size,
        "loaded_at": datetime.now().isoformat(timespec="seconds"),
    }


def get_loaded_model_info() -> Dict[str, Any]:
    with _model_lock:
        if _loaded_model_info:
            return dict(_loaded_model_info)
        meta = read_model_meta()
        return dict(meta or {})


def load_model(model_path: Optional[str] = None, *, force_reload: bool = False, version: Optional[str] = None):
    global _model, _loaded_model_info

    target_path = os.path.abspath(model_path or resolve_initial_model_path())
    if not os.path.isfile(target_path):
        raise FileNotFoundError(f"YOLO model file not found: {target_path}")

    with _model_lock:
        current_path = _loaded_model_info.get("path")
        if _model is not None and not force_reload and current_path == target_path:
            return _model

        target_info = build_model_info(target_path, version=version)
        print(
            "Loading YOLO model:",
            f"version={target_info['version']}",
            f"path={target_info['path']}",
        )

        from ultralytics import YOLO

        _model = YOLO(target_path)
        _loaded_model_info = target_info
        write_model_meta(_loaded_model_info)
        print("YOLO model loaded successfully.")
        return _model


def reload_model(model_path: str, *, version: Optional[str] = None) -> Dict[str, Any]:
    load_model(model_path, force_reload=True, version=version)
    return get_loaded_model_info()


def decode_base64_image(data_url: str):
    if data_url.startswith("data:"):
        _, b64 = data_url.split(",", 1)
    else:
        b64 = data_url
    img_bytes = base64.b64decode(b64)
    arr = np.frombuffer(img_bytes, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def results_to_list(
    results,
    original_frame,
    frame_no: Optional[int] = None,
    timestamp: Optional[str] = None,
    scale: float = 1.0,
    db_conn=None,
) -> List[Dict[str, Any]]:
    """
    Convert model output into response-ready detection metadata.
    """
    model = load_model()
    detections: List[Dict[str, Any]] = []

    for result in results:
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue

        for box in boxes:
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

            if scale and scale != 1.0:
                inv = 1.0 / scale
                x1, y1, x2, y2 = (x1 * inv, y1 * inv, x2 * inv, y2 * inv)

            x1_i, y1_i, x2_i, y2_i = map(lambda value: int(round(value)), (x1, y1, x2, y2))
            width = max(0, x2_i - x1_i)
            height = max(0, y2_i - y1_i)

            ts = timestamp or datetime.now().isoformat(sep=" ", timespec="seconds")

            detections.append(
                {
                    "x1": x1_i,
                    "y1": y1_i,
                    "x2": x2_i,
                    "y2": y2_i,
                    "width": width,
                    "height": height,
                    "confidence": confidence,
                    "class_id": class_id,
                    "label": label,
                }
            )

    return detections


def detect_frame(
    frame,
    *,
    frame_no: int = 0,
    timestamp: Optional[str] = None,
    conf: float = 0.4,
    max_dim: int = 640,
    db_conn=None,
) -> Dict[str, Any]:
    if frame is None:
        raise ValueError("frame is required")

    orig_h, orig_w = frame.shape[:2]

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
    with _model_lock:
        model = load_model()
        results = model.predict(frame_proc, conf=conf, verbose=False)
    t1 = time.time()

    detections = results_to_list(
        results,
        original_frame=frame,
        frame_no=frame_no,
        timestamp=timestamp,
        scale=scale,
        db_conn=db_conn,
    )

    return {
        "detected": len(detections) > 0,
        "detections": detections,
        "processing_time": round(t1 - t0, 3),
        "orig_size": {"width": orig_w, "height": orig_h},
        "processed_size": {"width": proc_w, "height": proc_h},
        "scale": scale,
        "model": get_loaded_model_info(),
    }
