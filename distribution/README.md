# CognArch Distribution Module

This folder owns release and bootstrap concerns. The `CognArch/` folder should
remain copy-pasteable as the source/application module.

Last updated: 2026-04-30

## Module Boundaries

- `CognArch/`: application source, skills, bundled embedding model, and app-level requirements.
- `distribution/`: launchers, release build scripts, and packaging documentation.
- `docs/`: user-facing guides copied into release zips.
- `runtime/`: ignored local or packaged Python runtime; created by the release builder.

Do not move launcher or packaging code into `CognArch/`. Release builds copy
the needed entrypoints from this module to the final zip root.

## Development Launch

From the repository root:

```bash
python distribution/build_release.py --install-local-runtime --target windows
```

Then either run:

```bash
python distribution/launcher.py
```

or double-click the thin repository-root wrapper:

```text
start.bat
```

The launcher first checks for a packaged `runtime/` Python next to
`launcher.py` (or at the repository root during development). If present, it
starts the existing Streamlit UI with that runtime. If not present, it creates
`.venv/`, installs the platform requirements from `CognArch/`, starts
Streamlit, and opens the default browser.

`distribution/start.bat` and `distribution/start.ps1` are the canonical Windows
entrypoint module. The repository-root `start.bat` / `start.ps1` files are thin
wrappers that delegate into `distribution/` so source changes inside
`CognArch/` do not affect the startup entrypoint.

## Build Releases

```bash
python distribution/build_release.py --target all
```

Outputs:

- `dist/releases/CognArch-windows-x64.zip`
- `dist/releases/CognArch-macos-arm64.zip`
- `dist/releases/CognArch-macos-x64.zip`

Runtime releases add `--with-runtime` and are built on the matching platform:

```bash
python distribution/build_release.py --target windows --with-runtime
python distribution/build_release.py --target macos-arm64 --with-runtime
python distribution/build_release.py --target macos-x64 --with-runtime
```

Runtime builds default to the pinned `python-build-standalone` release
`20260414` and install dependencies through `CognArch/constraints-runtime.txt`.
Use `--clean-staging` before a build and `--prune-releases` after a successful
build to keep local `dist/` output from accumulating stale staging directories
and numbered zip variants.

Runtime outputs:

- `dist/releases/CognArch-windows-x64-runtime.zip`
- `dist/releases/CognArch-macos-arm64-runtime.zip`
- `dist/releases/CognArch-macos-x64-runtime.zip`

If an existing zip is locked by the OS, the builder writes a numbered variant
such as `CognArch-windows-x64-1.zip`.

The release zips include the `CognArch/` source module, `docs/`, and the
required platform entrypoints at the zip root. Runtime zips also include a
generated `runtime/` directory and `THIRD_PARTY_NOTICES.md`. They do not include
`.venv/`, app-module helper scripts, user data, API keys,
sessions, or generated vector stores.
