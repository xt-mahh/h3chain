#!/usr/bin/env python3
"""h3chain — MiniMax H3 unlimited-length video generation service (FastAPI).

Single-file service: story.json state machine + ComfyUI Extender
submit/poll/download + SSE progress stream.

Designed for the AutoDL Art app form factor:
  - this service on port 6008
  - ComfyUI (with MiniMax H3 Extender) on port 6006

Starts even without ComfyUI running — generation endpoints check
ComfyUI reachability only when called.
"""
import builtins
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional

# ------------------------------------------------------------------ constants

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
WORKSPACE = Path(os.environ.get("H3CHAIN_WORKSPACE", APP_DIR / "workspace"))
COMFY_HOST = os.environ.get("H3CHAIN_COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("H3CHAIN_COMFY_PORT", "6006"))
TIMEOUT = int(os.environ.get("H3CHAIN_TIMEOUT", "1800"))
HEARTBEAT = 15          # seconds between SSE heartbeats
MAX_REF_SIZE = 20 * 1024 * 1024   # 20MB upload cap
MAX_REFS = 9             # H3 supports ref_1..ref_9
DURATION_MIN, DURATION_MAX = 0.25, 150.0
FRAME_GRID = 17          # duration must land on 17k+5 frames at 24fps

# Six-section prompt skeleton (Ref2VA official structure)
SIX_SECTION_PROMPT = """subject_definitions:
<Picture 1> is [who/what].
<Picture 2> is [optional second subject].

integrated_multimodal_description:
[One paragraph combining every subject above with the camera, light, motion and environment of this clip.]

overall_soundscape:
[Continuous ambient sound of this clip. No dialogue lines here.]

dialogue_and_sound_effects:
A male voice speaks in Mandarin Chinese: "[one short line]" 

non_diegetic_music:
None. No background music, no score, no melodic instrument of any kind. This clip is delivered dry (dialogue/VO + ambience only); music will be added in post-production.

camera_and_shot:
[Camera type, framing, and movement of this clip.]"""

# ---------------------------------------------------------------- errors

class DomainError(Exception):
    """Base for all spec-declared error types (C2-def convention)."""
    def __init__(self, message: str = "", detail: str = ""):
        self.message = message
        self.detail = detail
        super().__init__(message)


# H3ChainError is the alias DomainError; all typed errors inherit it so
# `except H3ChainError` catches every spec-declared error.  The class also
# matches the C2-def convention `class XxxError(DomainError)` via aliasing.
H3ChainError = DomainError


class ProjectNotFoundError(DomainError): pass
class DirectoryExistsError(DomainError): pass
class StoryInvalidError(DomainError): pass
class StoryLockError(DomainError): pass
class RefUploadError(DomainError): pass
class RefImageMissingError(DomainError): pass
class ComfyUIUnreachableError(DomainError): pass
class ExtenderNodeMissingError(DomainError): pass
class SubmissionError(DomainError): pass
class ExecutionError(DomainError): pass
class ClipNotFoundError(DomainError): pass
class OutOfOrderKeepError(DomainError): pass
class AllValidatedError(DomainError): pass


# Typed aliases over builtins (spec declares these as domain errors while
# keeping builtin semantics: HTTP 404 / polling timeout).
class FileNotFoundError(DomainError, builtins.FileNotFoundError): pass
class TimeoutError(DomainError, builtins.TimeoutError): pass


# ---------------------------------------------------------------- storage

def _project_dir(name: str) -> Path:
    pdir = WORKSPACE / name
    if not pdir.exists():
        raise ProjectNotFoundError(
            f"project '{name}' not found",
            f"Create it first: POST /api/projects with name='{name}'.")
    return pdir


def _load_story(pdir: Path) -> dict:
    story_path = pdir / "story.json"
    try:
        story = json.loads(story_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StoryInvalidError(
            "story.json is not valid JSON",
            f"Parse error at line {e.lineno}, column {e.colno}: {e.msg}")
    _validate_story(story)
    return story


def _validate_story(story: dict) -> None:
    """Structural validation per StoryProject schema (B013/B016)."""
    if not isinstance(story, dict):
        raise StoryInvalidError("story must be a JSON object", "")
    clips = story.get("clips")
    if not isinstance(clips, list) or not clips:
        raise StoryInvalidError("story.clips missing or empty",
                                "At least one clip is required (no clips).")
    if len(story.get("refs", [])) > MAX_REFS:
        raise StoryInvalidError("too many reference images",
                                f"H3 supports at most {MAX_REFS} reference images (ref_1..ref_9).")
    for clip in clips:
        cid = clip.get("id", "<no id>")
        prompt = clip.get("prompt", "")
        if not isinstance(prompt, str) or not prompt.strip():
            raise StoryInvalidError(
                f"clip '{cid}' has empty prompt",
                f"empty prompt (clip id: {cid}).")
        dur = clip.get("duration")
        if (not isinstance(dur, (int, float))
                or not (DURATION_MIN <= dur <= DURATION_MAX)):
            raise StoryInvalidError(
                f"clip '{cid}' duration {dur} out of range",
                f"duration must be within {DURATION_MIN}-{DURATION_MAX} seconds "
                f"(17k+5 frame grid at 24fps: 0.25, 0.96, 1.66, ...).")


def _save_story(pdir: Path, story: dict) -> None:
    """Write story.json under an exclusive lock (flock)."""
    story_path = pdir / "story.json"
    lock_path = pdir / ".story.lock"
    lock = open(lock_path, "w")
    try:
        import fcntl
    except ImportError:
        # Windows/dev env: fall back to atomic replace (single writer assumed)
        lock.close()
        tmp = story_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(story_path)
        return
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StoryLockError(
                "write lock held",
                "Another request is writing story.json. Retry in a moment.")
        story_path.write_text(
            json.dumps(story, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        except Exception:
            pass
        lock.close()


# ---------------------------------------------------------------- comfy client

def _comfy_request(method: str, path: str, payload=None, timeout: int = 30):
    """Single ComfyUI HTTP call. Raises typed errors, never dies."""
    url = f"http://{COMFY_HOST}:{COMFY_PORT}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise SubmissionError(f"HTTP {e.code} from ComfyUI {path}",
                              f"Response body: {body}")
    except urllib.error.URLError as e:
        raise ComfyUIUnreachableError(
            "cannot reach ComfyUI",
            f"Connection to {COMFY_HOST}:{COMFY_PORT} failed ({e.reason}). "
            f"Is ComfyUI running? Check: curl http://{COMFY_HOST}:{COMFY_PORT}/system_stats. "
            f"On the AutoDL image it starts automatically via boot.sh.")


def _check_server() -> None:
    """Fail fast on unreachable / Extender missing (B010/B011)."""
    try:
        info = _comfy_request("GET", "/object_info", timeout=10)
    except SubmissionError:
        raise
    if "MiniMaxH3Extender" not in info:
        raise ExtenderNodeMissingError(
            "MiniMax H3 Extender node is not installed",
            "Install ComfyUI_MiniMax_H3_Extender into ComfyUI/custom_nodes/ "
            "and restart ComfyUI. See: "
            "https://github.com/tritant/ComfyUI_MiniMax_H3_Extender")


def _upload_ref_to_comfy(ref_path: Path) -> None:
    """Upload one reference image via POST /upload/image?overwrite=true (DP-006)."""
    boundary = uuid.uuid4().hex
    body = ref_path.read_bytes()
    parts = [
        (f"--{boundary}\r\n"
         f"Content-Disposition: form-data; name=\"image\"; "
         f"filename=\"{ref_path.name}\"\r\n"
         f"Content-Type: image/png\r\n\r\n").encode(),
        body,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(
        f"http://{COMFY_HOST}:{COMFY_PORT}/upload/image?overwrite=true",
        data=b"".join(parts), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise RefUploadError(
            f"reference image upload failed: HTTP {e.code}",
            f"Uploading {ref_path.name} to ComfyUI failed. "
            f"Is ComfyUI running and reachable?")


def _sync_refs(pdir: Path, story: dict) -> None:
    """Upload every ref listed in story.refs to ComfyUI (B002/DP-006)."""
    for ref in story.get("refs", []):
        local = pdir / "refs" / ref
        if not local.exists():
            raise RefImageMissingError(
                f"reference image missing: {ref}",
                f"File not found: {pdir.name}/refs/{ref}. "
                f"Upload it via POST /api/projects/{pdir.name}/refs.")
        _upload_ref_to_comfy(local)


def _build_workflow(story: dict) -> dict:
    """Build the Extender API prompt (verified node chain, DP-045)."""
    clips_json = json.dumps(
        {"version": 1, "clips": story["clips"]}, ensure_ascii=False)
    width, height = story.get("resolution", [768, 1344])
    models = story.get("models") or {}
    unet = models.get("unet", "minimax/minimax_h3_ref2va_pruned_int8_convrot.safetensors")
    lora = models.get("lora", "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors")
    clip_model = models.get("clip", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")
    video_vae = models.get("video_vae", "minimax_h3_video_vae_fp16.safetensors")
    audio_vae = models.get("audio_vae", "minimax_h3_audio_vae_fp32.safetensors")

    prompt = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "MiniMaxH3SigmaShift", "inputs": {
            "shift_video": 12.0, "shift_audio": 3.0, "model": ["1", 0]}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "lora_name": lora, "strength_model": 1.0, "model": ["2", 0]}},
        "4": {"class_type": "PathchSageAttentionKJ", "inputs": {
            "sage_attention": "auto", "model": ["3", 0]}},
        "5": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch",
              "inputs": {"model": ["4", 0]}},
        "6": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": clip_model, "type": "minimax"}},
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": video_vae}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": audio_vae}},
        "10": {"class_type": "MiniMaxH3Extender", "inputs": {
            "run_mode": "clip_by_clip",
            "width": width, "height": height,
            "ref_image_size": "match",
            "steps": story.get("steps", 8),
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "context_length": str(story.get("context_length", "22")),
            "audio_context_length": 0,
            "clips_json": clips_json,
            "model": ["5", 0], "clip": ["6", 0],
            "vae": ["7", 0], "audio_vae": ["8", 0]}},
        "11": {"class_type": "MiniMaxH3MotionContextDiskFinalDecode", "inputs": {
            "cache": ["10", 0], "fps": 24.0,
            "filename_prefix": story.get("prefix", "h3chain"),
            "codec": "H.264", "crf": 17, "preset": "veryfast",
            "audio_bitrate": "192k",
            "vae": ["7", 0], "audio_vae": ["8", 0]}},
    }
    for i, ref in enumerate(story.get("refs", [])[:MAX_REFS], start=1):
        node_id = str(100 + i)
        prompt[node_id] = {"class_type": "LoadImage",
                           "inputs": {"image": ref}}
        prompt["10"]["inputs"][f"ref_{i}"] = [node_id, 0]
    return {"prompt": prompt}


def _submit_and_wait(workflow: dict, timeout: int, progress_cb=None) -> dict:
    """Submit workflow, poll until success/error, emit progress (B002/B024)."""
    res = _comfy_request("POST", "/prompt", workflow)
    prompt_id = res.get("prompt_id", "")
    if not prompt_id:
        raise SubmissionError("submit failed, no prompt_id in response",
                              f"Response: {json.dumps(res)[:500]}")
    start = time.time()
    last_report = 0.0
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(
                    f"http://{COMFY_HOST}:{COMFY_PORT}/history/{prompt_id}",
                    timeout=10) as r:
                history = json.loads(r.read())
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {}).get("status_str", "")
                if status == "error":
                    msgs = entry.get("status", {}).get("messages", [])
                    raise ExecutionError(
                        "execution error on ComfyUI",
                        f"Server messages: "
                        f"{json.dumps(msgs, ensure_ascii=False)[:600]}")
                if status == "success":
                    return entry
        except (ExecutionError, SubmissionError):
            raise
        except Exception:
            pass
        if progress_cb and time.time() - last_report >= HEARTBEAT:
            progress_cb({"type": "heartbeat", "elapsed": round(time.time() - start, 1)})
            last_report = time.time()
        time.sleep(5)
    raise TimeoutError(
        f"timed out after {timeout}s (prompt_id={prompt_id})",
        f"The clip may still be generating on ComfyUI. "
        f"Check ComfyUI history, then retry with a longer timeout "
        f"(H3CHAIN_TIMEOUT env).")


def _find_artifacts(entry: dict) -> list:
    """Collect downloadable outputs from a finished history entry."""
    arts = []
    for node_out in entry.get("outputs", {}).values():
        for key in ("videos", "gifs", "images"):
            for item in node_out.get(key, []):
                if item.get("filename"):
                    arts.append({**item, "kind": key})
    return arts


def _download_artifacts(entry: dict, out_dir: Path) -> list:
    """Download every artifact to {project}/output/ and return filenames."""
    names = []
    for art in _find_artifacts(entry):
        url = (f"http://{COMFY_HOST}:{COMFY_PORT}/view?filename={art['filename']}"
               f"&subfolder={art.get('subfolder', '')}&type=output")
        dest = out_dir / art["filename"]
        with urllib.request.urlopen(url, timeout=300) as r:
            dest.write_bytes(r.read())
        names.append(art["filename"])
    return names


# ---------------------------------------------------------------- task runner

class TaskState:
    """One generation task per project (gen/export)."""
    def __init__(self):
        self.lock = threading.Lock()
        self.active = {}      # project -> {started_at, prompt_id, kind, clips_info}
        self.events = {}      # project -> list of pending SSE events
        self.event_cv = threading.Condition()

    def start(self, project: str, kind: str, clips_info: dict):
        with self.lock:
            if project in self.active:
                raise SubmissionError(
                    "a generation task is already running for this project",
                    f"Wait for the current task to finish, or retry later. "
                    f"GET /api/projects/{project}/status shows progress.")
            self.active[project] = {
                "kind": kind, "started_at": time.time(), **clips_info}
        self.push(project, {"type": "task_started", "kind": kind, **clips_info})

    def finish(self, project: str, summary: dict):
        with self.lock:
            self.active.pop(project, None)
        self.push(project, {"type": "done", **summary})

    def fail(self, project: str, error: str, detail: str):
        with self.lock:
            self.active.pop(project, None)
        self.push(project, {"type": "error", "error": error, "detail": detail})

    def push(self, project: str, event: dict):
        with self.event_cv:
            self.events.setdefault(project, []).append(event)
            self.event_cv.notify_all()

    def snapshot_active(self, project: str):
        with self.lock:
            return dict(self.active.get(project, {}))

    def stream(self, project: str):
        """SSE generator: progress + heartbeat (B019/B020)."""
        seen = 0
        last_beat = time.time()
        while True:
            with self.event_cv:
                queue = self.events.get(project, [])
                while len(queue) <= seen and time.time() - last_beat < HEARTBEAT:
                    self.event_cv.wait(timeout=1.0)
                queue = self.events.get(project, [])
                new = queue[seen:]
                seen = len(queue)
            if new:
                for ev in new:
                    yield ev
                last_beat = time.time()
            else:
                yield {"type": "heartbeat"}
                last_beat = time.time()


TASKS = TaskState()


def _run_gen(project: str, pdir: Path, target_all: bool = False):
    """Background thread: generate next unvalidated clip (or all, for export)."""
    try:
        story = _load_story(pdir)
        out_dir = pdir / "output"
        out_dir.mkdir(exist_ok=True)
        if target_all:
            # export mode: loop until all clips validated
            while True:
                story = _load_story(pdir)
                pending = [c for c in story["clips"] if not c.get("validated")]
                if not pending:
                    break
                clip = pending[0]
                idx = story["clips"].index(clip)
                total = len(story["clips"])
                TASKS.push(project, {
                    "type": "progress", "kind": "export",
                    "clip": clip["id"], "index": idx + 1, "total": total})
                entry = _submit_and_wait(_build_workflow(story), TIMEOUT)
                _download_artifacts(entry, out_dir)
                # mark validated after successful generation (DP-007)
                story["clips"][idx]["validated"] = True
                _save_story(pdir, story)
            # Final merge decode
            story = _load_story(pdir)
            total = len(story["clips"])
            TASKS.push(project, {"type": "progress", "kind": "export",
                                 "clip": "final_merge", "index": total, "total": total})
            entry = _submit_and_wait(_build_workflow(story), TIMEOUT)
            names = _download_artifacts(entry, out_dir)
            TASKS.finish(project, {"files": names})
            return
        # single-clip gen
        pending = [c for c in story["clips"] if not c.get("validated")]
        if not pending:
            TASKS.finish(project, {"message": "all clips validated",
                                   "next": "export"})
            return
        clip = pending[0]
        idx = story["clips"].index(clip)
        total = len(story["clips"])
        TASKS.push(project, {"type": "progress", "kind": "gen",
                             "clip": clip["id"], "index": idx + 1, "total": total})
        entry = _submit_and_wait(_build_workflow(story), TIMEOUT)
        _download_artifacts(entry, out_dir)
        TASKS.finish(project, {
            "message": f"clip {clip['id']} generated (not validated yet)",
            "next": "keep or redo",
            "clip": clip["id"]})
    except H3ChainError as e:
        TASKS.fail(project, e.message, e.detail)
    except TimeoutError as e:
        TASKS.fail(project, str(e.args[0]) if e.args else "timeout", "")
    except Exception as e:
        TASKS.fail(project, f"unexpected error: {e}", "")


# ---------------------------------------------------------------- FastAPI app

app = FastAPI(title="h3chain", version="0.2.0",
              description="MiniMax H3 unlimited-length video generation")


def _api_error_response(e: H3ChainError):
    return {"ok": False, "error": e.message, "error_detail": e.detail, "data": {}}


def _wait_task(project: str, timeout: float = 300) -> dict:
    """Wait for task completion and return final event summary (tests reuse)."""
    deadline = time.time() + timeout
    seen = len(TASKS.events.get(project, []))
    while time.time() < deadline:
        with TASKS.event_cv:
            q = TASKS.events.get(project, [])
            if len(q) > seen:
                for ev in q[seen:]:
                    if ev.get("type") in ("done", "error"):
                        return ev
                seen = len(q)
        time.sleep(0.1)
    raise TimeoutError(f"task for {project} did not finish in {timeout}s")


class NameBody(BaseModel):
    name: str


class StoryBody(BaseModel):
    story: dict


@app.get("/api/projects")
def api_list_projects():
    WORKSPACE.mkdir(exist_ok=True)
    names = sorted(p.name for p in WORKSPACE.iterdir()
                   if p.is_dir() and (p / "story.json").exists())
    return {"ok": True, "data": {"projects": names}}


@app.post("/api/projects")
def api_create_project(body: NameBody):
    name = body.name.strip()
    if not name or "/" in name or ".." in name:
        raise HTTPException(400, "invalid project name")
    pdir = WORKSPACE / name
    if pdir.exists():
        e = DirectoryExistsError(f"'{name}' already exists",
                                  "Choose a different project name.")
        return _api_error_response(e)
    (pdir / "refs").mkdir(parents=True)
    (pdir / "output").mkdir()
    story = _make_template_story(name)
    _save_story(pdir, story)
    return {"ok": True, "data": {"project": name,
                                 "next": "upload refs, edit story.json"}}


def _make_template_story(name: str) -> dict:
    return {
        "title": name,
        "resolution": [768, 1344],
        "steps": 8,
        "context_length": "22",
        "seed_mode": "increment",
        "prefix": name or "h3chain",
        "models": {},
        "refs": ["ref_1.png"],
        "clips": [
            {"id": "clip_1", "prompt": SIX_SECTION_PROMPT, "duration": 5.0,
             "seed": 42, "seed_mode": "increment", "validated": False},
            {"id": "clip_2",
             "prompt": "(Copy the subject_definitions block from clip_1 so "
                       "identity stays locked, then describe the next beat.)",
             "duration": 5.0, "seed": 43, "seed_mode": "increment",
             "validated": False},
        ],
    }


@app.get("/api/projects/{project}/story")
def api_get_story(project: str):
    try:
        pdir = _project_dir(project)
        story = _load_story(pdir)
    except H3ChainError as e:
        return _api_error_response(e)
    return {"ok": True, "data": {"story": story}}


@app.put("/api/projects/{project}/story")
def api_update_story(project: str, body: StoryBody):
    try:
        pdir = _project_dir(project)
        _validate_story(body.story)
        _save_story(pdir, body.story)
    except H3ChainError as e:
        return _api_error_response(e)
    return {"ok": True, "data": {"saved": True, "next": "gen or status"}}


@app.post("/api/projects/{project}/refs")
async def api_upload_ref(project: str, file: UploadFile = File(...)):
    return await _upload_ref_impl(project, file)


async def _upload_ref_impl(project: str, file: UploadFile):
    try:
        pdir = _project_dir(project)
        refs_dir = pdir / "refs"
        refs_dir.mkdir(exist_ok=True)
        content = await file.read()
        if len(content) > MAX_REF_SIZE:
            raise RefUploadError(
                "file too large",
                f"Max size is 20MB. Got {len(content)} bytes.")
        if not (file.filename or "").lower().endswith((".png", ".jpg", ".jpeg")):
            raise RefUploadError(
                "unsupported format",
                "Only PNG/JPG reference images are accepted.")
        fname = file.filename or "ref.png"
        dest = refs_dir / Path(fname).name
        dest.write_bytes(content)
    except H3ChainError as e:
        return _api_error_response(e)
    return {"ok": True, "data": {"filename": dest.name,
                                 "next": "edit story.json refs list"}}


@app.post("/api/projects/{project}/gen")
def api_gen(project: str):
    try:
        pdir = _project_dir(project)
        story = _load_story(pdir)
        pending_count = sum(1 for c in story["clips"] if not c.get("validated"))
        if pending_count == 0:
            return {"ok": True, "data": {
                "message": "all clips validated",
                "next": "export",
                "message_zh": "全部 clip 已验证，可导出"}}
        # synchronous preflight (fail fast, B010-B012)
        _check_server()
        _sync_refs(pdir, story)
        TASKS.start(project, "gen", {"clip": story["clips"][0]["id"]})
        threading.Thread(target=_run_gen, args=(project, pdir, False),
                         daemon=True).start()
    except H3ChainError as e:
        return _api_error_response(e)
    return {"ok": True, "data": {
        "message": f"generation started",
        "next": "watch events stream", "project": project}}


@app.post("/api/projects/{project}/keep")
def api_keep(project: str, clip: Optional[str] = None):
    try:
        pdir = _project_dir(project)
        story = _load_story(pdir)
        clip_ids = [c["id"] for c in story["clips"]]
        if clip is None or clip == "last":
            pending = [c for c in story["clips"] if not c.get("validated")]
            if not pending:
                raise AllValidatedError(
                    "nothing left to keep",
                    "All clips are already validated. Next: export.")
            target = pending[0]
        else:
            if clip not in clip_ids:
                raise ClipNotFoundError(
                    f"clip '{clip}' not found",
                    f"Available clip ids: {', '.join(clip_ids)}")
            target = next(c for c in story["clips"] if c["id"] == clip)
            if not target.get("validated"):
                # out-of-order check (B005): all clips before it must be validated
                idx = story["clips"].index(target)
                before = story["clips"][:idx]
                if any(not c.get("validated") for c in before):
                    raise OutOfOrderKeepError(
                        "keep must follow a continuous chain",
                        f"Clips before '{clip}' are not all validated. "
                        f"Keep them first, or keep clips in order." )
        target["validated"] = True
        _save_story(pdir, story)
        validated_count = sum(1 for c in story["clips"] if c.get("validated"))
        total = len(story["clips"])
        next_step = "gen" if validated_count < total else "export"
        return {"ok": True, "data": {"kept": target["id"],
                                     "validated": f"{validated_count}/{total}",
                                     "next": next_step}}
    except H3ChainError as e:
        return _api_error_response(e)


@app.post("/api/projects/{project}/redo")
def api_redo(project: str, clip: Optional[str] = None, seed: Optional[int] = None):
    try:
        pdir = _project_dir(project)
        story = _load_story(pdir)
        clip_ids = [c["id"] for c in story["clips"]]
        if clip is None or clip == "last":
            pending = [c for c in story["clips"] if not c.get("validated")]
            if not pending:
                # nothing pending: redo the last clip (B006 'last' resolution)
                target = story["clips"][-1]
            else:
                target = pending[-1]
        else:
            if clip not in clip_ids:
                raise ClipNotFoundError(
                    f"clip '{clip}' not found",
                    f"Available clip ids: {', '.join(clip_ids)}")
            target = next(c for c in story["clips"] if c["id"] == clip)
        idx = story["clips"].index(target)
        old_seed = target.get("seed", 0)
        affected = story["clips"][idx:]
        if seed is not None:
            new_seed = seed
        else:
            new_seed = old_seed
            while new_seed == old_seed:
                new_seed = random.randint(1, 10**9)
        for c in affected:
            c["validated"] = False
        target["seed"] = new_seed
        _save_story(pdir, story)
        return {"ok": True, "data": {
            "redone": target["id"],
            "affected": len(affected),
            "new_seed": new_seed,
            "reason": "fresh seed avoids Extender disk-cache hit",
            "next": "gen"}}
    except H3ChainError as e:
        return _api_error_response(e)


@app.get("/api/projects/{project}/status")
def api_status(project: str):
    try:
        pdir = _project_dir(project)
        story = _load_story(pdir)
    except H3ChainError as e:
        return _api_error_response(e)
    total = len(story["clips"])
    validated_count = sum(1 for c in story["clips"] if c.get("validated"))
    pending = [c for c in story["clips"] if not c.get("validated")]
    clips_view = [{"id": c["id"], "duration": c.get("duration"),
                   "seed": c.get("seed"), "validated": bool(c.get("validated"))}
                  for c in story["clips"]]
    task = TASKS.snapshot_active(project)
    return {"ok": True, "data": {
        "validated": f"{validated_count}/{total}",
        "clips": clips_view,
        "next_clip": pending[0]["id"] if pending else None,
        "active_task": task or None,
        "next": "gen" if pending else "export"}}


@app.post("/api/projects/{project}/export")
def api_export(project: str):
    try:
        pdir = _project_dir(project)
        story = _load_story(pdir)
        # synchronous preflight (fail fast)
        _check_server()
        _sync_refs(pdir, story)
        TASKS.start(project, "export", {"clip": "all"})
        threading.Thread(target=_run_gen, args=(project, pdir, True),
                         daemon=True).start()
    except H3ChainError as e:
        return _api_error_response(e)
    return {"ok": True, "data": {"message": "export started",
                                 "next": "watch events stream"}}


@app.get("/api/projects/{project}/events")
def api_events(project: str):
    try:
        _project_dir(project)
    except H3ChainError as e:
        return _api_error_response(e)

    def sse():
        for ev in TASKS.stream(project):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@app.get("/api/projects/{project}/output/{file}")
def api_download(project: str, file: str):
    try:
        pdir = _project_dir(project)
    except H3ChainError as e:
        return _api_error_response(e)
    dest = pdir / "output" / Path(file).name
    if not dest.exists():
        raise HTTPException(404, f"file '{file}' not found in output/")
    return FileResponse(dest, media_type="video/mp4", filename=dest.name)


@app.get("/", response_class=HTMLResponse)
def _index():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")




