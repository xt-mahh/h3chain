"""ux-perf-v1 tests — B029-B033 from spec v0.3.0.

Same mock strategy as test_server.py: monkeypatched server._comfy_request
and urllib.request.urlopen; isolated workspace via conftest/autouse.
"""
import io
import json
import threading
import time
from pathlib import Path

import pytest

pytestmark_behavior = pytest.mark.behavior if hasattr(pytest.mark, "behavior") else (lambda b: (lambda f: f))

from fastapi.testclient import TestClient

import server


@pytest.fixture(autouse=True)
def fresh_env(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", ws)
    monkeypatch.setattr(server, "TASKS", server.TaskState())
    monkeypatch.setattr(server, "TIMEOUT", 60)
    yield ws


@pytest.fixture
def client():
    return TestClient(server.app)


def make_project(client, name="demo", clip_count=2):
    r = client.post("/api/projects", json={"name": name})
    assert r.json()["ok"] is True
    s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    s["clips"] = [
        {"id": f"clip_{i+1}", "prompt": f"prompt {i+1} with soundscape",
         "duration": 5.0, "seed": 42 + i, "seed_mode": "increment",
         "validated": False}
        for i in range(clip_count)]
    r2 = client.put(f"/api/projects/{name}/story", json={"story": s})
    assert r2.json()["ok"] is True
    return name


def _mock_comfy_ok(monkeypatch, name, artifact_name=None, pid="pid-x",
                   teacache_registered=False, capture=None):
    artifact = artifact_name or f"{name}.mp4"

    def fake_comfy_request(method, path, payload=None, timeout=30):
        if path == "/object_info":
            info = {"MiniMaxH3Extender": {}}
            if teacache_registered:
                info["TeaCacheForDiT"] = {}
            return info
        if path == "/prompt":
            if capture is not None:
                capture["workflow"] = payload
            return {"prompt_id": pid}
        raise AssertionError(f"unexpected {path}")

    monkeypatch.setattr(server, "_comfy_request", fake_comfy_request)

    def fake_urlopen(url, timeout=None):
        u = getattr(url, "full_url", url)

        class R:
            def read(self): return b""
            def __enter__(self): return self
            def __exit__(self, *a): pass
        if "/upload/image" in u:
            return R()
        if f"/history/{pid}" in u:
            class H(R):
                def read(self):
                    return json.dumps({
                        pid: {"status": {"status_str": "success"},
                              "outputs": {"11": {"videos": [
                                  {"filename": artifact,
                                   "subfolder": ""}]}}}}).encode()
            return H()
        if "/view" in u:
            class V(R):
                def read(self): return b"FAKE_MP4"
            return V()
        raise AssertionError(f"unexpected url {u}")

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    return artifact


def _wait_done(name, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(e.get("type") == "done" for e in server.TASKS.events.get(name, [])):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.behavior("B029")
def test_gen_clip_status_lifecycle(client, monkeypatch):
    name = make_project(client, "lcs", clip_count=2)
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")
    _mock_comfy_ok(monkeypatch, name)

    r = client.post(f"/api/projects/{name}/gen")
    assert r.json()["ok"] is True
    assert _wait_done(name)
    s = json.loads((server.WORKSPACE / name / "story.json").read_text())
    assert s["clips"][0]["status"] == "generated"
    assert s["clips"][0]["validated"] is False
    assert s["clips"][1]["status"] == "pending"

    r = client.post(f"/api/projects/{name}/keep")
    assert r.json()["ok"] is True
    s = json.loads((server.WORKSPACE / name / "story.json").read_text())
    assert s["clips"][0]["status"] == "locked"
    assert s["clips"][0]["validated"] is True


@pytest.mark.behavior("B029")
def test_gen_clip_status_generating_persisted(client, monkeypatch):
    """During generation, story.json on disk shows status='generating'."""
    name = make_project(client, "lgn", clip_count=1)
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")

    started = threading.Event()
    release = threading.Event()

    orig_submit = server._submit_and_wait

    def slow_submit(workflow, timeout, progress_cb=None):
        started.set()
        release.wait(timeout=30)
        return orig_submit(workflow, timeout, progress_cb) if False else {
            "status": {"status_str": "success"},
            "outputs": {"11": {"videos": [{"filename": f"{name}.mp4",
                                           "subfolder": ""}]}}}

    monkeypatch.setattr(server, "_submit_and_wait", slow_submit)
    _mock_comfy_ok(monkeypatch, name)   # for _check_server + upload

    r = client.post(f"/api/projects/{name}/gen")
    assert r.json()["ok"] is True
    assert started.wait(timeout=10)
    s = json.loads((server.WORKSPACE / name / "story.json").read_text())
    assert s["clips"][0]["status"] == "generating"
    assert "gen_started_at" in s["clips"][0]
    release.set()
    assert _wait_done(name)


@pytest.mark.behavior("B029")
def test_gen_clip_status_error_on_failure(client, monkeypatch):
    name = make_project(client, "lerr", clip_count=1)
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")

    def fake_comfy_request(method, path, payload=None, timeout=30):
        if path == "/object_info":
            return {"MiniMaxH3Extender": {}}
        if path == "/prompt":
            return {"prompt_id": "pid-f"}
        raise AssertionError(f"unexpected {path}")

    monkeypatch.setattr(server, "_comfy_request", fake_comfy_request)

    def fake_urlopen(url, timeout=None):
        u = getattr(url, "full_url", url)

        class R:
            def read(self): return b""
            def __enter__(self): return self
            def __exit__(self, *a): pass
        if "/upload/image" in u:
            return R()
        if "/history/pid-f" in u:
            class H(R):
                def read(self):
                    return json.dumps({
                        "pid-f": {"status": {"status_str": "error"},
                                  "outputs": {}}}).encode()
            return H()
        raise AssertionError(f"unexpected url {u}")

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    r = client.post(f"/api/projects/{name}/gen")
    assert r.json()["ok"] is True
    deadline = time.time() + 30
    while time.time() < deadline:
        s = json.loads((server.WORKSPACE / name / "story.json").read_text())
        if s["clips"][0].get("status") == "error":
            break
        time.sleep(0.05)
    s = json.loads((server.WORKSPACE / name / "story.json").read_text())
    assert s["clips"][0]["status"] == "error"
    assert "gen_duration_s" in s["clips"][0]


@pytest.mark.behavior("B029")
def test_status_legacy_backfill(client):
    name = make_project(client, "lgc", clip_count=2)
    s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    for c in s["clips"]:
        c.pop("status", None)
    s["clips"][0]["validated"] = True
    r = client.put(f"/api/projects/{name}/story", json={"story": s})
    assert r.json()["ok"] is True

    body = client.get(f"/api/projects/{name}/status").json()
    assert body["ok"] is True
    st = [c["status"] for c in body["data"]["clips"]]
    assert st == ["locked", "pending"]


@pytest.mark.behavior("B030")
def test_gen_timing_recorded_and_pushed(client, monkeypatch):
    name = make_project(client, "tim", clip_count=1)
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")
    _mock_comfy_ok(monkeypatch, name)

    r = client.post(f"/api/projects/{name}/gen")
    assert r.json()["ok"] is True
    assert _wait_done(name)
    s = json.loads((server.WORKSPACE / name / "story.json").read_text())
    c = s["clips"][0]
    assert "gen_started_at" in c and c["gen_started_at"] > 0
    assert "gen_duration_s" in c and c["gen_duration_s"] > 0

    body = client.get(f"/api/projects/{name}/status").json()
    row = body["data"]["clips"][0]
    assert "gen_started_at" in row and "gen_duration_s" in row
    prog = [e for e in server.TASKS.events.get(name, [])
            if e.get("type") == "progress"]
    assert prog and all("elapsed_s" in e for e in prog)


@pytest.mark.behavior("B031")
def test_done_event_carries_artifact_info(client, monkeypatch):
    name = make_project(client, "dn", clip_count=1)
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")
    art = _mock_comfy_ok(monkeypatch, name, artifact_name="h3chain.mp4")

    r = client.post(f"/api/projects/{name}/gen")
    assert r.json()["ok"] is True
    assert _wait_done(name)
    done = [e for e in server.TASKS.events.get(name, [])
            if e.get("type") == "done"]
    assert done
    d = done[-1]
    assert d.get("clip") == "clip_1"
    assert d.get("file") == art
    assert isinstance(d.get("duration_s"), (int, float))
    assert d.get("duration_s") == 5.0


@pytest.mark.behavior("B032")
def test_teacache_node_inserted_when_registered(client, monkeypatch):
    name = make_project(client, "tc1", clip_count=1)
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")
    captured = {}
    _mock_comfy_ok(monkeypatch, name, pid="pid-t",
                   teacache_registered=True, capture=captured)

    r = client.post(f"/api/projects/{name}/gen")
    assert r.json()["ok"] is True
    deadline = time.time() + 30
    while time.time() < deadline:
        if captured.get("workflow"):
            break
        time.sleep(0.05)
    assert captured.get("workflow"), "workflow not submitted"
    wf = captured["workflow"]["prompt"]
    assert "4b" in wf
    assert wf["4b"]["class_type"] == "TeaCacheForDiT"
    assert wf["4b"]["inputs"]["model"] == ["4", 0]
    assert wf["10"]["inputs"]["model"] == ["4b", 0]


@pytest.mark.behavior("B032")
def test_teacache_absent_degrades_to_plain_chain(client, monkeypatch):
    name = make_project(client, "tc0", clip_count=1)
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")
    _mock_comfy_ok(monkeypatch, name, pid="pid-t2", teacache_registered=False)

    r = client.post(f"/api/projects/{name}/gen")
    assert r.json()["ok"] is True
    assert _wait_done(name)


@pytest.mark.behavior("B032")
def test_teacache_disabled_by_flag(client, monkeypatch):
    name = make_project(client, "tcd", clip_count=1)
    (server.WORKSPACE / name / "refs" / "ref_1.png").write_bytes(b"\x89PNG")
    s = client.get(f"/api/projects/{name}/story").json()["data"]["story"]
    s["teacache"] = False
    r = client.put(f"/api/projects/{name}/story", json={"story": s})
    assert r.json()["ok"] is True

    captured = {}
    _mock_comfy_ok(monkeypatch, name, pid="pid-t3",
                   teacache_registered=True, capture=captured)
    r = client.post(f"/api/projects/{name}/gen")
    assert r.json()["ok"] is True
    assert _wait_done(name)
    # give the thread a moment, then assert no 4b node was submitted
    time.sleep(0.5)
    wf = captured["workflow"]["prompt"]
    assert "4b" not in wf
    assert wf["10"]["inputs"]["model"] == ["4", 0]


@pytest.mark.behavior("B033")
def test_layout_preview_panel_first_in_mid_column():
    html = (Path(__file__).parent.parent / "web" / "index.html").read_text()
    # split at mid column
    parts = html.split('class="col-mid"')
    assert len(parts) >= 2, "col-mid not found"
    mid = parts[1]
    # preview marker (id or panel heading) before story settings marker
    pv = mid.find('id="preview"')
    if pv == -1:
        pv = mid.find('预览')
    st = mid.find('故事设置')
    assert pv != -1, "preview panel marker not found in col-mid"
    assert st != -1, "story settings marker not found in col-mid"
    assert pv < st, "preview panel must precede story settings in DOM"
    # grid ratio: mid column fr / total fr == 0.5
    import re
    m = re.search(r"grid-template-columns:\s*([^;]+);", html)
    assert m, "grid-template-columns not found"
    frs = [float(x) for x in re.findall(r"([\d.]+)fr", m.group(1))]
    assert len(frs) == 3, f"expected 3 fr tracks: {m.group(1)}"
    assert abs(frs[1] / sum(frs) - 0.5) < 1e-6, f"mid fr ratio != 0.5"


@pytest.mark.behavior("B031")
def test_b031_frontend_autorefresh_static():
    """B031 frontend contract (static): playVideo uses cache-busting timestamp
    query and sets autoplay; preview <video> element is muted autoplay."""
    import re
    html = (Path(__file__).parent.parent / "web" / "index.html").read_text()
    assert "?t=${Date.now()}" in html, "playVideo src lacks timestamp query"
    assert re.search(r"\bv\.autoplay\s*=\s*true", html), "autoplay not set on play"
    assert 'id="preview-video"' in html
    assert "autoplay muted" in html or "muted autoplay" in html or \
        ("autoplay" in html.split('id="preview-video"')[1][:200] and "muted" in html.split('id="preview-video"')[1][:200])
