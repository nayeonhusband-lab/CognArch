"""CognArch shared utility wrappers.

Path ownership lives in paths.py. This module keeps the older helper names
available for existing workers while letting release/runtime policy stay
centralized.
"""

import os
import shutil
from pathlib import Path

from paths import (
    APP_ROOT,
    DATA_ROOT,
    app_path,
    data_path,
    ensure_dir,
    make_relative,
    resolve_path,
)


def get_app_dir():
    """Return the CognArch application/resource directory."""
    return str(APP_ROOT)


def get_data_dir():
    """Return the mutable data directory used for indexes and ingest state."""
    return ensure_dir(data_path("data"))


def get_project_root():
    """Return the mutable project/data root used by legacy helpers."""
    return str(DATA_ROOT)


def find_antiword() -> str | None:
    """Locate antiword via explicit config or PATH."""
    configured = os.environ.get("COGNARCH_ANTIWORD", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return str(candidate.resolve())

    return shutil.which("antiword")
