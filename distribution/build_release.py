"""Build CognArch source and runtime zip releases.

Module boundaries:
- CognArch/ is copied as the source/application module.
- distribution/ supplies launchers and packaging metadata.
- runtime/ is generated outside the source module and packaged only when requested.

Runtime releases use python-build-standalone as a clean redistributable Python
base, then install CognArch requirements into the packaged runtime.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import stat
import subprocess
import platform
import tarfile
import urllib.request
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "CognArch"
DISTRIBUTION_DIR = REPO_ROOT / "distribution"
DIST_DIR = REPO_ROOT / "dist"
STAGING_DIR = DIST_DIR / "_staging"
ARTIFACT_DIR = DIST_DIR / "releases"
LOCAL_RUNTIME_DIR = REPO_ROOT / "runtime"

PYTHON_BUILD_STANDALONE_API = "https://api.github.com/repos/astral-sh/python-build-standalone/releases"
DEFAULT_PYTHON_BUILD_STANDALONE_RELEASE = "20260414"
DEFAULT_RUNTIME_CONSTRAINTS = APP_DIR / "constraints-runtime.txt"

COMMON_EXCLUDES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "python_env",
    ".git",
    ".claude",
    ".vscode",
    "dist",
    "runtime",
}

APP_EXCLUDE_PATTERNS = [
    "web_config.json",
    "task_status.json",
    "data/*",
    "inputs/*",
    "processed/*",
    "sessions/*",
    "knowledge_base/index.json",
    "knowledge_base/vector_store/*",
    "knowledge_base/notes/*",
    "ingest_smoke_*",
    "start.bat",
    "stop.bat",
    "*.pyc",
    "*.pyo",
]

WINDOWS_ENTRYPOINTS = ["launcher.py", "start.bat", "start.ps1"]
MACOS_ENTRYPOINTS = ["launcher.py", "run.sh", "run.command"]

PACKAGE_TARGETS = {
    "windows": {
        "name": "CognArch-windows-x64",
        "platform": "windows-x64",
        "entrypoints": WINDOWS_ENTRYPOINTS,
        "python_triple": "x86_64-pc-windows-msvc",
        "requirements": APP_DIR / "requirements-windows.txt",
    },
    "macos-arm64": {
        "name": "CognArch-macos-arm64",
        "platform": "macos-arm64",
        "entrypoints": MACOS_ENTRYPOINTS,
        "python_triple": "aarch64-apple-darwin",
        "requirements": APP_DIR / "requirements-macos.txt",
    },
    "macos-x64": {
        "name": "CognArch-macos-x64",
        "platform": "macos-x64",
        "entrypoints": MACOS_ENTRYPOINTS,
        "python_triple": "x86_64-apple-darwin",
        "requirements": APP_DIR / "requirements-macos.txt",
    },
}

TARGET_GROUPS = {
    "all": ["windows", "macos-arm64", "macos-x64"],
    "windows": ["windows"],
    "macos": ["macos-arm64", "macos-x64"],
    "macos-arm64": ["macos-arm64"],
    "macos-x64": ["macos-x64"],
}


def _matches_any(path: Path, patterns: list[str]) -> bool:
    rel = path.as_posix()
    return any(fnmatch.fnmatch(rel, pattern) for pattern in patterns)


def _remove_tree(path: Path) -> None:
    def onerror(func, failed_path, _exc_info):
        try:
            os.chmod(failed_path, stat.S_IWRITE)
            func(failed_path)
        except Exception:
            raise

    shutil.rmtree(path, onerror=onerror)


def _copy_app_module(target_root: Path) -> None:
    target_app = target_root / "CognArch"
    if target_app.exists():
        _remove_tree(target_app)

    def ignore(dir_path: str, names: list[str]) -> set[str]:
        ignored = set()
        base = Path(dir_path)
        for name in names:
            if name in COMMON_EXCLUDES:
                ignored.add(name)
                continue
            rel = (base / name).relative_to(APP_DIR)
            if _matches_any(rel, APP_EXCLUDE_PATTERNS):
                ignored.add(name)
        return ignored

    shutil.copytree(APP_DIR, target_app, ignore=ignore)


def _copy_entrypoints(target_root: Path, names: list[str]) -> None:
    for name in names:
        shutil.copy2(DISTRIBUTION_DIR / name, target_root / name)


def _copy_docs(target_root: Path) -> None:
    for name in ["README.md", "LICENSE", "COMMERCIAL.md"]:
        source = REPO_ROOT / name
        if source.exists():
            shutil.copy2(source, target_root / source.name)

    docs_source = REPO_ROOT / "docs"
    if docs_source.exists():
        shutil.copytree(docs_source, target_root / "docs")

    shutil.copy2(DISTRIBUTION_DIR / "README.md", target_root / "DISTRIBUTION.md")


def _unique_staging_root(package_name: str) -> Path:
    base = STAGING_DIR / package_name
    if not base.exists():
        base.mkdir(parents=True)
        return base
    for idx in range(1, 1000):
        candidate = STAGING_DIR / f"{package_name}-{idx}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
    raise RuntimeError(f"Unable to allocate staging directory for {package_name}")


def _assert_under(path: Path, root: Path) -> None:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"Refusing to clean path outside {resolved_root}: {resolved_path}") from exc


def _clean_staging() -> None:
    if not STAGING_DIR.exists():
        return
    _assert_under(STAGING_DIR, DIST_DIR)
    try:
        _remove_tree(STAGING_DIR)
    except OSError as exc:
        print(f"[CognArch] Could not fully clean staging directory; continuing with a fresh variant: {exc}")


def _prune_release_variants(package_name: str, keep: Path) -> None:
    if not ARTIFACT_DIR.exists():
        return
    pattern = re.compile(rf"^{re.escape(package_name)}(?:-\d+)?\.zip$")
    keep_path = keep.resolve()
    for candidate in ARTIFACT_DIR.glob("*.zip"):
        if candidate.resolve() == keep_path or not pattern.match(candidate.name):
            continue
        try:
            candidate.unlink()
        except PermissionError:
            try:
                candidate.chmod(stat.S_IREAD | stat.S_IWRITE)
                candidate.unlink()
            except PermissionError:
                print(f"[CognArch] Skipping locked release artifact: {candidate}")


def _zip_dir(source_dir: Path, zip_path: Path, package_name: str) -> Path:
    if zip_path.exists():
        try:
            zip_path.unlink()
        except PermissionError:
            stem = zip_path.stem
            for idx in range(1, 100):
                candidate = zip_path.with_name(f"{stem}-{idx}.zip")
                if not candidate.exists():
                    zip_path = candidate
                    break

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file_path in source_dir.rglob("*"):
            if not file_path.is_file():
                continue
            arcname = f"{package_name}/{file_path.relative_to(source_dir).as_posix()}"
            info = zipfile.ZipInfo.from_file(file_path, arcname)
            if file_path.name in {"run.sh", "run.command"}:
                info.external_attr = 0o755 << 16
            with open(file_path, "rb") as f:
                zf.writestr(info, f.read())
    return zip_path


def _runtime_python_candidates(runtime_root: Path, platform_key: str) -> list[Path]:
    if platform_key.startswith("windows"):
        return [
            runtime_root / "python.exe",
            runtime_root / "python" / "python.exe",
            runtime_root / "install" / "python.exe",
        ]
    return [
        runtime_root / "bin" / "python3",
        runtime_root / "bin" / "python",
        runtime_root / "python" / "bin" / "python3",
        runtime_root / "python" / "bin" / "python",
        runtime_root / "install" / "bin" / "python3",
        runtime_root / "install" / "bin" / "python",
    ]


def _find_runtime_python(runtime_root: Path, platform_key: str) -> Path:
    for candidate in _runtime_python_candidates(runtime_root, platform_key):
        if candidate.exists():
            return candidate
    raise RuntimeError(f"Packaged Python executable not found under {runtime_root}")


def _find_runtime_root(extract_root: Path, platform_key: str) -> Path:
    candidates = [extract_root]
    candidates.extend(path for path in extract_root.rglob("*") if path.is_dir())
    for candidate in sorted(candidates, key=lambda item: len(item.parts)):
        try:
            _find_runtime_python(candidate, platform_key)
            return candidate
        except RuntimeError:
            continue
    raise RuntimeError(f"No Python runtime root found under {extract_root}")


def _copy_runtime_dir(runtime_source: Path, target_root: Path, platform_key: str) -> Path:
    runtime_target = target_root / "runtime"
    if runtime_target.exists():
        _remove_tree(runtime_target)
    shutil.copytree(runtime_source, runtime_target)
    return _find_runtime_python(runtime_target, platform_key)


def _copy_runtime_to_path(runtime_source: Path, runtime_target: Path, platform_key: str) -> Path:
    if runtime_target.exists():
        _remove_tree(runtime_target)
    shutil.copytree(runtime_source, runtime_target)
    return _find_runtime_python(runtime_target, platform_key)


def _download_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "CognArch-release-builder"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "CognArch-release-builder"})
    with urllib.request.urlopen(request, timeout=120) as response, open(destination, "wb") as output:
        shutil.copyfileobj(response, output)


def _select_python_build_asset(package_def: dict, python_version: str, release: str) -> tuple[str, str]:
    release_url = (
        f"{PYTHON_BUILD_STANDALONE_API}/latest"
        if release == "latest"
        else f"{PYTHON_BUILD_STANDALONE_API}/tags/{release}"
    )
    data = _download_json(release_url)
    triple = package_def["python_triple"]
    prefix = f"cpython-{python_version}."

    candidates = []
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if not name.endswith(".tar.gz"):
            continue
        if prefix not in name or triple not in name:
            continue
        if "install_only_stripped" not in name:
            continue
        if "debug" in name or "freethreaded" in name:
            continue
        candidates.append((name, asset["browser_download_url"]))

    if not candidates:
        raise RuntimeError(
            f"No python-build-standalone asset found for Python {python_version} and {triple}"
        )
    candidates.sort()
    return candidates[-1]


def _install_requirements(runtime_python: Path, requirements_path: Path, constraints_path: Path | None) -> None:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    try:
        subprocess.check_call([str(runtime_python), "-m", "ensurepip", "--upgrade"], env=env)
    except subprocess.CalledProcessError:
        pass

    command = [
        str(runtime_python),
        "-m",
        "pip",
        "install",
        "--no-warn-script-location",
    ]
    if constraints_path and constraints_path.exists():
        command.extend(["-c", str(constraints_path)])
    command.extend(["-r", str(requirements_path)])
    subprocess.check_call(command, env=env)


def _prepare_python_build_runtime(target_root: Path, package_def: dict, args: argparse.Namespace) -> tuple[Path, str]:
    work_dir = target_root / "_runtime_work"
    extract_dir = work_dir / "extract"
    work_dir.mkdir(parents=True, exist_ok=True)

    asset_name, asset_url = _select_python_build_asset(
        package_def,
        python_version=args.python_version,
        release=args.python_build_standalone_release,
    )
    archive_path = work_dir / asset_name
    print(f"Downloading {asset_name}")
    _download_file(asset_url, archive_path)

    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(extract_dir, filter="data")

    runtime_root = _find_runtime_root(extract_dir, package_def["platform"])
    runtime_python = _copy_runtime_dir(runtime_root, target_root, package_def["platform"])
    _remove_tree(work_dir)
    return runtime_python, f"python-build-standalone {args.python_build_standalone_release} ({asset_name})"


def _prepare_python_build_runtime_at(runtime_target: Path, package_def: dict, args: argparse.Namespace) -> tuple[Path, str]:
    work_dir = DIST_DIR / "_runtime_work" / package_def["platform"]
    extract_dir = work_dir / "extract"
    if work_dir.exists():
        _remove_tree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    asset_name, asset_url = _select_python_build_asset(
        package_def,
        python_version=args.python_version,
        release=args.python_build_standalone_release,
    )
    archive_path = work_dir / asset_name
    print(f"Downloading {asset_name}")
    _download_file(asset_url, archive_path)

    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(extract_dir, filter="data")

    runtime_root = _find_runtime_root(extract_dir, package_def["platform"])
    runtime_python = _copy_runtime_to_path(runtime_root, runtime_target, package_def["platform"])
    _remove_tree(work_dir)
    return runtime_python, f"python-build-standalone {args.python_build_standalone_release} ({asset_name})"


def _prepare_runtime(target_root: Path, package_def: dict, args: argparse.Namespace) -> tuple[Path | None, str]:
    if args.runtime_dir:
        runtime_python = _copy_runtime_dir(args.runtime_dir.resolve(), target_root, package_def["platform"])
        _install_requirements(runtime_python, package_def["requirements"], args.constraints)
        return runtime_python, f"local runtime directory: {args.runtime_dir}"

    if not args.with_runtime:
        return None, "not included"

    runtime_python, runtime_source = _prepare_python_build_runtime(target_root, package_def, args)
    _install_requirements(runtime_python, package_def["requirements"], args.constraints)
    return runtime_python, runtime_source


def _run_capture(command: list[str]) -> str:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return result.stdout.strip() if result.returncode == 0 else ""


def _pip_packages(runtime_python: Path) -> list[dict[str, str]]:
    raw = _run_capture([str(runtime_python), "-m", "pip", "list", "--format=json"])
    if not raw:
        return []
    packages = json.loads(raw)
    enriched = []
    for package in packages:
        name = package.get("name", "")
        show = _run_capture([str(runtime_python), "-m", "pip", "show", name])
        fields = {}
        for line in show.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
        enriched.append({
            "name": name,
            "version": package.get("version", ""),
            "license": fields.get("license", "UNKNOWN") or "UNKNOWN",
            "home": fields.get("home-page", "") or fields.get("project-url", ""),
        })
    return sorted(enriched, key=lambda item: item["name"].lower())


def _write_third_party_notices(
    target_root: Path,
    package_def: dict,
    runtime_python: Path | None,
    runtime_source: str,
    constraints_path: Path | None,
) -> None:
    lines = [
        "# Third-Party Notices",
        "",
        "This archive may include third-party software and model assets.",
        "CognArch's own noncommercial license does not change third-party license terms.",
        "",
        "## Packaged Python Runtime",
        "",
    ]

    if runtime_python:
        python_version = _run_capture([str(runtime_python), "-c", "import sys; print(sys.version.replace('\\n', ' '))"])
        constraints_label = "not used"
        if constraints_path:
            try:
                constraints_label = f"`CognArch/{constraints_path.relative_to(APP_DIR).as_posix()}`"
            except ValueError:
                constraints_label = str(constraints_path)
        lines.extend([
            f"- Platform package: {package_def['name']}",
            f"- Runtime source: {runtime_source}",
            f"- Runtime constraints: {constraints_label}",
            f"- Python: {python_version or 'UNKNOWN'}",
            "- Python license: Python Software Foundation License; see upstream Python notices where applicable.",
            "",
            "## Installed Python Packages",
            "",
            "| Package | Version | License | Home |",
            "|---|---:|---|---|",
        ])
        for package in _pip_packages(runtime_python):
            lines.append(
                f"| {package['name']} | {package['version']} | {package['license']} | {package['home']} |"
            )
    else:
        lines.extend([
            "- No packaged Python runtime is included in this source-style archive.",
            "- Dependencies are installed by the launcher into a local `.venv/` when needed.",
        ])

    lines.extend([
        "",
        "## Bundled Model Assets",
        "",
        "- `CognArch/models/all-MiniLM-L6-v2`: Apache-2.0, from `sentence-transformers/all-MiniLM-L6-v2`.",
        "",
    ])

    (target_root / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines), encoding="utf-8")


def build_package(package_def: dict, args: argparse.Namespace) -> Path:
    package_name = package_def["name"] + ("-runtime" if args.with_runtime or args.runtime_dir else "")
    staging_root = _unique_staging_root(package_name)

    _copy_app_module(staging_root)
    _copy_entrypoints(staging_root, package_def["entrypoints"])
    _copy_docs(staging_root)
    runtime_python, runtime_source = _prepare_runtime(staging_root, package_def, args)
    _write_third_party_notices(staging_root, package_def, runtime_python, runtime_source, args.constraints)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = ARTIFACT_DIR / f"{package_name}.zip"
    return _zip_dir(staging_root, zip_path, package_name)


def _local_target_from_host() -> str:
    if os.name == "nt":
        return "windows"
    if platform.system().lower() == "darwin":
        machine = platform.machine().lower()
        return "macos-arm64" if machine in {"arm64", "aarch64"} else "macos-x64"
    raise RuntimeError("Local runtime install is supported only on Windows and macOS.")


def install_local_runtime(args: argparse.Namespace) -> Path:
    target = args.target
    if target in {"all", "macos"}:
        target = _local_target_from_host()
    package_def = PACKAGE_TARGETS[target]

    runtime_python, runtime_source = _prepare_python_build_runtime_at(LOCAL_RUNTIME_DIR, package_def, args)
    _install_requirements(runtime_python, package_def["requirements"], args.constraints)
    print(f"Installed local runtime at: {LOCAL_RUNTIME_DIR}")
    print(f"Runtime source: {runtime_source}")
    return runtime_python


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build CognArch release zip packages.")
    parser.add_argument(
        "--target",
        choices=sorted(TARGET_GROUPS),
        default="all",
        help="Release target to build.",
    )
    parser.add_argument(
        "--with-runtime",
        action="store_true",
        help="Bundle python-build-standalone and install platform requirements.",
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        help="Copy an existing redistributable Python runtime directory instead of downloading one.",
    )
    parser.add_argument(
        "--python-version",
        default="3.12",
        help="Python minor version to select from python-build-standalone assets.",
    )
    parser.add_argument(
        "--python-build-standalone-release",
        default=DEFAULT_PYTHON_BUILD_STANDALONE_RELEASE,
        help="Pinned python-build-standalone release tag, or 'latest' for ad hoc builds.",
    )
    parser.add_argument(
        "--constraints",
        type=Path,
        default=DEFAULT_RUNTIME_CONSTRAINTS,
        help="Constraints file used when installing runtime dependencies.",
    )
    parser.add_argument(
        "--clean-staging",
        action="store_true",
        help="Remove dist/_staging before building.",
    )
    parser.add_argument(
        "--prune-releases",
        action="store_true",
        help="After a successful build, remove older numbered zip variants for the built package names.",
    )
    parser.add_argument(
        "--install-local-runtime",
        action="store_true",
        help="Install the same packaged runtime used by release zips into repo-root runtime/ and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.runtime_dir and not args.runtime_dir.exists():
        raise SystemExit(f"Runtime directory does not exist: {args.runtime_dir}")
    if args.constraints and not args.constraints.exists():
        raise SystemExit(f"Constraints file does not exist: {args.constraints}")

    if args.install_local_runtime:
        runtime_python = install_local_runtime(args)
        print(f"Runtime Python: {runtime_python}")
        return

    DIST_DIR.mkdir(exist_ok=True)
    if args.clean_staging:
        _clean_staging()
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    outputs = []
    for target in TARGET_GROUPS[args.target]:
        package_def = PACKAGE_TARGETS[target]
        package_name = package_def["name"] + ("-runtime" if args.with_runtime or args.runtime_dir else "")
        output = build_package(package_def, args)
        outputs.append(output)
        if args.prune_releases:
            _prune_release_variants(package_name, output)

    print("Built release packages:")
    for path in outputs:
        print(f" - {path}")


if __name__ == "__main__":
    main()
