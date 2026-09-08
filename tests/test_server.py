"""h3chain server tests — 28 behaviors from spec v0.2.0.

Mock ComfyUI via monkeypatched server._comfy_request / urllib urlopen.
Workspace isolated per-test via H3CHAIN_WORKSPACE env (set in conftest).
"""
import io
import json
import time
import threading
import uuid
from pathlib import Path

import pytest

pytestmark_behavior = pytest.mark.behavior if hasattr(pytest.mark, "behavior") else (lambda b: (lambda f: f))

from fastapi.testclient import TestClient

import server


@pytest.fixture(autouse=True)
def fresh_env(tmp_path, monkeypatch):
    """Isolate workspace + reset TASKS state for every test."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", ws)
    monkeypatch.setattr(server, "TASKS", server.TaskState())
    monkeypatch.setattr(server, "TIMEOUT", 60)
    yield ws
    # threads may still run; they write only into ws (tmp_path)


@pytest.fixture
def client():
    return TestClient(server.app)


def make_project(client, name="demo", clip_count=2):
    r = client.post("/api/projects", json={"name": name})
    assert r.json()["ok"] is True
    if clip_count != 2:
        story = r.json()["data"]["story"] if "story" in r.json()["data"] else None
        # adjust clip count via PUT
        s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
        s["clips"] = [
            {"id": f"clip_{i+1}", "prompt": f"prompt {i+1} with soundscape",
             "duration": 5.0, "seed": 42 + i, "seed_mode": "increment",
             "validated": False}
            for i in range(clip_count)]
        r2 = client.put(f"/api/projects/{name}/story", json={"story": s})
        assert r2.json()["ok"] is True
    return name


# ---------------------------------------------------------------- B001/B025

@pytest.mark.behavior("B001")
def test_api_create_project_creates_structure(client):
    r = client.post("/api/projects", json={"name": "p1"})
    body = r.json()
    assert body["ok"] is True
    ws = server.WORKSPACE
    assert (ws / "p1" / "story.json").exists()
    assert (ws / "p1" / "refs").is_dir()
    assert (ws / "p1" / "output").is_dir()
    story = json.loads((ws / "p1" / "story.json").read_text())
    # six-section skeleton + two example clips (DP-010)
    assert "subject_definitions" in story["clips"][0]["prompt"]
    assert "overall_soundscape" in story["clips"][0]["prompt"]
    assert len(story["clips"]) == 2
    assert story["resolution"] == [768, 1344]   # DP-011


@pytest.mark.behavior("B025")
def test_api_create_project_already_exists(client):
    make_project(client, "dup")
    r = client.post("/api/projects", json={"name": "dup"})
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "'dup' already exists"


# ---------------------------------------------------------------- B003/B015

@pytest.mark.behavior("B003")
def test_api_gen_all_validated(client):
    name = make_project(client, "allv")
    s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    for c in s["clips"]:
        c["validated"] = True
    client.put(f"/api/projects/{name}/story", json={"story": s})
    r = client.post(f"/api/projects/{name}/gen")
    body = r.json()
    assert body["ok"] is True
    assert "all clips validated" in body["data"]["message"]
    assert body["data"]["next"] == "export"


@pytest.mark.behavior("B015")
def test_api_gen_project_missing(client):
    r = client.post("/api/projects/ghost/gen")
    body = r.json()
    assert body["ok"] is False
    assert "not found" in body["error"]


# ---------------------------------------------------------------- B004/B005/B026

@pytest.mark.behavior("B004")
def test_api_keep_last_marks_first_pending(client):
    name = make_project(client, "keep1")
    r = client.post(f"/api/projects/{name}/keep?clip=last")
    body = r.json()
    assert body["ok"] is True
    s = json.loads((server.WORKSPACE / name / "story.json").read_text())
    assert s["clips"][0]["validated"] is True
    assert s["clips"][1]["validated"] is False
    assert body["data"]["validated"] == "1/2"


@pytest.mark.behavior("B005")
def test_api_keep_out_of_order_refused(client):
    name = make_project(client, "ooo")
    s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    s["clips"][0]["validated"] = True   # clip_1 validated, clip_2 not
    client.put(f"/api/projects/{name}/story", json={"story": s})
    # add a third clip so clip_3 has an unvalidated predecessor
    s["clips"].append({"id": "clip_3", "prompt": "p3 soundscape",
                       "duration": 5.0, "seed": 44,
                       "seed_mode": "increment", "validated": False})
    client.put(f"/api/projects/{name}/story", json={"story": s})
    before = (server.WORKSPACE / name / "story.json").read_text()
    r = client.post(f"/api/projects/{name}/keep?clip=clip_3")
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "keep must follow a continuous chain"
    after = (server.WORKSPACE / name / "story.json").read_text()
    assert after == before   # story.json unchanged


@pytest.mark.behavior("B026")
def test_api_keep_all_validated_error(client):
    name = make_project(client, "allk")
    s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    for c in s["clips"]:
        c["validated"] = True
    client.put(f"/api/projects/{name}/story", json={"story": s})
    r = client.post(f"/api/projects/{name}/keep")
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "nothing left to keep"
    assert "export" in body["error_detail"]


# ---------------------------------------------------------------- B006/B027

@pytest.mark.behavior("B006")
def test_api_redo_changes_seed_and_invalidates_tail(client):
    name = make_project(client, "redo1")
    s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    s["clips"].append({"id": "clip_3", "prompt": "p3 soundscape",
                       "duration": 5.0, "seed": 44,
                       "seed_mode": "increment", "validated": False})
    s["clips"][0]["validated"] = True
    s["clips"][1]["validated"] = True
    client.put(f"/api/projects/{name}/story", json={"story": s})
    r = client.post(f"/api/projects/{name}/redo?clip=clip_2")
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["affected"] == 2
    assert body["data"]["new_seed"] != 43
    s2 = json.loads((server.WORKSPACE / name / "story.json").read_text())
    assert s2["clips"][0]["validated"] is True    # before redo point kept
    assert s2["clips"][1]["validated"] is False   # target invalidated
    assert s2["clips"][2]["validated"] is False   # tail invalidated
    assert s2["clips"][1]["seed"] != 43           # fresh seed (DP-004)
    assert 1 <= s2["clips"][1]["seed"] <= 10**9


@pytest.mark.behavior("B006")
def test_api_redo_explicit_seed(client):
    name = make_project(client, "redo2")
    r = client.post(f"/api/projects/{name}/redo?clip=clip_1&seed=999")
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["new_seed"] == 999
    s = json.loads((server.WORKSPACE / name / "story.json").read_text())
    assert s["clips"][0]["seed"] == 999


@pytest.mark.behavior("B027")
def test_api_redo_nonexistent_clip(client):
    name = make_project(client, "redo3")
    r = client.post(f"/api/projects/{name}/redo?clip=clip_99")
    body = r.json()
    assert body["ok"] is False
    assert "clip_1" in body["error_detail"]   # available ids listed


# ---------------------------------------------------------------- B007

@pytest.mark.behavior("B007")
def test_api_status_shows_progress(client):
    name = make_project(client, "st")
    s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    s["clips"][0]["validated"] = True
    client.put(f"/api/projects/{name}/story", json={"story": s})
    r = client.get(f"/api/projects/{name}/status")
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["validated"] == "1/2"
    assert body["data"]["next_clip"] == "clip_2"
    row = body["data"]["clips"][0]
    assert set(row) >= {"id", "duration", "seed", "validated"}


# ---------------------------------------------------------------- B013/B016

@pytest.mark.behavior("B016")
def test_story_validation_errors(client):
    name = make_project(client, "inv")
    base = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    # no clips
    s = json.loads(json.dumps(base)); s["clips"] = []
    r = client.put(f"/api/projects/{name}/story", json={"story": s})
    assert "At least one clip" in r.json()["error_detail"]
    # empty prompt
    s = json.loads(json.dumps(base)); s["clips"][0]["prompt"] = "  "
    r = client.put(f"/api/projects/{name}/story", json={"story": s})
    assert "empty prompt" in r.json()["error_detail"]
    # duration out of range
    s = json.loads(json.dumps(base)); s["clips"][0]["duration"] = 999
    r = client.put(f"/api/projects/{name}/story", json={"story": s})
    assert "0.25-150.0" in r.json()["error_detail"]
    # too many refs
    s = json.loads(json.dumps(base)); s["refs"] = [f"r{i}.png" for i in range(10)]
    r = client.put(f"/api/projects/{name}/story", json={"story": s})
    assert "ref_1..ref_9" in r.json()["error_detail"]


@pytest.mark.behavior("B013")
def test_api_gen_invalid_story_json(client):
    name = make_project(client, "badjson")
    (server.WORKSPACE / name / "story.json").write_text("{not json")
    r = client.post(f"/api/projects/{name}/gen")
    body = r.json()
    assert body["ok"] is False
    assert "JSON" in body["error"]


# ---------------------------------------------------------------- B017/B018

@pytest.mark.behavior("B017")
def test_api_upload_ref_success(client):
    name = make_project(client, "up")
    r = client.post(f"/api/projects/{name}/refs",
                    files={"file": ("ref_1.png", b"\x89PNG fake", "image/png")})
    body = r.json()
    assert body["ok"] is True
    assert (server.WORKSPACE / name / "refs" / "ref_1.png").exists()


@pytest.mark.behavior("B018")
def test_api_upload_ref_rejects_bad_file(client):
    name = make_project(client, "up2")
    r = client.post(f"/api/projects/{name}/refs",
                    files={"file": ("evil.txt", b"x" * 10, "text/plain")})
    assert r.json()["ok"] is False
    r = client.post(f"/api/projects/{name}/refs",
                    files={"file": ("big.png", b"x" * (21 * 1024 * 1024), "image/png")})
    assert r.json()["ok"] is False
    assert "20MB" in r.json()["error_detail"]


# ---------------------------------------------------------------- B010/B011/B012

@pytest.mark.behavior("B010")
def test_api_gen_comfy_unreachable(client, monkeypatch):
    name = make_project(client, "unreach")
    import urllib.request
    def boom(*a, **k):
        import urllib.error
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    r = client.post(f"/api/projects/{name}/gen")
    body = r.json()
    assert body["ok"] is False
    assert "cannot reach ComfyUI" in body["error"]


@pytest.mark.behavior("B011")
def test_api_gen_extender_missing(client, monkeypatch):
    name = make_project(client, "nomod")
    monkeypatch.setattr(server, "_comfy_request",
                        lambda m, p, **k: {"SomeNode": {}})
    r = client.post(f"/api/projects/{name}/gen")
    body = r.json()
    assert body["ok"] is False
    assert "Extender" in body["error"]
    assert "install" in body["error_detail"].lower()


@pytest.mark.behavior("B012")
def test_api_gen_ref_missing_local(client, monkeypatch):
    name = make_project(client, "noref")
    monkeypatch.setattr(server, "_comfy_request",
                        lambda m, p, **k: {"MiniMaxH3Extender": {}})
    r = client.post(f"/api/projects/{name}/gen")
    body = r.json()
    assert body["ok"] is False
    assert "reference image missing" in body["error"]
    assert "refs/ref_1.png" in body["error_detail"]


# ---------------------------------------------------------------- B002 happy path

@pytest.mark.behavior("B002")
def test_api_gen_happy_path_with_mock_comfy(client, monkeypatch):
    name = make_project(client, "happy")
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")
    calls = {"upload": 0}

    def fake_comfy_request(method, path, payload=None, timeout=30):
        if path == "/object_info":
            return {"MiniMaxH3Extender": {}, "LoadImage": {}}
        if path == "/prompt":
            return {"prompt_id": "pid-1"}
        raise AssertionError(f"unexpected comfy call {method} {path}")

    monkeypatch.setattr(server, "_comfy_request", fake_comfy_request)

    def fake_urlopen(url, timeout=None):
        u = getattr(url, "full_url", url)   # may be a Request object
        class R:
            def read(self): return b""

            def __enter__(self): return self

            def __exit__(self, *a): pass
        if "/upload/image" in u:
            calls["upload"] += 1
            return R()
        if "/history/pid-1" in u:
            class H(R):
                def read(self):
                    return json.dumps({
                        "pid-1": {"status": {"status_str": "success"},
                                  "outputs": {"11": {"videos": [
                                      {"filename": f"{name}.mp4",
                                       "subfolder": ""}]}}}}).encode()
            return H()
        if "/view" in u:
            class V(R):
                def read(self): return b"FAKE_MP4_BYTES"
            return V()
        raise AssertionError(f"unexpected url {u}")

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    r = client.post(f"/api/projects/{name}/gen")
    assert r.json()["ok"] is True
    # wait for background thread
    deadline = time.time() + 20
    while time.time() < deadline:
        q = server.TASKS.events.get(name, [])
        if any(e.get("type") == "done" for e in q):
            break
        time.sleep(0.1)
    assert any(e.get("type") == "done" for e in server.TASKS.events.get(name, []))
    assert (server.WORKSPACE / name / "output" / f"{name}.mp4").exists()
    assert calls["upload"] == 1   # ref sync happened (DP-006)
    done_ev = [e for e in server.TASKS.events.get(name, []) if e["type"] == "done"][0]
    assert done_ev["clip"] == "clip_1"
    assert done_ev["next"] in ("keep or redo",)
    # progress event pushed (B019)
    prog = [e for e in server.TASKS.events.get(name, []) if e["type"] == "progress"]
    assert prog and prog[0]["clip"] == "clip_1"
    assert prog[0]["total"] == 2


# ---------------------------------------------------------------- B008 export

@pytest.mark.behavior("B008")
def test_api_export_two_phase_with_mock(client, monkeypatch):
    name = make_project(client, "exp", clip_count=3)
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")

    def fake_comfy_request(method, path, payload=None, timeout=30):
        if path == "/object_info":
            return {"MiniMaxH3Extender": {}}
        if path == "/prompt":
            return {"prompt_id": "pid-e"}
        raise AssertionError(f"unexpected {path}")

    monkeypatch.setattr(server, "_comfy_request", fake_comfy_request)

    def fake_urlopen(url, timeout=None):
        u = getattr(url, "full_url", url)   # may be a Request object
        class R:
            def read(self): return b""

            def __enter__(self): return self

            def __exit__(self, *a): pass
        if "/upload/image" in u:
            return R()
        if "/history/pid-e" in u:
            class H(R):
                def read(self):
                    return json.dumps({
                        "pid-e": {"status": {"status_str": "success"},
                                  "outputs": {"11": {"videos": [
                                      {"filename": f"{name}.mp4",
                                       "subfolder": ""}]}}}}).encode()
            return H()
        if "/view" in u:
            class V(R):
                def read(self): return b"FAKE_MP4"
            return V()
        raise AssertionError(f"unexpected url {u}")

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    r = client.post(f"/api/projects/{name}/export")
    assert r.json()["ok"] is True
    deadline = time.time() + 30
    while time.time() < deadline:
        if any(e.get("type") == "done" for e in server.TASKS.events.get(name, [])):
            break
        time.sleep(0.1)
    events = server.TASKS.events.get(name, [])
    assert any(e.get("type") == "done" for e in events)
    # export loop marked all clips validated (DP-007)
    s = json.loads((server.WORKSPACE / name / "story.json").read_text())
    assert all(c["validated"] for c in s["clips"])
    # progress events for each clip + final merge
    clips_progress = [e for e in events
                      if e.get("type") == "progress" and e.get("kind") == "export"]
    assert any(e.get("clip") == "final_merge" for e in clips_progress)


# ---------------------------------------------------------------- B009 resumable

@pytest.mark.behavior("B009")
def test_api_export_resumable(client, monkeypatch):
    """clips 1-5 validated; export only generates from clip 6 (B009)."""
    name = make_project(client, "res", clip_count=6)
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")
    s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    for c in s["clips"][:5]:
        c["validated"] = True
    client.put(f"/api/projects/{name}/story", json={"story": s})

    gen_clips = []
    state = {"pid": 0}

    def fake_comfy_request(method, path, payload=None, timeout=30):
        if path == "/object_info":
            return {"MiniMaxH3Extender": {}}
        if path == "/prompt":
            # record the first pending clip in the submitted clips_json
            cj = payload["prompt"]["10"]["inputs"]["clips_json"]
            clips = json.loads(cj)["clips"]
            pending = [c for c in clips if not c.get("validated")]
            gen_clips.append(pending[0]["id"] if pending else "final")
            state["pid"] += 1
            return {"prompt_id": f"pid-{state['pid']}"}
        raise AssertionError(f"unexpected {path}")

    monkeypatch.setattr(server, "_comfy_request", fake_comfy_request)

    def fake_urlopen(url, timeout=None):
        u = getattr(url, "full_url", url)   # may be a Request object
        class R:
            def read(self): return b""

            def __enter__(self): return self

            def __exit__(self, *a): pass
        if "/upload/image" in u:
            return R()
        if "/history/pid-" in u:
            pid = u.rsplit("pid-", 1)[1].split("/")[0]
            class H(R):
                def read(self):
                    return json.dumps({
                        f"pid-{pid}": {"status": {"status_str": "success"},
                              "outputs": {"11": {"videos": [
                                  {"filename": f"{name}.mp4",
                                   "subfolder": ""}]}}}}).encode()
            return H()
        if "/view" in u:
            class V(R):
                def read(self): return b"FAKE_MP4"
            return V()
        raise AssertionError(f"unexpected url {u}")

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    client.post(f"/api/projects/{name}/export")
    deadline = time.time() + 30
    while time.time() < deadline:
        if any(e.get("type") == "done" for e in server.TASKS.events.get(name, [])):
            break
        time.sleep(0.1)
    # only clip_6 + final decode submitted — clips 1-5 hit disk cache (B009)
    assert gen_clips == ["clip_6", "final"]


# ---------------------------------------------------------------- B019/B020 SSE

@pytest.mark.behavior("B020")
def test_sse_stream_events_and_heartbeat(client, monkeypatch):
    name = make_project(client, "sse")
    # push a progress event, then read the stream
    server.TASKS.push(name, {"type": "progress", "clip": "clip_1", "index": 1,
                             "total": 2})
    server.TASKS.push(name, {"type": "done", "files": ["x.mp4"]})
    # directly consume the generator (B019): progress + done events present
    gen = server.TASKS.stream(name)
    got = [next(gen), next(gen)]
    types = [e["type"] for e in got]
    assert "progress" in types
    assert "done" in types
    # heartbeat emitted when no events pending (B020)
    hb = next(gen)
    assert hb["type"] == "heartbeat"


# ---------------------------------------------------------------- B021/B022 download

@pytest.mark.behavior("B019")
def test_sse_progress_during_generation(client, monkeypatch):
    """B019: during a running gen task, the SSE stream yields progress events
    carrying clip id/index, and done with artifact filenames (no polling loop:
    push events then consume the generator directly)."""
    name = make_project(client, "sseprog")
    server.TASKS.push(name, {"type": "progress", "clip": "clip_2", "index": 2,
                             "total": 3, "status": "validated"})
    server.TASKS.push(name, {"type": "done", "files": ["output/final.mp4"]})
    gen = server.TASKS.stream(name)
    got = [next(gen), next(gen)]
    prog = next(e for e in got if e["type"] == "progress")
    assert prog["clip"] == "clip_2" and prog["index"] == 2
    done = next(e for e in got if e["type"] == "done")
    assert any(f.endswith("final.mp4") for f in done["files"])



@pytest.mark.behavior("B021")
def test_api_download_output_file(client):
    name = make_project(client, "dl")
    out = server.WORKSPACE / name / "output"
    out.mkdir(exist_ok=True)
    (out / "final.mp4").write_bytes(b"MP4DATA")
    r = client.get(f"/api/projects/{name}/output/final.mp4")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("video/mp4")
    assert r.content == b"MP4DATA"


@pytest.mark.behavior("B022")
def test_api_download_404(client):
    name = make_project(client, "dl2")
    r = client.get(f"/api/projects/{name}/output/ghost.mp4")
    assert r.status_code == 404


# ---------------------------------------------------------------- B023/B024

@pytest.mark.behavior("B023")
def test_submission_error_http_detail(client):
    """B023: HTTP 4xx/5xx on POST /prompt raises SubmissionError whose message
    carries the HTTP status code and detail with the first 500 chars of the
    response body (direct unit call — no background-task polling)."""
    import urllib.error
    import io

    class FakeHTTPError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://comfy/prompt", 503, "Service Unavailable",
                             hdrs=None, fp=io.BytesIO(b"server exploded " * 60))

    def fake_urlopen(req, timeout=30):
        raise FakeHTTPError()

    import urllib.request
    saved = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        with pytest.raises(server.SubmissionError) as ei:
            server._comfy_request("POST", "/prompt", {})
    finally:
        urllib.request.urlopen = saved
    assert "503" in str(ei.value.message)
    assert "server exploded" in ei.value.detail
    # detail = "Response body: " + first 500 chars of a 960-char body
    assert len(ei.value.detail) <= len("Response body: ") + 500


@pytest.mark.behavior("B024")
def test_submission_error_and_timeout_details(client, monkeypatch):
    name = make_project(client, "sub")
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")
    import urllib.error

    class HTTPError(Exception):
        pass

    def fake_comfy_request(method, path, payload=None, timeout=30):
        if path == "/object_info":
            return {"MiniMaxH3Extender": {}}
        if path == "/prompt":
            raise server.SubmissionError("HTTP 500 from ComfyUI /prompt",
                                         "Response body: boom")
        raise AssertionError(path)

    monkeypatch.setattr(server, "_comfy_request", fake_comfy_request)

    def fake_upload(path):
        pass

    monkeypatch.setattr(server, "_upload_ref_to_comfy", fake_upload)
    r = client.post(f"/api/projects/{name}/gen")
    # task fails in background; capture error event
    deadline = time.time() + 15
    err = None
    while time.time() < deadline:
        errs = [e for e in server.TASKS.events.get(name, [])
                if e.get("type") == "error"]
        if errs:
            err = errs[-1]
            break
        time.sleep(0.1)
    assert err is not None
    assert "HTTP 500" in err["error"]


# ---------------------------------------------------------------- B028 lock

@pytest.mark.behavior("B028")
def test_story_write_lock_conflict(client, monkeypatch):
    name = make_project(client, "lock")
    import fcntl
    # hold the lock: make flock raise BlockingIOError inside server.save_story
    def fake_flock(fd, op):
        raise BlockingIOError
    monkeypatch.setattr("fcntl.flock", fake_flock)
    s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    r = client.put(f"/api/projects/{name}/story", json={"story": s})
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "write lock held"


@pytest.mark.behavior("B014")
def test_api_gen_execution_error_detail(client, monkeypatch):
    """B014: history status=error -> ExecutionError with server messages."""
    name = make_project(client, "execerr")
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")

    def fake_comfy_request(method, path, payload=None, timeout=30):
        if path == "/object_info":
            return {"MiniMaxH3Extender": {}}
        if path == "/prompt":
            return {"prompt_id": "pid-x"}
        raise AssertionError(path)

    monkeypatch.setattr(server, "_comfy_request", fake_comfy_request)

    def fake_urlopen(url, timeout=None):
        u = getattr(url, "full_url", url)
        class R:
            def read(self): return b""
            def __enter__(self): return self
            def __exit__(self, *a): pass
        if "/upload/image" in u:
            return R()
        if "/history/pid-x" in u:
            class H(R):
                def read(self):
                    return json.dumps({
                        "pid-x": {"status": {"status_str": "error",
                                             "messages": ["boom trace line"]},
                                  "outputs": {}}}).encode()
            return H()
        raise AssertionError(f"unexpected url {u}")

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    client.post(f"/api/projects/{name}/gen")
    deadline = time.time() + 15
    err = None
    while time.time() < deadline:
        errs = [e for e in server.TASKS.events.get(name, [])
                if e.get("type") == "error"]
        if errs:
            err = errs[-1]
            break
        time.sleep(0.1)
    assert err is not None
    assert "execution error" in err["error"]
    assert "boom trace line" in err["detail"]   # messages surfaced (600-char cap in prod)


@pytest.mark.behavior("B019")
def test_sse_progress_during_generation(client):
    """B019: SSE stream pushes progress events while a task is running."""
    name = make_project(client, "ssegen")
    server.TASKS.start(name, "gen", {"clip": "clip_1"})
    server.TASKS.push(name, {"type": "progress", "kind": "gen",
                             "clip": "clip_1", "index": 1, "total": 2})
    gen = server.TASKS.stream(name)
    ev1, ev2 = next(gen), next(gen)
    types = [e["type"] for e in (ev1, ev2)]
    assert "progress" in types
    prog = next(e for e in (ev1, ev2) if e["type"] == "progress")
    assert prog["clip"] == "clip_1" and prog["total"] == 2


@pytest.mark.behavior("B023")
def test_submission_http_error_detail(client, monkeypatch):
    """B023: submit HTTP error -> SubmissionError with status code + body."""
    import urllib.error as _ue

    def fake_urlopen(req, timeout=None):
        raise _ue.HTTPError(
            "http://127.0.0.1:6006/prompt", 500, "Internal Server Error",
            None, io.BytesIO(b"boom-from-comfy"))

    # patch the real transport so the REAL _comfy_request handles the error
    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    from server import SubmissionError
    with pytest.raises(SubmissionError) as ei:
        server._comfy_request("POST", "/prompt", {"prompt": {}})
    assert "HTTP 500" in ei.value.message
    assert "boom-from-comfy" in ei.value.detail


