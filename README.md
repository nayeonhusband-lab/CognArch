# CognArch

CognArch is an out-of-the-box, local-first knowledge base powered by discipline-specific cognitive models (e.g., CS, social sciences, law). Generate reading notes at scale, build reusable knowledge structures, and power precise Q&A, literature reviews, ideation, and writing—all grounded in your own corpus.
一款开箱即用的本地化个人知识库应用，内置多学科认知模型（如计算机、社科、法学）。支持批量生成阅读笔记，构建可复用知识体系，并基于自有语料实现精准问答、文献综述、创意构思与写作。

Last updated: 2026-04-30

## License

CognArch is available for noncommercial use under the PolyForm Noncommercial
License 1.0.0. It is not distributed under an OSI-approved open source license.

Commercial use requires a separate written license from the copyright holder.
See `LICENSE` and `COMMERCIAL.md`.

The Chinese user guide lives at `docs/USER_GUIDE.zh-CN.txt`.
The GitHub deployment checklist lives at `docs/GITHUB_DEPLOYMENT_PLAN.zh-CN.md`.

## Source Checkout Layout

```text
CognArch/       # source/application module
distribution/   # launcher and release packaging module
docs/           # user-facing guides and release notes
runtime/        # ignored local packaged runtime; matches GitHub Release runtime layout
```

`CognArch/` is designed to be copy-pasteable as the app source module. Release
and bootstrap concerns live in `distribution/` so app changes do not need to
touch packaging code.

Release zips contain `CognArch/`, platform entrypoints, legal notices, and
optionally a packaged `runtime/` Python. They do not include the
`distribution/` source folder.

## Launch

For the same runtime model used by GitHub Release packages, install a local
packaged runtime once:

```bash
python distribution/build_release.py --install-local-runtime --target windows
```

Then launch:

```bash
python distribution/launcher.py
```

Windows double-click launch from the repository root:

```text
start.bat
```

The launcher uses repo-root `runtime/` first, starts Streamlit, and opens a
local browser tab. If `runtime/` is absent, it falls back to creating `.venv/`
from the system Python for development only.

Runtime release packages include their own `runtime/` Python and dependencies.
For those packages, the same launcher uses the packaged runtime first and does
not require users to install Python separately.

Release packages place platform entrypoints at the zip root:

- Windows: `start.bat` or `start.ps1`
- macOS: `run.command` or `run.sh`

Useful environment variables:

- `COGNARCH_HOME`: optional writable data root for inputs, outputs, indexes, and config.
- `COGNARCH_PORT`: optional port override. Defaults to the first free port from `8501`.
- `COGNARCH_ANTIWORD`: optional explicit path to `antiword` for legacy `.doc` parsing.
- `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`: optional config fallback.

## CLI

```bash
python CognArch/main.py ingest
python CognArch/main.py generate --prompt "..."
python CognArch/main.py status <session_id>
```

## Release Builds

```bash
python distribution/build_release.py --target all
```

Runtime packages are built per platform:

```bash
python distribution/build_release.py --target windows --with-runtime
python distribution/build_release.py --target macos-arm64 --with-runtime
python distribution/build_release.py --target macos-x64 --with-runtime
```

Runtime builds default to a pinned `python-build-standalone` release and
`CognArch/constraints-runtime.txt` so GitHub Release artifacts are reproducible.
Use `--clean-staging --prune-releases` to keep local build output tidy.

Release zips are written under `dist/releases/`. They include the bundled
embedding model at `CognArch/models/all-MiniLM-L6-v2` so vector search can run
offline.

Release zips exclude `.venv/`, API keys, generated sessions, uploaded/processed
documents, and vector-store runtime files. Runtime packages also include
`THIRD_PARTY_NOTICES.md` for the packaged Python runtime, installed Python
packages, and bundled model assets.
