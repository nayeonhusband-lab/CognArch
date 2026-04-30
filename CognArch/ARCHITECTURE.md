# CognArch Architecture

Last Updated: 2026-04-29

## 1. What It Is
CognArch is a local knowledge-base tool for heavy document workflows:
**Ingest -> Index -> Retrieve -> Generate**, grounded strictly in your own corpus.

## 2. Runtime Stack

| Layer | Files | Role |
|---|---|---|
| Web | `web_app.py` | Streamlit UI (chat, file ingest, config, sidebar ops) |
| Init | `orchestrator.py` | Directory bootstrap, worker instantiation, session creation |
| Analytical | `analytical_worker.py` | File parsing, OCR, skill routing, structured note generation |
| Generative | `generative_worker.py` | Retrieval + synthesis (Q&A, lit review, outline tasks) |
| Multi-Round | `generative_multi_round_workflow.py` | Stage-driven retrieval workflow for outline-based generation |
| Vector Store | `vector_store.py` | Embedding-based semantic retrieval |
| Config | `config.py` | API key / base_url / model preference resolution |
| Paths | `paths.py` | Central APP_ROOT / DATA_ROOT / MODEL_ROOT resolution |

## 3. Data Layout

```text
DATA_ROOT/                  # defaults to CognArch/, override with COGNARCH_HOME
├─ inputs/                  # pending files
├─ processed/               # archived originals
├─ knowledge_base/
│  ├─ notes/                # generated markdown notes
│  ├─ index.json            # metadata registry (auto-maintained)
│  └─ vector_store/         # embeddings + metadata (auto-managed)
└─ sessions/                # per-task outputs and logs

APP_ROOT/
├─ skills/
│  ├─ analytical/           # domain-specific note skills
│  └─ generative/           # generation/synthesis skills
└─ models/all-MiniLM-L6-v2/ # bundled offline embedding model
```

## 4. Core Workflows

### 4.1 Knowledge Reconstruction (Ingest)
1. Upload files via Web UI -> saved to `inputs/`.
2. `RunnerWorker` parses each file (PDF/DOCX/PPTX/MD/XLSX, with OCR fallback).
3. Routes each file to the best analytical skill.
4. Structured markdown note written to `knowledge_base/notes/`.
5. Metadata appended to `knowledge_base/index.json`.
6. Vector store updated incrementally.
7. Original archived to `processed/` with dedup/rerun safeguards.

### 4.2 Chat Generation
1. User types prompt in chat.
2. `GenerativeWorker` performs semantic retrieval (or uses preselected docs).
3. Optional coarse filter + LLM relevance filter applied.
4. LLM generates grounded output with source anchors.
5. Output saved under `sessions/<session_id>/outputs`.

### 4.3 Index Maintenance
- **Sync Vector Store**: differential sync (`vector_store.sync_from_index`) -- adds/updates/removes vectors based on `index.json`.
- **Rebuild Vector Store**: full re-encode from `index.json` (`vector_store.rebuild_vector_store`).
- **Rebuild Knowledge Index** (`RunnerWorker.rebuild_index_json`): validates `index.json` against actual files in `knowledge_base/notes/`. Removes stale entries for deleted notes, reports orphan files. Zero LLM calls.

## 5. Calling Conventions

### Model Selection (DeepSeek V4 API)
All calls use OpenAI-compatible SDK with these patterns:

| Phase | Model | Parameters |
|---|---|---|
| Analytical Map (chunk) | `deepseek-v4-flash` | `temperature=0.1` |
| Analytical Reduce | `deepseek-v4-flash` or `deepseek-v4-pro` (toggle) | `extra_body + reasoning_effort=max` (thinking mode) |
| Generative non-final | `deepseek-v4-flash` | `extra_body + reasoning_effort=max` |
| Generative final / Reduce | `deepseek-v4-pro` (toggle) | `extra_body + reasoning_effort=max` |

Thinking mode restriction: no `temperature`, `top_p`, `presence_penalty`, `frequency_penalty`.

### Direct Worker Calls
Web layer calls workers directly; no LLM-based routing:

```python
# Knowledge reconstruction
orchestrator.create_session()
shared = orchestrator.shared_workspace
runner = orchestrator.workers["RunnerWorker"]
result = runner.execute({"input_dir": SYSTEM_PATHS["inputs"], "user_instruction": ""}, shared)

# Chat generation
orchestrator.create_session()
shared = orchestrator.shared_workspace
gen = orchestrator.workers["GenerativeWorker"]
response = gen.execute({"user_instruction": prompt, "target_filenames": selected_files, "retrieval_doc_cap": cap}, shared)
```

## 6. Configuration

BYOK (Bring Your Own Key):
- Provide DeepSeek API key + base URL in sidebar.
- `web_config.json`: persisted UI config under `DATA_ROOT`; `web_config.example.json` is the Git-safe template.
- `config.py`: runtime resolution and model preference helpers.

## 7. Engineering Notes

- **No Orchestrator LLM routing**: Orchestrator is purely an init container (paths + workers + sessions). It does not call LLMs for task planning.
- **Index.json is append-only with dedup**: `_update_index` overwrites existing entries by filepath, but never removes stale ones. Use "Rebuild Knowledge Index" to clean up.
- **Vector store relies on index.json**: `sync_from_index` and `rebuild_vector_store` read `index.json` as the source of truth. They do not scan the filesystem.
- **File dedup**: `file_hash_cache.py` tracks hashes for `inputs/` and `processed/` with force-rerun controls.
- **Session isolation**: Each task (reconstruction or generation) creates its own session directory under `sessions/`.

## 8. Release Architecture

- `CognArch/` is the source/application module. It should stay copy-pasteable.
- `distribution/` owns launchers and release packaging; release builds copy the needed entrypoints to the zip root.
- Runtime release packages contain a generated `runtime/` Python and installed dependencies. The launcher prefers `runtime/` when present, then falls back to development `.venv/` creation.
- Runtime release inputs are pinned through `distribution/build_release.py` and `constraints-runtime.txt`; refresh them intentionally before cutting a new release.
- Mutable user data defaults to the `CognArch/` folder for portable zip releases, and can be moved with `COGNARCH_HOME`.
- Bundled embedding assets live under `CognArch/models/all-MiniLM-L6-v2` and are included in release zips for offline vector search.
- CognArch is source-available under PolyForm Noncommercial 1.0.0. Commercial use requires separate written authorization.
