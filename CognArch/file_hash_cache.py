import hashlib
import json
import os
from pathlib import Path

from utils import get_data_dir, get_project_root, make_relative, resolve_path

SUPPORTED_INPUT_EXTS = (".pdf", ".docx", ".doc", ".md", ".txt", ".pptx", ".xlsx")
DUPLICATES_DIRNAME = "_duplicates"
HASH_INDEX_PATH = os.path.join(get_data_dir(), "file_hash_index.json")
INGEST_STATE_PATH = os.path.join(get_data_dir(), "ingest_state.json")


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_hash_index() -> dict:
    data = _load_json(HASH_INDEX_PATH, {})
    if not isinstance(data, dict):
        return {}
    return data


def _save_hash_index(index: dict) -> None:
    _save_json(HASH_INDEX_PATH, index)


def _normalize_relpath(path: str) -> str:
    return make_relative(os.path.abspath(path)).replace("\\", "/")


def _stat_signature(path: str) -> tuple[int, int]:
    st = os.stat(path)
    return int(st.st_size), int(st.st_mtime)


def hash_bytes(content: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(content)
    return digest.hexdigest()


def hash_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def get_or_compute_file_hash(path: str) -> str:
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return ""

    relpath = _normalize_relpath(abs_path)
    size, mtime = _stat_signature(abs_path)
    index = _load_hash_index()
    cached = index.get(relpath)
    if cached and cached.get("size") == size and cached.get("mtime") == mtime and cached.get("sha256"):
        return cached["sha256"]

    digest = hash_file(abs_path)
    index[relpath] = {
        "sha256": digest,
        "size": size,
        "mtime": mtime,
    }
    _save_hash_index(index)
    return digest


def remove_hash_entry(path: str) -> None:
    relpath = _normalize_relpath(path)
    index = _load_hash_index()
    if relpath in index:
        index.pop(relpath, None)
        _save_hash_index(index)


def cleanup_missing_hash_entries() -> None:
    index = _load_hash_index()
    alive = {}
    for relpath, payload in index.items():
        abs_path = resolve_path(relpath)
        if os.path.exists(abs_path):
            alive[relpath] = payload
    if alive != index:
        _save_hash_index(alive)


def rebuild_hash_index(project_root: str = "") -> dict:
    """重新扫描 inputs/ 和 processed/ 目录，重建 hash 索引并检测重复文件"""
    if not project_root:
        project_root = get_project_root()

    old_index = _load_hash_index()
    new_index = {}
    scanned = 0
    reused = 0
    recalculated = 0

    # 扫描目标: (目录, 是否递归, 排除目录)
    scan_targets = [
        (os.path.join(project_root, "inputs"), False, (DUPLICATES_DIRNAME,)),
        (os.path.join(project_root, "inputs", DUPLICATES_DIRNAME), False, ()),
        (os.path.join(project_root, "processed"), False, ()),
    ]

    for directory, recursive, exclude in scan_targets:
        if not os.path.exists(directory):
            continue
        for path in list_supported_files(directory, recursive=recursive, exclude_dirs=exclude):
            try:
                scanned += 1
                relpath = _normalize_relpath(path)
                size, mtime = _stat_signature(path)

                cached = old_index.get(relpath)
                if cached and cached.get("size") == size and cached.get("mtime") == mtime and cached.get("sha256"):
                    new_index[relpath] = cached
                    reused += 1
                else:
                    digest = hash_file(path)
                    new_index[relpath] = {"sha256": digest, "size": size, "mtime": mtime}
                    recalculated += 1
            except (OSError, FileNotFoundError):
                # 文件在扫描过程中被删除或不可访问，跳过
                continue

    _save_hash_index(new_index)
    clear_force_rerun_for_missing_files()

    # 检测重复文件
    hash_groups = {}
    for relpath, payload in new_index.items():
        hash_groups.setdefault(payload["sha256"], []).append(relpath)
    duplicate_groups = [paths for paths in hash_groups.values() if len(paths) > 1]

    return {
        "scanned": scanned,
        "reused": reused,
        "recalculated": recalculated,
        "removed": len(old_index) - len(new_index),
        "duplicates": len(duplicate_groups),
        "duplicate_details": duplicate_groups,
    }


def list_supported_files(directory: str, recursive: bool = False, exclude_dirs: tuple[str, ...] = ()) -> list[str]:
    if not directory or not os.path.exists(directory):
        return []

    results = []
    base = Path(directory)
    if recursive:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for filename in files:
                if filename.lower().endswith(SUPPORTED_INPUT_EXTS):
                    results.append(os.path.join(root, filename))
    else:
        for entry in os.listdir(base):
            full = os.path.join(base, entry)
            if os.path.isfile(full) and entry.lower().endswith(SUPPORTED_INPUT_EXTS):
                results.append(full)
    return sorted(results)


def build_file_infos(directory: str, recursive: bool = False, exclude_dirs: tuple[str, ...] = ()) -> list[dict]:
    infos = []
    for path in list_supported_files(directory, recursive=recursive, exclude_dirs=exclude_dirs):
        if not os.path.exists(path):
            continue
        infos.append({
            "name": os.path.basename(path),
            "path": os.path.abspath(path),
            "relpath": _normalize_relpath(path),
            "mtime": os.path.getmtime(path),
            "size": os.path.getsize(path),
            "sha256": get_or_compute_file_hash(path),
        })
    return sorted(infos, key=lambda x: x["mtime"], reverse=True)


def build_hash_lookup(directory: str, recursive: bool = False, exclude_dirs: tuple[str, ...] = ()) -> dict[str, list[dict]]:
    lookup: dict[str, list[dict]] = {}
    for info in build_file_infos(directory, recursive=recursive, exclude_dirs=exclude_dirs):
        lookup.setdefault(info["sha256"], []).append(info)
    return lookup


def get_ingest_state() -> dict:
    data = _load_json(INGEST_STATE_PATH, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("force_rerun_paths", [])
    data.setdefault("confirm_force_rerun_path", "")
    return data


def save_ingest_state(state: dict) -> None:
    payload = {
        "force_rerun_paths": sorted(set(state.get("force_rerun_paths", []))),
        "confirm_force_rerun_path": state.get("confirm_force_rerun_path", ""),
    }
    _save_json(INGEST_STATE_PATH, payload)


def clear_force_rerun_for_missing_files() -> None:
    state = get_ingest_state()
    kept = []
    for relpath in state.get("force_rerun_paths", []):
        if os.path.exists(resolve_path(relpath)):
            kept.append(relpath)
    state["force_rerun_paths"] = kept
    if state.get("confirm_force_rerun_path") and not os.path.exists(resolve_path(state["confirm_force_rerun_path"])):
        state["confirm_force_rerun_path"] = ""
    save_ingest_state(state)


def mark_force_rerun(path: str) -> None:
    state = get_ingest_state()
    relpath = _normalize_relpath(path)
    paths = set(state.get("force_rerun_paths", []))
    paths.add(relpath)
    state["force_rerun_paths"] = sorted(paths)
    state["confirm_force_rerun_path"] = ""
    save_ingest_state(state)


def unmark_force_rerun(path: str) -> None:
    state = get_ingest_state()
    relpath = _normalize_relpath(path)
    state["force_rerun_paths"] = [p for p in state.get("force_rerun_paths", []) if p != relpath]
    if state.get("confirm_force_rerun_path") == relpath:
        state["confirm_force_rerun_path"] = ""
    save_ingest_state(state)


def is_force_rerun(path: str) -> bool:
    relpath = _normalize_relpath(path)
    return relpath in set(get_ingest_state().get("force_rerun_paths", []))


def set_confirm_force_rerun(path: str) -> None:
    state = get_ingest_state()
    state["confirm_force_rerun_path"] = _normalize_relpath(path)
    save_ingest_state(state)


def clear_confirm_force_rerun() -> None:
    state = get_ingest_state()
    state["confirm_force_rerun_path"] = ""
    save_ingest_state(state)


def get_confirm_force_rerun_path() -> str:
    return get_ingest_state().get("confirm_force_rerun_path", "")


def next_available_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    counter = 1
    while True:
        candidate = f"{stem}_{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1
