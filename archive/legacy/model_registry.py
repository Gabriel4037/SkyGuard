import json
import os
from datetime import datetime
from typing import Dict, Optional

from werkzeug.utils import secure_filename

import detector_runtime


MODELS_DIR = os.environ.get("RELEASED_MODELS_DIR", detector_runtime.resource_path("released_models"))
CURRENT_MODEL_META = os.path.join(MODELS_DIR, "current_model.json")

os.makedirs(MODELS_DIR, exist_ok=True)


def get_current_model_info() -> Optional[Dict]:
    if not os.path.isfile(CURRENT_MODEL_META):
        return None
    with open(CURRENT_MODEL_META, "r", encoding="utf-8") as handle:
        return json.load(handle)


def get_current_model_path() -> Optional[str]:
    meta = get_current_model_info()
    if not meta:
        return None
    path = meta.get("path")
    if not path or not os.path.isfile(path):
        return None
    return path


def release_uploaded_model(file_storage, released_by: str) -> Dict:
    if not file_storage or not file_storage.filename:
        raise ValueError("missing model file")

    original_name = secure_filename(file_storage.filename)
    if not original_name.lower().endswith(".pt"):
        raise ValueError("only .pt files are supported")

    version = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{version}_{original_name}"
    save_path = os.path.join(MODELS_DIR, filename)
    file_storage.save(save_path)

    meta = {
        "version": version,
        "filename": filename,
        "path": save_path,
        "released_at": datetime.utcnow().isoformat(timespec="seconds"),
        "released_by": released_by,
        "size_bytes": os.path.getsize(save_path),
    }

    with open(CURRENT_MODEL_META, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)

    return meta
