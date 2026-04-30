"""CognArch command line interface.

The Streamlit UI is the primary product surface. This CLI is a thin,
scriptable wrapper over the same workers used by the UI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime


def _configure_cli_stdio() -> None:
    """Keep direct Windows CLI runs from crashing on emoji/log output."""
    os.environ.setdefault("PYTHONUTF8", "1")

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_configure_cli_stdio()

from orchestrator import Orchestrator, SYSTEM_PATHS
from paths import get_task_status_path


TASK_STATUS_FILE = get_task_status_path()


def load_task_status() -> dict:
    if os.path.exists(TASK_STATUS_FILE):
        try:
            with open(TASK_STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_task_status(status_dict: dict) -> None:
    os.makedirs(os.path.dirname(TASK_STATUS_FILE), exist_ok=True)
    with open(TASK_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status_dict, f, ensure_ascii=False, indent=2)


def update_task_status(task_id: str, status: str, message: str = "", result_path: str = "") -> None:
    tasks = load_task_status()
    tasks[task_id] = {
        "status": status,
        "message": message,
        "result_path": result_path,
        "updated_at": datetime.now().isoformat(),
    }
    save_task_status(tasks)


def _print_result(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print("\n" + "=" * 50)
    print("CognArch CLI result")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def run_ingest(args: argparse.Namespace) -> dict:
    orchestrator = Orchestrator()
    session_id = orchestrator.create_session()
    shared = orchestrator.shared_workspace

    runner = orchestrator.workers["RunnerWorker"]
    result = runner.execute({
        "input_dir": args.input_dir or SYSTEM_PATHS["inputs"],
        "user_instruction": args.instruction or "",
    }, shared)

    result["session_id"] = session_id
    result["session_dir"] = shared.get("session_dir", "")
    update_task_status(session_id, result.get("status", "unknown"), result.get("message", ""), result["session_dir"])
    return result


def run_generate(args: argparse.Namespace) -> dict:
    orchestrator = Orchestrator()
    session_id = orchestrator.create_session()
    shared = orchestrator.shared_workspace

    worker = orchestrator.workers["GenerativeWorker"]
    result = worker.execute({
        "user_instruction": args.prompt,
        "target_filenames": args.files or None,
        "retrieval_doc_cap": args.retrieval_doc_cap,
    }, shared)

    result["session_id"] = session_id
    result["session_dir"] = shared.get("session_dir", "")
    update_task_status(session_id, result.get("status", "unknown"), result.get("message", ""), result["session_dir"])
    return result


def run_status(args: argparse.Namespace) -> dict:
    tasks = load_task_status()
    item = tasks.get(args.task_id)
    if not item:
        return {"status": "not_found", "message": f"Task {args.task_id} does not exist"}
    return item


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CognArch CLI - local knowledge reconstruction and generation",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON only.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Process files from inputs/ into the knowledge base.")
    ingest.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    ingest.add_argument("--input-dir", default=SYSTEM_PATHS["inputs"], help="Directory containing files to ingest.")
    ingest.add_argument("--instruction", default="", help="Optional reading/analysis instruction.")
    ingest.set_defaults(func=run_ingest)

    generate = subparsers.add_parser("generate", help="Generate an output from the knowledge base.")
    generate.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    generate.add_argument("--prompt", required=True, help="User instruction for generation.")
    generate.add_argument("-f", "--files", nargs="*", help="Optional preselected note filenames or paths.")
    generate.add_argument("--retrieval-doc-cap", type=int, default=50, help="Automatic retrieval document cap.")
    generate.set_defaults(func=run_generate)

    status = subparsers.add_parser("status", help="Read a previously recorded task status.")
    status.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    status.add_argument("task_id", help="Session/task id returned by ingest or generate.")
    status.set_defaults(func=run_status)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = args.func(args)
    _print_result(result, args.json)


if __name__ == "__main__":
    main()
