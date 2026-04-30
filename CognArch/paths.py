"""Centralized path resolution for source, data, and bundled resources."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _as_path(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser().resolve()


def get_app_root() -> Path:
    """Return the CognArch application/resource directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_ROOT = get_app_root()
RELEASE_ROOT = APP_ROOT.parent


def get_data_root() -> Path:
    """Return the writable data root.

    By default CognArch keeps data next to the app code for zip-release
    portability. Set COGNARCH_HOME to move mutable user data elsewhere.
    """
    override = os.environ.get("COGNARCH_HOME", "").strip()
    if override:
        return _as_path(override)
    return APP_ROOT


DATA_ROOT = get_data_root()


def get_model_root() -> Path:
    """Return the directory that contains bundled embedding models."""
    override = os.environ.get("COGNARCH_MODEL_DIR", "").strip()
    if override:
        return _as_path(override)
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "models"  # type: ignore[attr-defined]
        if bundled.exists():
            return bundled
    return APP_ROOT / "models"


MODEL_ROOT = get_model_root()


def ensure_dir(path: str | os.PathLike[str]) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return str(path)


def app_path(*parts: str) -> str:
    return str(APP_ROOT.joinpath(*parts))


def data_path(*parts: str) -> str:
    return str(DATA_ROOT.joinpath(*parts))


def model_path(*parts: str) -> str:
    return str(MODEL_ROOT.joinpath(*parts))


def get_config_path() -> str:
    return data_path("web_config.json")


def get_task_status_path() -> str:
    return data_path("task_status.json")


def get_hash_index_path() -> str:
    return data_path("data", "file_hash_index.json")


def get_ingest_state_path() -> str:
    return data_path("data", "ingest_state.json")


def get_index_path() -> str:
    return data_path("knowledge_base", "index.json")


def get_vector_store_dir() -> str:
    return data_path("knowledge_base", "vector_store")


def get_system_paths() -> dict[str, str]:
    return {
        "inputs": data_path("inputs"),
        "processed": data_path("processed"),
        "kb_notes": data_path("knowledge_base", "notes"),
        "sessions": data_path("sessions"),
        "skills_analytical": app_path("skills", "analytical"),
        "skills_generative": app_path("skills", "generative"),
    }


def resolve_path(relative_or_absolute_path: str) -> str:
    """Resolve user-data-relative paths with legacy app-root fallback."""
    if not relative_or_absolute_path:
        return ""

    candidate = Path(relative_or_absolute_path)
    if candidate.is_absolute():
        return str(candidate)

    data_candidate = DATA_ROOT / candidate
    if data_candidate.exists():
        return str(data_candidate)

    legacy_candidate = APP_ROOT / candidate
    if legacy_candidate.exists():
        return str(legacy_candidate)

    return str(data_candidate)


def make_relative(filepath: str) -> str:
    """Make paths portable relative to DATA_ROOT when possible."""
    absolute = Path(filepath).resolve()
    for root in (DATA_ROOT, APP_ROOT):
        try:
            return absolute.relative_to(root).as_posix()
        except ValueError:
            continue
    return str(absolute)

