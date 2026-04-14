import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import database
import detector_runtime
import requests


@dataclass
class DetectorSettings:
    conf: float = 0.4
    max_dim: int = 640


class DetectorNodeService:
    def __init__(
        self,
        settings: Optional[DetectorSettings] = None,
        db_conn=None,
        server_url: str = "",
        username: str = "",
        password: str = "",
        clips_dir: Optional[str] = None,
    ):
        self.settings = settings or DetectorSettings()
        self.db_conn = db_conn or database.init_db(detector_runtime.resource_path("data/client_detections.db"))
        self.server_url = server_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.models_dir = Path(detector_runtime.resource_path("models"))
        self.clips_dir = Path(clips_dir or detector_runtime.resource_path("clips"))
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        detector_runtime.load_model()

    def update_settings(self, *, conf: Optional[float] = None, max_dim: Optional[int] = None) -> None:
        if conf is not None:
            self.settings.conf = float(conf)
        if max_dim is not None:
            self.settings.max_dim = int(max_dim)

    def detect_frame(self, frame, *, frame_no: int = 0, timestamp: Optional[str] = None) -> Dict[str, Any]:
        return detector_runtime.detect_frame(
            frame,
            frame_no=frame_no,
            timestamp=timestamp,
            conf=self.settings.conf,
            max_dim=self.settings.max_dim,
            db_conn=self.db_conn,
        )

    def login(self) -> bool:
        if not self.server_url or not self.username or not self.password:
            return False

        response = self.session.post(
            f"{self.server_url}/api/auth/login",
            json={"username": self.username, "password": self.password},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        return bool(payload.get("ok"))

    def get_remote_model_info(self) -> Optional[Dict[str, Any]]:
        if not self.server_url:
            return None
        response = self.session.get(f"{self.server_url}/api/models/current", timeout=15)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        return payload.get("model")

    def get_local_model_info(self) -> Dict[str, Any]:
        return detector_runtime.get_loaded_model_info()

    def is_newer_model_available(self, remote_info: Optional[Dict[str, Any]]) -> bool:
        if not remote_info:
            return False
        local = self.get_local_model_info()
        remote_version = str(remote_info.get("version") or remote_info.get("filename") or "")
        local_version = str(local.get("version") or local.get("filename") or "")
        return bool(remote_version and remote_version != local_version)

    def download_model(self, model_info: Dict[str, Any]) -> Path:
        target = self.models_dir / model_info["filename"]
        if not target.exists():
            response = self.session.get(f"{self.server_url}/api/models/download/current", timeout=60)
            response.raise_for_status()
            target.write_bytes(response.content)
        return target

    def sync_pending_logs(self, limit: int = 50) -> int:
        if not self.server_url:
            return 0

        synced = 0
        pending = database.list_unsynced_logs(self.db_conn, limit=limit)
        for item in pending:
            clip_value = item.get("clip") or ""
            clip_filename = None
            if clip_value.startswith("Saved:"):
                clip_filename = clip_value.split("Saved:", 1)[1].strip()

            payload = {
                "local_id": item.get("id"),
                "time": item.get("time", ""),
                "event": item.get("event", ""),
                "source": item.get("source", ""),
                "clip": clip_value,
            }
            files = None
            if clip_filename:
                clip_path = self.clips_dir / clip_filename
                if clip_path.exists():
                    handle = clip_path.open("rb")
                    files = {"file": (clip_filename, handle, "video/webm")}

            try:
                log_response = self.session.post(
                    f"{self.server_url}/api/node/upload_event",
                    data={"payload": json.dumps(payload)},
                    files=files,
                    timeout=60,
                )
                log_response.raise_for_status()
                log_payload = log_response.json()
            finally:
                if files:
                    files["file"][1].close()

            database.mark_log_synced(self.db_conn, item["id"], log_payload.get("id"))
            synced += 1

        return synced


def build_client_service(db_conn=None, **kwargs) -> DetectorNodeService:
    settings_keys = {"conf", "max_dim"}
    settings_kwargs = {key: value for key, value in kwargs.items() if key in settings_keys}
    client_kwargs = {key: value for key, value in kwargs.items() if key not in settings_keys}
    settings = DetectorSettings(**settings_kwargs)
    return DetectorNodeService(settings=settings, db_conn=db_conn, **client_kwargs)
