"""Мастер установки: проверка компонентов, загрузки с прогрессом, папки, API."""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import memory_desk.config, memory_desk.knowledge, memory_desk.llm, memory_desk.setup, memory_desk.voice  # noqa: F401
    globals().update(config=memory_desk.config, knowledge=memory_desk.knowledge, llm=memory_desk.llm, setup=memory_desk.setup, voice=memory_desk.voice)
    setup.runner.reset()
    yield
    setup.runner.reset()


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_CACHE", tmp_path / "hf")
    return tmp_path / "hf"


def put_model(cache_dir: Path, repo: str, partial: bool = False) -> None:
    base = cache_dir / ("models--" + repo.replace("/", "--"))
    (base / "snapshots" / "abc").mkdir(parents=True)
    (base / "blobs").mkdir(parents=True)
    (base / "blobs" / ("x.incomplete" if partial else "x")).write_bytes(b"1" * 100)


def by_id(items):
    return {c["id"]: c for c in items}


# --------------------------------------------------------------------------- проверки

def test_embedding_model_matches_code():
    assert setup.EMBEDDING_MODEL == knowledge.EMBEDDING_MODEL


def test_hf_cached_needs_complete_snapshot(tmp_path):
    assert setup.hf_cached("a/b", tmp_path) is False
    put_model(tmp_path, "a/b", partial=True)
    assert setup.hf_cached("a/b", tmp_path) is False          # недокачанный файл
    (tmp_path / "models--a--b" / "blobs" / "x.incomplete").rename(tmp_path / "models--a--b" / "blobs" / "y")
    assert setup.hf_cached("a/b", tmp_path) is True


def test_check_reports_every_component(cache, monkeypatch):
    monkeypatch.setattr(setup, "have_module", lambda name: name != "mlx_whisper")
    monkeypatch.setattr(setup, "which", lambda name: "/opt/x/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(llm, "list_models", lambda url, timeout=2.0: [{"name": "qwen3:4b", "size": 1, "params": "", "quant": ""}])
    put_model(cache, setup.EMBEDDING_MODEL)
    monkeypatch.setattr(voice, "model_ready", lambda: False)
    items = by_id(setup.check({}))
    assert list(items) == ["packages", "ffmpeg", "ollama_bin", "ollama_server", "llm", "whisper", "embedding", "voice", "vision", "eventkit"]
    assert items["packages"]["state"] == "missing" and "mlx_whisper" in items["packages"]["detail"]
    assert items["ffmpeg"]["state"] == "ok" and items["ffmpeg"]["action"] == ""
    assert items["ollama_bin"]["state"] == "missing" and items["ollama_bin"]["action"] == "hint"       # без Homebrew поставить нечем
    assert items["ollama_server"]["state"] == "ok" and items["llm"]["state"] == "ok"
    assert items["embedding"]["state"] == "ok" and items["whisper"]["state"] == "missing" and items["whisper"]["mb"] == 1600
    assert items["voice"]["required"] is False and items["whisper"]["required"] is True
    monkeypatch.setattr(setup, "which", lambda name: f"/opt/{name}")
    assert by_id(setup.check({}))["ollama_bin"]["state"] == "ok"


def test_llm_choice_changes_what_is_checked(cache, monkeypatch):
    monkeypatch.setattr(llm, "list_models", lambda url, timeout=2.0: [{"name": "qwen3:4b", "size": 1, "params": "", "quant": ""}])
    assert by_id(setup.check({"llm_model": "qwen3:8b"}))["llm"]["state"] == "missing"
    assert by_id(setup.check({"llm_model": "qwen3:8b"}))["llm"]["mb"] == 5200
    monkeypatch.setattr(llm, "list_models", lambda url, timeout=2.0: None)
    down = by_id(setup.check({}))
    assert down["ollama_server"]["state"] == "missing" and down["llm"]["action"] == "" and "не запущена" in down["llm"]["detail"]


def test_whisper_choice_changes_repo_and_size(cache, monkeypatch):
    assert setup.whisper_repo({"asr_model": "medium"}) == "mlx-community/whisper-medium-mlx"
    put_model(cache / "hub", "mlx-community/whisper-large-v3-turbo")
    assert by_id(setup.check({"asr_model": "turbo"}))["whisper"]["state"] == "ok"
    assert by_id(setup.check({"asr_model": "large-v3"}))["whisper"]["state"] == "missing"


def test_status_ready_and_wizard_rules(cache, monkeypatch):
    monkeypatch.setattr(setup, "have_module", lambda name: True)
    monkeypatch.setattr(setup, "which", lambda name: "/opt/x")
    monkeypatch.setattr(llm, "list_models", lambda url, timeout=2.0: [{"name": "qwen3:4b", "size": 1, "params": "", "quant": ""}])
    monkeypatch.setattr(voice, "model_ready", lambda: False)
    put_model(cache, setup.EMBEDDING_MODEL)
    put_model(cache / "hub", "mlx-community/whisper-large-v3-turbo")
    ready = setup.status({"setup_done": False}, meetings=5)
    assert ready["ready"] is True and ready["needsWizard"] is False and ready["disk"]["needGb"] == 0     # есть данные и всё на месте
    fresh = setup.status({"setup_done": False}, meetings=0)
    assert fresh["needsWizard"] is True                                                                  # чистая установка: показать мастер
    assert setup.status({"setup_done": True}, meetings=0)["needsWizard"] is False
    monkeypatch.setattr(setup, "which", lambda name: None if name == "ffmpeg" else "/opt/x")
    broken = setup.status({"setup_done": False}, meetings=5)
    assert broken["ready"] is False and broken["needsWizard"] is True


def test_disk_need_counts_only_missing_models(cache, monkeypatch):
    monkeypatch.setattr(setup, "have_module", lambda name: True)
    monkeypatch.setattr(setup, "which", lambda name: "/opt/x")
    monkeypatch.setattr(llm, "list_models", lambda url, timeout=2.0: [])
    monkeypatch.setattr(setup, "free_gb", lambda path=None: 4.0)
    disk = setup.status({}, 0)["disk"]
    assert disk["freeGb"] == 4.0 and disk["needGb"] == round((2500 + 1600 + 470) / 1024 + setup.NEED_FREE_MARGIN_GB, 1)


def test_folders_state(tmp_path):
    info = setup.folders({"recordings_dir": str(tmp_path), "obsidian_dir": str(tmp_path / "нет")})
    assert info["recordingsOk"] is True and info["obsidianOk"] is False


# --------------------------------------------------------------------------- загрузчик

def wait_done(timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        job = setup.runner.view()
        if job and job["state"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("загрузка не закончилась")


def test_runner_runs_one_job_at_a_time_and_reports(monkeypatch):
    gate = threading.Event()

    def slow(job, cfg):
        job.total, job.done = 200, 50
        job.line("качаю")
        gate.wait(3)
        job.done = 200
    monkeypatch.setitem(setup.HANDLERS, "voice", slow)
    assert setup.runner.start("voice", {}) is True
    assert setup.runner.start("voice", {}) is False                  # вторая загрузка поверх первой не запускается
    view = setup.runner.view()
    assert view["state"] == "running" and view["percent"] == 25 and view["message"] == "качаю"
    gate.set()
    done = wait_done()
    assert done["state"] == "done" and done["percent"] == 100
    with pytest.raises(setup.SetupError):
        setup.runner.start("нет-такого", {})


def test_runner_error_is_remembered_and_shown_in_status(cache, monkeypatch):
    def failing(job, cfg):
        raise setup.SetupError("нет сети")
    monkeypatch.setitem(setup.HANDLERS, "embedding", failing)
    monkeypatch.setattr(llm, "list_models", lambda url, timeout=2.0: [])
    setup.runner.start("embedding", {})
    assert wait_done()["state"] == "error"
    item = by_id(setup.status({}, 0)["components"])["embedding"]
    assert item["state"] == "error" and item["detail"] == "нет сети"

    def odd(job, cfg):
        raise ZeroDivisionError("x")
    monkeypatch.setitem(setup.HANDLERS, "vision", odd)
    setup.runner.start("vision", {})
    assert "Непредвиденная ошибка" in wait_done()["message"]


def test_runner_cancel_stops_a_command(monkeypatch):
    monkeypatch.setitem(setup.HANDLERS, "voice", lambda job, cfg: setup.stream_command(job, [sys.executable, "-c", "import time; time.sleep(30)"]))
    setup.runner.start("voice", {})
    time.sleep(0.4)
    assert setup.runner.cancel() is True
    assert wait_done()["state"] == "cancelled"
    assert setup.runner.cancel() is False


def test_stream_command_collects_output_and_explains_failure():
    job = setup.Job("x", "x")
    setup.stream_command(job, [sys.executable, "-c", "print('первая'); print('вторая')"])
    assert job.log == ["первая", "вторая"]
    bad = setup.Job("y", "y")
    with pytest.raises(setup.SetupError) as caught:
        setup.stream_command(bad, [sys.executable, "-c", "import sys; print('сломалось'); sys.exit(3)"], label="пример")
    assert "пример" in str(caught.value) and "сломалось" in str(caught.value) and "(3)" in str(caught.value)
    with pytest.raises(setup.SetupError):
        setup.stream_command(setup.Job("z", "z"), ["/нет/такой/команды"])


def test_hf_download_tracks_progress_and_checks_result(cache):
    code = ("import os, time\nfrom pathlib import Path\nbase = Path(os.environ['SETUP_CACHE']) / 'models--a--b'\n"
            "(base / 'blobs').mkdir(parents=True)\n(base / 'blobs' / 'f.incomplete').write_bytes(b'1' * 3000)\ntime.sleep(0.9)\n"
            "(base / 'blobs' / 'f.incomplete').rename(base / 'blobs' / 'f')\n(base / 'snapshots' / 'r').mkdir(parents=True)\n")
    job = setup.Job("whisper", "w")
    seen = []
    original = setup.dir_bytes
    setup.dir_bytes = lambda p: (seen.append(original(p)), seen[-1])[1]
    try:
        setup._hf_download(job, code, "a/b", cache, 1)
    finally:
        setup.dir_bytes = original
    assert max(seen) >= 3000 and job.done >= 3000 and setup.hf_cached("a/b", cache)
    with pytest.raises(setup.SetupError) as caught:
        setup._hf_download(setup.Job("w", "w"), "pass", "c/d", cache, 1)
    assert "в кеше нет" in str(caught.value)


def test_hf_download_runs_online_in_a_child_process(cache):
    """Сервер работает с HF_HUB_OFFLINE=1, а загрузчик обязан выключить это для своего процесса."""
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        code = ("import os\nfrom pathlib import Path\nassert os.environ['HF_HUB_OFFLINE'] == '0' and os.environ['TRANSFORMERS_OFFLINE'] == '0'\n"
                "base = Path(os.environ['SETUP_CACHE']) / 'models--e--f'\n(base / 'snapshots' / 'r').mkdir(parents=True)\n(base / 'blobs').mkdir(parents=True)\n")
        setup._hf_download(setup.Job("e", "e"), code, "e/f", cache, 1)
    finally:
        os.environ.pop("HF_HUB_OFFLINE")


# --------------------------------------------------------------------------- Ollama (заглушка по HTTP)

class _Ollama(BaseHTTPRequestHandler):
    models: list[str] = []
    mode = "ok"

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"models": [{"name": n, "size": 1} for n in self.models]}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        if self.mode == "404":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        rows = [{"status": "pulling manifest"}, {"status": "pulling abc", "total": 1000, "completed": 250}, {"status": "pulling abc", "total": 1000, "completed": 1000}]
        if self.mode == "error":
            rows = [{"status": "pulling manifest"}, {"error": "диск переполнен"}]
        for row in rows:
            self.wfile.write((json.dumps(row) + "\n").encode())
            self.wfile.flush()
        if self.mode == "ok":
            type(self).models = ["qwen3:4b"]


@pytest.fixture()
def ollama(monkeypatch):
    _Ollama.models, _Ollama.mode = [], "ok"
    server = HTTPServer(("127.0.0.1", 0), _Ollama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "OLLAMA_URL", f"http://127.0.0.1:{server.server_port}")
    yield _Ollama
    server.shutdown()


def test_pull_llm_streams_progress(ollama):
    job = setup.Job("llm", "m")
    setup._pull_llm(job, {})
    assert job.total == 1000 and job.done == 1000 and "pulling abc" in job.log
    assert llm.has_model(llm.list_models(config.OLLAMA_URL), "qwen3:4b")


def test_pull_llm_errors_are_explained(ollama):
    ollama.mode = "error"
    with pytest.raises(setup.SetupError) as caught:
        setup._pull_llm(setup.Job("llm", "m"), {})
    assert "диск переполнен" in str(caught.value)
    ollama.mode = "404"
    with pytest.raises(setup.SetupError) as caught:
        setup._pull_llm(setup.Job("llm", "m"), {"llm_model": "нет-такой:1"})
    assert "нет-такой:1" in str(caught.value)
    ollama.mode = "ok"
    ollama.models = []
    setup._pull_llm(setup.Job("llm", "m"), {"llm_model": "qwen3:4b"})


def test_pull_llm_without_server(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    with pytest.raises(setup.SetupError) as caught:
        setup._pull_llm(setup.Job("llm", "m"), {})
    assert "не запущена" in str(caught.value)


def test_start_ollama_guards(monkeypatch):
    monkeypatch.setattr(setup, "which", lambda name: None)
    with pytest.raises(setup.SetupError):
        setup._start_ollama(setup.Job("o", "o"), {})
    monkeypatch.setattr(setup, "which", lambda name: "/bin/true")
    monkeypatch.setattr(config, "OLLAMA_URL", "http://192.168.1.5:11434")
    with pytest.raises(setup.SetupError) as caught:
        setup._start_ollama(setup.Job("o", "o"), {})
    assert "внешний" in str(caught.value)


def test_brew_needs_homebrew(monkeypatch):
    monkeypatch.setattr(setup, "which", lambda name: None)
    with pytest.raises(setup.SetupError) as caught:
        setup.HANDLERS["ffmpeg"](setup.Job("f", "f"), {})
    assert "brew.sh" in str(caught.value)
    calls = []
    monkeypatch.setattr(setup, "which", lambda name: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(setup, "stream_command", lambda job, argv, env=None, poll=None, label="", on_line=None: calls.append(argv))
    setup.HANDLERS["ollama_bin"](setup.Job("o", "o"), {})
    assert calls == [["/opt/homebrew/bin/brew", "install", "ollama"]]


def test_pip_installs_into_the_running_environment(monkeypatch):
    calls = []
    monkeypatch.setattr(setup, "stream_command", lambda job, argv, env=None, poll=None, label="", on_line=None: calls.append(argv))
    setup.HANDLERS["vision"](setup.Job("v", "v"), {})
    setup.HANDLERS["packages"](setup.Job("p", "p"), {})
    assert calls[0] == [sys.executable, "-m", "pip", "install", "pyobjc-framework-Vision>=10"]
    assert calls[1][:5] == [sys.executable, "-m", "pip", "install", "-r"] and calls[1][5].endswith("requirements.txt")


def test_every_component_has_a_handler_and_title():
    items = setup.check({})
    runnable = {c["id"] for c in items if c["action"] != "" or c["id"] in {"llm", "whisper", "embedding", "ollama_server", "voice"}}
    assert runnable <= set(setup.HANDLERS) and set(setup.HANDLERS) <= set(setup.TITLES)
    assert {c["id"] for c in items} == set(setup.HANDLERS)


# --------------------------------------------------------------------------- API

@pytest.fixture()
def keep_config(client):
    core = client.app.state.core
    before = dict(core.cfg)
    yield core
    core.apply_settings(before)


def test_api_status_shape(client, monkeypatch):
    data = client.get("/api/setup").json()
    assert {"components", "folders", "disk", "ready", "done", "fresh", "brew", "needsWizard", "job"} <= set(data)
    assert {"id", "title", "group", "state", "required", "mb", "action"} <= set(data["components"][0])
    assert data["fresh"] is False and data["folders"]["recordingsOk"] is True


def test_api_run_validates_and_serializes(client, monkeypatch):
    assert client.post("/api/setup/run", json={"component": "нет"}).status_code == 404
    gate = threading.Event()
    monkeypatch.setitem(setup.HANDLERS, "voice", lambda job, cfg: gate.wait(3))
    assert client.post("/api/setup/run", json={"component": "voice"}).status_code == 200
    assert client.post("/api/setup/run", json={"component": "voice"}).status_code == 409
    assert client.get("/api/setup").json()["job"]["state"] == "running"
    assert client.post("/api/setup/cancel").json() == {"cancelled": True}
    gate.set()
    wait_done()
    assert client.post("/api/setup/cancel").json() == {"cancelled": False}


def test_api_folders_are_created_and_saved(client, keep_config, tmp_path):
    rec, vault = tmp_path / "новые" / "записи", tmp_path / "заметки" / "Встречи"
    data = client.post("/api/setup/folders", json={"recordings_dir": str(rec), "obsidian_dir": str(vault)}).json()
    assert rec.is_dir() and vault.is_dir() and data["folders"]["recordingsOk"] and data["folders"]["obsidianOk"]
    cfg = keep_config.cfg
    assert cfg["recordings_dir"] == str(rec) and cfg["obsidian_dir"] == str(vault)
    assert cfg["archive_dir"] == str(rec / "Обработано")           # архив по умолчанию едет за папкой записей
    assert client.post("/api/setup/folders", json={"recordings_dir": "относительный/путь", "obsidian_dir": str(vault)}).status_code == 400


def test_api_finish_keeps_other_settings_and_survives_settings_save(client, keep_config):
    before = dict(keep_config.cfg)
    assert client.post("/api/setup/finish").json()["done"] is True
    assert keep_config.cfg["setup_done"] is True and keep_config.cfg["llm_model"] == before["llm_model"]
    settings = client.get("/api/settings").json()
    settings.pop("setup_done", None)
    assert client.put("/api/settings", json=settings).status_code == 200
    assert keep_config.cfg["setup_done"] is True                  # сохранение настроек из формы не сбрасывает «мастер пройден»


def test_token_cannot_reach_setup(client):
    from fastapi.testclient import TestClient
    made = client.post("/api/access", json={"name": "мастер", "kind": "cli", "write": True}).json()
    try:
        plain = TestClient(client.app)
        for method, path in [("GET", "/api/setup"), ("POST", "/api/setup/run"), ("POST", "/api/setup/folders"), ("POST", "/api/setup/finish")]:
            assert plain.request(method, path, headers={"Authorization": "Bearer " + made["token"]}, json={}).status_code == 403
    finally:
        client.delete(f"/api/access/{made['id']}")


def test_api_folders_keep_a_custom_archive(client, keep_config, tmp_path):
    keep_config.apply_settings({**keep_config.cfg, "archive_dir": str(tmp_path / "мой-архив")})
    client.post("/api/setup/folders", json={"recordings_dir": str(tmp_path / "r2"), "obsidian_dir": str(tmp_path / "v2")})
    assert keep_config.cfg["archive_dir"] == str(tmp_path / "мой-архив")


def test_api_models_choice(client, keep_config):
    data = client.post("/api/setup/models", json={"llm_model": "qwen3:8b", "asr_model": "medium"}).json()
    assert keep_config.cfg["llm_model"] == "qwen3:8b" and keep_config.cfg["asr_model"] == "medium"
    items = by_id(data["components"])
    assert items["llm"]["title"].endswith("qwen3:8b") and "whisper-medium-mlx" in items["whisper"]["note"]
    assert client.post("/api/setup/models", json={"llm_model": "плохо имя!", "asr_model": "turbo"}).status_code == 400
    assert client.post("/api/setup/models", json={"llm_model": "", "asr_model": "turbo"}).status_code == 200
    assert keep_config.cfg["llm_model"] == ""


# --------------------------------------------------------------------------- выбор папки

def test_pick_folder_runs_macos_dialog_and_handles_cancel(monkeypatch, tmp_path):
    import subprocess
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=str(tmp_path / "Записи") + "/\n", stderr="")
    monkeypatch.setattr(setup.sys, "platform", "darwin")
    monkeypatch.setattr(setup, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(setup.subprocess, "run", fake)
    assert setup.pick_folder("recordings", str(tmp_path / "нет" / "ещё")) == str(tmp_path / "Записи")
    assert calls[0][0] == "osascript" and calls[0][-1] == str(tmp_path)  # стартует с ближайшей существующей папки
    monkeypatch.setattr(setup.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="execution error: User canceled. (-128)"))
    assert setup.pick_folder("vault") == ""
    monkeypatch.setattr(setup.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"))
    with pytest.raises(RuntimeError):
        setup.pick_folder("vault")
    with pytest.raises(ValueError):
        setup.pick_folder("system")


def test_pick_folder_unavailable_off_macos(monkeypatch):
    monkeypatch.setattr(setup.sys, "platform", "linux")
    assert setup.can_pick() is False
    with pytest.raises(RuntimeError):
        setup.pick_folder("recordings")


def test_api_pick_folder(client, monkeypatch):
    monkeypatch.setattr(setup, "pick_folder", lambda kind, start="": f"/tmp/{kind}")
    assert client.post("/api/setup/pick-folder", json={"kind": "vault"}).json() == {"path": "/tmp/vault"}
    monkeypatch.undo()
    # без macOS окна нет: понятная ошибка, а неизвестный вид папки отвергается
    monkeypatch.setattr(setup.sys, "platform", "linux")
    assert client.post("/api/setup/pick-folder", json={"kind": "vault"}).status_code == 409
    assert client.post("/api/setup/pick-folder", json={"kind": "system"}).status_code == 400


# --------------------------------------------------------------------------- этапы, время, места

def test_pip_output_moves_through_stages():
    job = setup.Job("packages", "Python-пакеты")
    parse = setup.pip_line_parser(job)
    assert job.stages == setup.PIP_STAGES and job.stage == 0
    parse("Collecting fastapi>=0.110 (from -r requirements.txt (line 1))")
    assert job.stage == 1 and "fastapi" in job.note and "найдено: 1" in job.note
    parse("Downloading torch-2.4.0-cp313-none-macosx_11_0_arm64.whl (62.1 MB)")
    assert "62.1 MB" in job.note
    parse("Collecting uvicorn")
    assert "найдено: 2" in job.note
    parse("Installing collected packages: a, b, c")
    assert job.stage == 2 and "3 пакетов" in job.note
    parse("Collecting late")             # этапы не откатываются назад
    assert job.stage == 2


def test_brew_output_gives_download_percent_then_install():
    job = setup.Job("ffmpeg", "ffmpeg")
    parse = setup.brew_line_parser(job)
    parse("==> Fetching ffmpeg")
    assert job.stage == 1
    parse("######################                                            34.5%")
    assert (job.done, job.total) == (345, 1000) and job.view()["percent"] == 34
    parse("==> Pouring ffmpeg--7.1.arm64_sequoia.bottle.tar.gz")
    assert job.stage == 2 and job.total == 0


def test_job_view_reports_speed_and_eta(monkeypatch):
    job = setup.Job("whisper", "Модель")
    job.bytes = True
    job.total = 1000
    clock = [100.0]
    monkeypatch.setattr(setup.time, "time", lambda: clock[0])
    job.done = 0
    job.view()
    clock[0] = 110.0
    job.done = 200
    view = job.view()
    assert view["speed"] == 20 and view["eta"] == 40
    job.bytes = False
    assert job.view()["speed"] is None and job.view()["eta"] is not None


def test_job_without_progress_has_no_eta():
    job = setup.Job("packages", "Python-пакеты")
    view = job.view()
    assert view["eta"] is None and view["speed"] is None and view["percent"] is None


def test_stream_command_feeds_the_line_parser(tmp_path):
    job = setup.Job("x", "x")
    seen = []
    setup.stream_command(job, [sys.executable, "-c", "print('Collecting a'); print('Collecting b')"], on_line=seen.append)
    assert seen == ["Collecting a", "Collecting b"]
    broken = []

    def boom(line):
        broken.append(line)
        raise RuntimeError("разбор сломался")
    setup.stream_command(setup.Job("y", "y"), [sys.executable, "-c", "print('q')"], on_line=boom)   # не должно падать
    assert broken == ["q"]


def test_places_and_reveal(monkeypatch, tmp_path):
    cfg = {"recordings_dir": str(tmp_path / "rec"), "obsidian_dir": str(tmp_path / "vault")}
    rows = {row["key"]: row for row in setup.places(cfg)}
    assert set(rows) == {"program", "data", "models", "recordings", "notes", "logs"}
    assert rows["recordings"]["path"] == str(tmp_path / "rec") and rows["recordings"]["exists"] is False
    assert rows["program"]["path"] == str(setup.config.PROJECT_ROOT)
    with pytest.raises(ValueError):
        setup.reveal("../etc", cfg)
    monkeypatch.setattr(setup.sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        setup.reveal("program", cfg)
    calls = []
    monkeypatch.setattr(setup.sys, "platform", "darwin")
    monkeypatch.setattr(setup, "which", lambda name: "/usr/bin/open")
    monkeypatch.setattr(setup.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    with pytest.raises(RuntimeError):
        setup.reveal("recordings", cfg)          # папки ещё нет
    (tmp_path / "rec").mkdir()
    assert setup.reveal("recordings", cfg) == str(tmp_path / "rec") and calls == [["open", str(tmp_path / "rec")]]


def test_api_status_has_places_and_reveal_is_guarded(client, monkeypatch):
    assert {"places"} <= set(client.get("/api/setup").json())
    monkeypatch.setattr(setup.sys, "platform", "linux")
    assert client.post("/api/setup/reveal", json={"key": "program"}).status_code == 409
    assert client.post("/api/setup/reveal", json={"key": "nope"}).status_code == 400


def test_system_rows_offer_a_fix_for_missing_engines(client):
    rows = {r["key"]: r for r in client.get("/api/system").json()["rows"]}
    assert rows["ocr"].get("fix") == 1 and rows["whisper"].get("fix") == 1


# --------------------------------------------------------------------------- режим приложения

def test_app_shell_is_wired_between_swift_and_frontend():
    root = Path(__file__).resolve().parents[1]
    swift = (root / "macos/Sources/MemoryDesk/WebWindow.swift").read_text("utf-8")
    main = (root / "frontend/src/main.tsx").read_text("utf-8")
    css = (root / "frontend/src/styles.css").read_text("utf-8")
    app = (root / "frontend/src/App.tsx").read_text("utf-8")
    assert 'applicationNameForUserAgent = "MemoryDeskApp"' in swift and "MemoryDeskApp" in main
    assert "data-shell='app'" in css and 'id="md-scroll"' in app


def test_app_icon_is_bundled():
    root = Path(__file__).resolve().parents[1] / "macos"
    import plistlib
    info = plistlib.loads((root / "Resources/Info.plist").read_bytes())
    icon = root / "Resources" / (info["CFBundleIconFile"] + ".icns")
    assert icon.read_bytes()[:4] == b"icns" and icon.stat().st_size > 10_000
    assert "AppIcon.icns" in (root / "build_app.sh").read_text("utf-8")
