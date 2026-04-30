# CognArch Agent Notes

Last updated: 2026-04-30

## Module Boundaries

- `CognArch/` is the source/application module. Keep it copy-pasteable.
- `distribution/` is the release/bootstrap module. Put launchers, zip builders, and packaging docs here.
- `docs/` is for human-facing guides copied into release zips.
- `runtime/` is an ignored local or packaged Python runtime. Source code should discover it through `distribution/launcher.py`, not by hard-coded paths.

## Runtime Paths

- Use `CognArch/paths.py` for all app/data/model path decisions.
- `APP_ROOT` contains source resources such as `skills/` and `models/`.
- `DATA_ROOT` defaults to `CognArch/` and can be overridden with `COGNARCH_HOME`.
- `COGNARCH_ANTIWORD` can point to an `antiword` executable when legacy `.doc` parsing is needed off-PATH.
- Mutable files such as `web_config.json`, `inputs/`, `processed/`, `sessions/`, `knowledge_base/index.json`, and `knowledge_base/vector_store/` are user data and should remain ignored.

## Launch And Packaging

- Development launch: `python distribution/launcher.py`.
- Preferred local runtime setup: `python distribution/build_release.py --install-local-runtime --target windows` (or the matching macOS target). This creates ignored repo-root `runtime/` so local use matches release packages.
- Release build: `python distribution/build_release.py --target all`.
- Runtime release builds: `python distribution/build_release.py --target windows --with-runtime`, `--target macos-arm64 --with-runtime`, or `--target macos-x64 --with-runtime`. Runtime builds use the pinned `python-build-standalone` release in `distribution/build_release.py` and `CognArch/constraints-runtime.txt` by default.
- Repository-root `start.bat` and `start.ps1` are thin wrappers only; keep the real Windows startup logic in `distribution/start.bat` and `distribution/start.ps1`.
- Windows release entrypoints are copied from `distribution/start.bat` and `distribution/start.ps1`.
- macOS release entrypoints are copied from `distribution/run.command` and `distribution/run.sh`.
- Do not hard-code Edge, Windows drive letters, or port `8501` as the only possible port.

## Validation

- Fast syntax check:
  `python -c "import pathlib, sys; [compile(p.read_text(encoding='utf-8-sig'), str(p), 'exec') for p in list(pathlib.Path('CognArch').rglob('*.py')) + list(pathlib.Path('distribution').rglob('*.py'))]"`
- CLI smoke:
  `python CognArch/main.py status does-not-exist --json`
- Release verification should confirm zips include `CognArch/models/all-MiniLM-L6-v2` and `THIRD_PARTY_NOTICES.md`, and exclude `.venv/`, `CognArch/start.bat`, `CognArch/stop.bat`, API keys, sessions, uploaded documents, and vector-store runtime files.
