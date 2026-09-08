# h3chain API (current)

> status: current — matches src/server.py v0.2.0

h3chain is a single-file FastAPI service that wraps ComfyUI's MiniMax H3
Extender node chain into an unlimited-length, clip-by-clip generation loop
with a simple web frontend. Runs on port 6008 next to ComfyUI on 6006.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | /api/projects | list projects in workspace/ |
| POST | /api/projects | create project (six-section template story.json) |
| GET  | /api/projects/{p}/story | read story.json |
| PUT  | /api/projects/{p}/story | validate + save story.json (flock) |
| POST | /api/projects/{p}/refs | upload reference image (PNG/JPG ≤ 20MB) |
| POST | /api/projects/{p}/gen | generate next unvalidated clip |
| POST | /api/projects/{p}/keep | lock clip (default: first pending; continuous-chain enforced) |
| POST | /api/projects/{p}/redo | redo clip with fresh seed (invalidates tail) |
| GET  | /api/projects/{p}/status | progress: X/Y validated + per-clip rows |
| POST | /api/projects/{p}/export | fill missing clips then FinalDecode merge |
| GET  | /api/projects/{p}/events | SSE stream: progress / heartbeat / done / error |
| GET  | /api/projects/{p}/output/{file} | download generated mp4 |
| GET  | /api/projects/{p}/output | list output artifacts (mtime desc, for UI auto-preview) |
| GET  | /api/projects/{p}/refs | list uploaded reference images (UI thumbnails) |
| GET  | /api/projects/{p}/refs/{file} | serve reference image binary (UI preview modal) |
| DELETE | /api/projects/{p} | delete project directory (story/refs/output) |

## Error types (ApiResponse.error)

ProjectNotFoundError, DirectoryExistsError, StoryInvalidError,
StoryLockError, RefUploadError, RefImageMissingError,
ComfyUIUnreachableError, ExtenderNodeMissingError, SubmissionError,
ExecutionError, ClipNotFoundError, OutOfOrderKeepError, AllValidatedError.

Errors return `{"ok": false, "error": "<short>", "error_detail": "<English detail>"}`.

## Response shape

Every response: `{"ok": bool, "error": str?, "error_detail": str?, "data": {...}}`.
`data` always includes a `next` step hint (`gen` / `export` / `keep or redo`).

## Generation loop

gen → watch SSE → keep (lock, disk-cache next time) or redo (fresh seed,
tail invalidated) → gen ... → export merges the whole chain via
MiniMaxH3MotionContextDiskFinalDecode.

## Environment

- H3CHAIN_WORKSPACE (default: src/workspace/)
- H3CHAIN_COMFY_HOST / H3CHAIN_COMFY_PORT (default 127.0.0.1:6006)
- H3CHAIN_TIMEOUT seconds (default 1800)

## Interface names

| Spec interface | Route |
|----------------|-------|
| `api_list_projects` | GET /api/projects |
| `api_create_project` | POST /api/projects |
| `api_get_story` | GET /api/projects/{p}/story |
| `api_update_story` | PUT /api/projects/{p}/story |
| `api_upload_ref` | POST /api/projects/{p}/refs |
| `api_gen` | POST /api/projects/{p}/gen |
| `api_keep` | POST /api/projects/{p}/keep |
| `api_redo` | POST /api/projects/{p}/redo |
| `api_status` | GET /api/projects/{p}/status |
| `api_export` | POST /api/projects/{p}/export |
| `api_events` | GET /api/projects/{p}/events |
| `api_download` | GET /api/projects/{p}/output/{file} |
| `api_list_output` | GET /api/projects/{p}/output |
| `api_list_refs` | GET /api/projects/{p}/refs |
| `api_view_ref` | GET /api/projects/{p}/refs/{file} |
| `api_delete_project` | DELETE /api/projects/{p} |
