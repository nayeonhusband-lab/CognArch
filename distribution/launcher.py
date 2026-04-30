"""Cross-platform CognArch launcher.

This file belongs to the distribution module, not the CognArch source module.
It can run from distribution/ during development, and release builds copy it to
the package root next to the CognArch/ source directory.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


LAUNCHER_DIR = Path(__file__).resolve().parent
RELEASE_ROOT = LAUNCHER_DIR if (LAUNCHER_DIR / "CognArch").exists() else LAUNCHER_DIR.parent
APP_DIR = RELEASE_ROOT / "CognArch"
RUNTIME_DIR = RELEASE_ROOT / "runtime"
VENV_DIR = RELEASE_ROOT / ".venv"
DEFAULT_PORT = 8501


def _runtime_python_candidates() -> list[Path]:
    if os.name == "nt":
        return [
            RUNTIME_DIR / "python.exe",
            RUNTIME_DIR / "python" / "python.exe",
            RUNTIME_DIR / "install" / "python.exe",
        ]
    return [
        RUNTIME_DIR / "bin" / "python3",
        RUNTIME_DIR / "bin" / "python",
        RUNTIME_DIR / "python" / "bin" / "python3",
        RUNTIME_DIR / "python" / "bin" / "python",
        RUNTIME_DIR / "install" / "bin" / "python3",
        RUNTIME_DIR / "install" / "bin" / "python",
    ]


def _runtime_python() -> Path | None:
    for candidate in _runtime_python_candidates():
        if candidate.exists():
            return candidate
    return None


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _platform_requirements() -> Path:
    if sys.platform == "darwin":
        return APP_DIR / "requirements-macos.txt"
    if os.name == "nt":
        return APP_DIR / "requirements-windows.txt"
    return APP_DIR / "requirements.txt"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    base_req = APP_DIR / "requirements.txt"
    if path.name != "requirements.txt" and base_req.exists():
        digest.update(base_req.read_bytes())
    return digest.hexdigest()


def ensure_venv(no_install: bool = False, reinstall: bool = False) -> Path:
    if not APP_DIR.exists():
        raise RuntimeError(f"CognArch source directory not found: {APP_DIR}")

    python_path = _venv_python()
    if not python_path.exists():
        print(f"[CognArch] Creating virtual environment: {VENV_DIR}")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])

    if no_install:
        return python_path

    req_path = _platform_requirements()
    if not req_path.exists():
        req_path = APP_DIR / "requirements.txt"

    stamp_path = VENV_DIR / ".cognarch_requirements_stamp"
    req_hash = _file_digest(req_path)
    installed_hash = stamp_path.read_text(encoding="utf-8").strip() if stamp_path.exists() else ""

    if reinstall or installed_hash != req_hash:
        print(f"[CognArch] Installing dependencies from {req_path.name}")
        subprocess.check_call([
            str(python_path),
            "-m",
            "pip",
            "install",
            "-r",
            str(req_path),
        ])
        stamp_path.write_text(req_hash, encoding="utf-8")

    return python_path


def resolve_python(no_install: bool = False, reinstall: bool = False) -> Path:
    runtime_python = _runtime_python()
    if runtime_python:
        if reinstall:
            print("[CognArch] Packaged runtime detected; --reinstall is ignored.")
        return runtime_python
    return ensure_venv(no_install=no_install, reinstall=reinstall)


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def choose_port(requested: int | None = None) -> int:
    if requested:
        if not _port_is_free(requested):
            raise RuntimeError(f"Requested port {requested} is already in use.")
        return requested

    env_port = os.environ.get("COGNARCH_PORT", "").strip()
    if env_port:
        port = int(env_port)
        if not _port_is_free(port):
            raise RuntimeError(f"COGNARCH_PORT {port} is already in use.")
        return port

    for port in range(DEFAULT_PORT, DEFAULT_PORT + 50):
        if _port_is_free(port):
            return port
    raise RuntimeError("No available local port found in range 8501-8550.")


def wait_for_server(port: int, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def run_streamlit(args: argparse.Namespace) -> int:
    python_path = resolve_python(no_install=args.no_install, reinstall=args.reinstall)
    port = choose_port(args.port)
    url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env.setdefault("PYTHONUTF8", "1")

    command = [
        str(python_path),
        "-m",
        "streamlit",
        "run",
        str(APP_DIR / "web_app.py"),
        "--server.port",
        str(port),
        "--browser.gatherUsageStats",
        "false",
    ]

    print(f"[CognArch] Starting Streamlit at {url}")
    process = subprocess.Popen(command, cwd=str(APP_DIR), env=env)

    try:
        if wait_for_server(port) and not args.no_browser:
            webbrowser.open(url)
        elif not args.no_browser:
            print("[CognArch] Server is still warming up; open this URL manually if needed:")
            print(url)

        return process.wait()
    except KeyboardInterrupt:
        print("\n[CognArch] Stopping...")
        process.terminate()
        try:
            return process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch CognArch local web app.")
    parser.add_argument("--port", type=int, help="Port to use. Defaults to COGNARCH_PORT or first free port from 8501.")
    parser.add_argument("--no-browser", action="store_true", help="Start the server without opening a browser.")
    parser.add_argument("--no-install", action="store_true", help="Reuse .venv without installing requirements.")
    parser.add_argument("--reinstall", action="store_true", help="Force reinstall requirements into .venv.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run_streamlit(parse_args()))
