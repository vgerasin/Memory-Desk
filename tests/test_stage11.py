"""Этап 11.4: чтение с телефона по домашней сети. Шлюз только для чтения, вход по PIN, устройства, защита от чужих."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PIN = "2468"
PHONE = ("192.168.1.20", 50000)
BASE = "http://192.168.1.5:8734"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(client, tmp_path, monkeypatch):
    import memory_desk.audit as audit
    import memory_desk.lock as lock
    import memory_desk.remote as rm
    from memory_desk.remote_gateway import build_gateway
    monkeypatch.setattr(lock, "_file", lambda: tmp_path / "lock.json")
    monkeypatch.setattr(lock, "_state_file", lambda: tmp_path / "lock-state.json")
    monkeypatch.setattr(lock, "_have_keychain", lambda: False)
    monkeypatch.setattr(rm, "_file", lambda: tmp_path / "remote.json")
    monkeypatch.setattr(audit, "path", lambda: tmp_path / "audit.log")
    original_start = rm.Remote.start
    monkeypatch.setattr(rm.Remote, "start", lambda self: False)           # второй сервер в обычных тестах не поднимается
    monkeypatch.setenv("MEMORY_DESK_REMOTE_LOOPBACK", "1")
    clock = {"t": 2_000_000.0}
    monkeypatch.setattr(rm, "now", lambda: clock["t"])
    lock.write_record(lock.make_record(PIN))
    core = client.app.state.core
    remote = rm.Remote(core)
    remote.configure(enabled=True, pin=PIN)

    class Env:
        pass
    e = Env()
    e.original_start = original_start
    e.rm, e.lock, e.audit, e.remote, e.clock, e.core, e.dir = rm, lock, audit, remote, clock, core, tmp_path
    e.advance = lambda seconds: clock.__setitem__("t", clock["t"] + seconds)
    e.gateway = lambda: TestClient(build_gateway(remote), base_url=BASE, client=PHONE, headers={"x-memory-desk": "1"})
    e.phone = e.gateway()
    e.signin = lambda c=None: (c or e.phone).post("/api/login", json={"pin": PIN})
    e.lines = lambda: [json.loads(x) for x in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()] if (tmp_path / "audit.log").exists() else []
    yield e
    lock.delete_record()
    client.app.state.lock.forget_cache()


@pytest.fixture()
def note(client):
    """Заметка со стенограммой в хранилище фикстуры; убирается после теста."""
    import sys
    sys.path.insert(0, str(ROOT))
    import meetings_process
    import memory_desk.knowledge
    import test_stage3 as s3
    s3.mp, s3.knowledge = meetings_process, memory_desk.knowledge
    created = s3.Note(client).create()
    yield created
    created.remove()


def walk(value):
    """Все ключи и все строки вложенной структуры."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield "key", k
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)
    elif isinstance(value, str):
        yield "str", value


# --------------------------------------------------------------------------- адреса

@pytest.mark.parametrize("address,expected", [
    ("192.168.1.1", True), ("10.0.0.5", True), ("172.16.5.5", True), ("172.32.0.1", False), ("100.64.1.1", True),
    ("100.128.0.1", False), ("8.8.8.8", False), ("127.0.0.1", False), ("::1", False), ("fe80::1", True), ("fd00::1", True),
    ("::ffff:192.168.1.5", True), ("169.254.1.1", True), ("2001:4860:4860::8888", False), ("0.0.0.0", False), ("не адрес", False), ("", False),
])
def test_only_local_network_addresses(address, expected):
    from memory_desk import remote
    assert remote.is_lan(address) is expected


def test_host_names_and_device_names():
    from memory_desk import remote
    assert remote.host_of("192.168.1.5:8734") == "192.168.1.5" and remote.host_of("[fe80::1]:8734") == "fe80::1"
    assert remote.valid_hosts("a.ts.net, B.local;;  a.ts.net, <script>") == ["a.ts.net", "b.local"]
    ios = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    assert remote.device_name(ios) == "iPhone · Safari"
    assert remote.device_name("") == "Устройство"


# --------------------------------------------------------------------------- включение

def test_cannot_enable_without_pin_or_with_wrong_pin(env):
    rm, lock = env.rm, env.lock
    fresh = rm.Remote(env.core)
    fresh.configure(enabled=False)
    with pytest.raises(rm.RemoteError) as wrong:
        fresh.configure(enabled=True, pin="0000")
    assert wrong.value.status == 401 and not fresh.enabled
    lock.delete_record()
    env.remote._rec_at = -1e9
    with pytest.raises(rm.RemoteError) as none:
        fresh.configure(enabled=True, pin=PIN)
    assert "PIN" in str(none.value) and not fresh.enabled and not env.remote.enabled     # без PIN доступ выключен сам


def test_settings_are_validated(env):
    rm = env.rm
    with pytest.raises(rm.RemoteError):
        env.remote.configure(days=3)
    with pytest.raises(rm.RemoteError):
        env.remote.configure(port=80)
    with pytest.raises(rm.RemoteError):
        env.remote.configure(port=rm.config.PORT)
    assert env.remote.configure(days=30, transcript=True, hosts="Mac.tail1234.ts.net") == ["days", "transcript", "hosts"]
    state = env.remote.status()
    assert state["days"] == 30 and state["transcript"] is True and state["hosts"] == ["mac.tail1234.ts.net"]


# --------------------------------------------------------------------------- защита шлюза

def test_gateway_has_exactly_the_read_only_routes(env):
    """Меняется только вход и выход; весь остальной набор — чтение. Любой новый маршрут сломает этот тест и потребует решения."""
    app = env.phone.app
    found = set()
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            if method not in {"HEAD", "OPTIONS"}:
                found.add((method, route.path))
    pages = {("GET", p) for p in env.rm.STATIC}
    api = {("GET", "/api/session"), ("POST", "/api/login"), ("POST", "/api/logout"), ("GET", "/api/today"), ("GET", "/api/records"),
           ("GET", "/api/records/{meeting_id}"), ("GET", "/api/tasks"), ("GET", "/api/people"), ("GET", "/api/projects")}
    assert found == pages | api
    assert [m for m, _ in found if m not in {"GET", "POST"}] == []


def test_foreign_requests_are_refused(env):
    phone = env.phone
    assert env.signin().status_code == 200
    # чужой адрес
    outside = TestClient(phone.app, base_url=BASE, client=("8.8.8.8", 4000), headers={"x-memory-desk": "1"})
    assert outside.get("/api/session").status_code == 403 and outside.get("/").status_code == 403
    assert outside.post("/api/login", json={"pin": PIN}).status_code == 403
    # туннель и прокси
    for header in ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip", "via"):
        assert phone.get("/api/today", headers={header: "203.0.113.9"}).status_code == 403
    # чужое имя в Host (DNS-rebinding)
    assert phone.get("/api/session", headers={"host": "evil.example:8734"}).status_code == 403
    assert phone.get("/api/session", headers={"host": "8.8.8.8:8734"}).status_code == 403
    assert phone.get("/api/session", headers={"host": "192.168.1.5:8734"}).status_code == 200
    # запись без заголовка и с чужой страницы
    bare = TestClient(phone.app, base_url=BASE, client=PHONE)
    assert bare.post("/api/login", json={"pin": PIN}).status_code == 403
    assert phone.post("/api/login", json={"pin": PIN}, headers={"origin": "http://evil.example"}).status_code == 403
    assert phone.post("/api/login", json={"pin": PIN}, headers={"origin": BASE}).status_code == 200
    assert phone.post("/api/login", content=b"x" * 5000, headers={"content-type": "application/json"}).status_code == 413
    # этих методов нет вовсе
    for method in ("put", "patch", "delete"):
        assert getattr(phone, method)("/api/records/x").status_code == 403
    denied = [r for r in env.lines() if r["action"] == "remote.blocked"]
    assert denied and all(r["result"] == "denied" and r["actor"] == "phone" for r in denied)
    assert len(denied) < 12             # повторы журнал не засоряют


def test_gateway_off_means_nothing_answers(env):
    env.remote.configure(enabled=False)
    assert env.phone.get("/").status_code == 403 and env.phone.get("/api/session").status_code == 403
    assert env.signin().status_code == 403


def test_headers_of_pages_and_api(env):
    page = env.phone.get("/")
    assert page.status_code == 200 and "Memory Desk" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"] and "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff" and page.headers["referrer-policy"] == "no-referrer"
    for name in ("app.js", "style.css", "manifest.webmanifest", "icon.svg", "strings.json"):
        assert env.phone.get("/" + name).status_code == 200
    assert env.phone.get("/../../lock.json").status_code in (403, 404) and env.phone.get("/app.py").status_code == 404
    assert env.phone.get("/api/session").headers["cache-control"] == "no-store"
    source = (ROOT / "memory_desk/remote_web/app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in source and "eval(" not in source and ".style.cssText" not in source        # данные в разметку не попадают


# --------------------------------------------------------------------------- вход

def test_data_needs_a_signed_in_device(env):
    for path in ("/api/today", "/api/records", "/api/records/x", "/api/tasks", "/api/people", "/api/projects"):
        assert env.phone.get(path).status_code == 401
    assert env.phone.get("/api/session").json()["authenticated"] is False
    assert env.phone.post("/api/logout").status_code == 401
    assert env.phone.get("/api/today", cookies={"md_remote": "forged-token"}).status_code == 401


def test_sign_in_cookie_and_device_list(env):
    response = env.signin()
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "md_remote=" in cookie and "max-age=604800" in cookie
    assert env.phone.get("/api/session").json()["authenticated"] is True
    devices = env.remote.status()["devices"]
    assert len(devices) == 1 and devices[0]["lastIp"] == PHONE[0] and devices[0]["name"]
    stored = (env.dir / "remote.json").read_text(encoding="utf-8")
    token = env.phone.cookies.get("md_remote")
    assert token and token not in stored and PIN not in stored         # токена и PIN в файле нет, только хэш токена
    assert oct((env.dir / "remote.json").stat().st_mode & 0o777) == "0o600"


def test_wrong_pin_slows_down_and_finally_switches_access_off(env):
    rm = env.rm
    for attempt in range(1, 3):
        assert env.phone.post("/api/login", json={"pin": "0000"}).status_code == 401
    third = env.phone.post("/api/login", json={"pin": "0000"})
    assert third.status_code == 401 and third.json()["retryAfter"] == 5          # после третьей ошибки — задержка
    blocked = env.phone.post("/api/login", json={"pin": PIN})
    assert blocked.status_code == 429 and blocked.headers["retry-after"]         # даже верный PIN в паузу не проходит
    delays = []
    for _ in range(6):
        env.advance(1000)
        delays.append(env.phone.post("/api/login", json={"pin": "0000"}).json().get("retryAfter"))
    assert delays[:3] == [10, 20, 40]
    env.advance(1000)
    last = env.phone.post("/api/login", json={"pin": "0000"})                    # десятая подряд
    assert last.status_code == 403 and "выключен" in last.json()["detail"]
    assert env.remote.enabled is False and env.signin().status_code == 403
    actions = [r["action"] for r in env.lines()]
    assert actions.count("remote.login_failed") == rm.HARD_LIMIT and "remote.lockout" in actions
    env.advance(1000)
    with pytest.raises(rm.RemoteError):
        env.remote.configure(enabled=True, pin="1111")                           # включить заново можно только верным PIN на компьютере
    env.remote.configure(enabled=True, pin=PIN)
    assert env.signin().status_code == 200


def test_success_resets_the_counter(env):
    env.phone.post("/api/login", json={"pin": "0000"})
    env.phone.post("/api/login", json={"pin": "0000"})
    assert env.signin().status_code == 200
    assert env.remote.status()["failures"] == 0


def test_sign_out_revoke_expiry_and_pin_change(env):
    env.signin()
    assert env.phone.post("/api/logout").status_code == 200
    assert env.phone.get("/api/today").status_code == 401 and env.remote.status()["devices"] == []

    env.signin()
    second = env.gateway()
    env.signin(second)
    assert len(env.remote.status()["devices"]) == 2
    first_id = env.remote.status()["devices"][0]["id"]
    assert env.remote.revoke(first_id) is True and env.remote.revoke(first_id) is False
    assert env.phone.get("/api/today").status_code == 401 and second.get("/api/today").status_code == 200

    env.advance(7 * 86400 + 5)                                      # срок входа вышел
    assert second.get("/api/today").status_code == 401

    env.signin()
    env.lock.write_record(env.lock.make_record("1357"))              # PIN сменили: прежние устройства больше не подходят
    env.remote.forget_pin()
    assert env.phone.get("/api/today").status_code == 401
    env.lock.write_record(env.lock.make_record(PIN))
    env.remote.forget_pin()
    assert env.phone.get("/api/today").status_code == 401            # отпечаток привязан к соли: возврат прежнего PIN токены не оживляет
    assert env.signin().status_code == 200

    env.signin(second)
    assert env.remote.revoke_all() >= 2
    assert env.phone.get("/api/today").status_code == 401 and second.get("/api/today").status_code == 401


def test_device_limit_drops_the_oldest(env):
    clients = []
    for _ in range(env.rm.MAX_DEVICES + 2):
        c = env.gateway()
        env.advance(5)
        assert env.signin(c).status_code == 200
        clients.append(c)
    assert len(env.remote.status()["devices"]) == env.rm.MAX_DEVICES
    assert clients[0].get("/api/today").status_code == 401 and clients[-1].get("/api/today").status_code == 200


# --------------------------------------------------------------------------- что уходит на телефон

def test_reading_works_and_leaks_nothing(note, env, client):
    env.signin()
    core = env.core
    meeting = core.by_id[note.id]
    vault = str(core.cfg.get("obsidian_dir"))
    bodies = {"today": env.phone.get("/api/today"), "records": env.phone.get("/api/records"),
              "record": env.phone.get(f"/api/records/{meeting.id}"), "tasks": env.phone.get("/api/tasks"),
              "decisions": env.phone.get("/api/tasks", params={"kind": "decisions"}),
              "people": env.phone.get("/api/people"), "projects": env.phone.get("/api/projects")}
    for name, response in bodies.items():
        assert response.status_code == 200, name
    assert bodies["records"].json()["total"] >= 1 and bodies["record"].json()["title"] == meeting.title
    forbidden_keys = {"notePath", "obsidianUrl", "audioUrl", "hasAudio", "voice", "notes", "aliases", "path", "diarization", "attachments",
                      "views", "bookmarks", "speakers", "template", "extraSections"}
    for name, response in bodies.items():
        text = json.dumps(response.json(), ensure_ascii=False)
        for kind, value in walk(response.json()):
            if kind == "key":
                assert value not in forbidden_keys, (name, value)
        assert vault not in text and "/Users/" not in text and ".md" not in text, name
    people = client.get("/api/people").json()["items"]
    private = [p["notes"] for p in people if p.get("notes")]
    shown = json.dumps(bodies["people"].json(), ensure_ascii=False)
    assert all(note not in shown for note in private)


def test_transcript_only_when_allowed(note, env):
    env.signin()
    meeting = env.core.by_id[note.id]
    off = env.phone.get(f"/api/records/{meeting.id}").json()
    assert off["transcript"] is None and off["transcriptAllowed"] is False
    assert env.phone.get("/api/session").json()["transcript"] is False
    env.remote.configure(transcript=True)
    on = env.phone.get(f"/api/records/{meeting.id}").json()
    assert isinstance(on["transcript"], list) and on["transcriptAllowed"] is True
    if on["transcript"]:
        assert set(on["transcript"][0]) <= {"t", "speaker", "text"}


def test_unknown_record_and_search_and_limits(env):
    env.signin()
    assert env.phone.get("/api/records/нет-такой").status_code == 404
    assert env.phone.get("/api/records", params={"limit": 0}).status_code == 422 and env.phone.get("/api/records", params={"limit": 999}).status_code == 422
    assert env.phone.get("/api/tasks", params={"kind": "что-то"}).status_code == 422
    found = env.phone.get("/api/records", params={"q": "справочник"}).json()
    assert found["total"] >= 0 and len(found["items"]) <= 40


def test_journal_has_ids_not_texts(note, env):
    env.signin()
    meeting = env.core.by_id[note.id]
    env.phone.get(f"/api/records/{meeting.id}")
    env.phone.get(f"/api/records/{meeting.id}")                       # повторное открытие в журнал не пишется
    rows = env.lines()
    views = [r for r in rows if r["action"] == "remote.view"]
    assert len(views) == 1 and views[0]["id"] == meeting.id and views[0]["actor"] == "phone"
    logins = [r for r in rows if r["action"] == "remote.login"]
    assert len(logins) == 1 and logins[0]["detail"].startswith(PHONE[0])
    text = (env.dir / "audit.log").read_text(encoding="utf-8")
    assert meeting.title not in text and PIN not in text
    assert env.audit.verify()["ok"] is True
    env.audit._tail.clear()
    labels = env.audit.labels()
    assert all(code in labels for code in ("remote.login", "remote.login_failed", "remote.logout", "remote.view", "remote.blocked", "remote.lockout",
                                           "remote.settings", "remote.revoke", "remote.revoke_all"))


# --------------------------------------------------------------------------- управление из кабинета

@pytest.fixture()
def cabinet(env, client):
    client.app.state.lock.forget_cache()
    old = client.app.state.remote
    client.app.state.remote = env.remote
    assert client.post("/api/lock/unlock", json={"pin": PIN}).status_code == 200
    yield client
    client.app.state.remote = old
    client.cookies.clear()


def test_cabinet_manages_remote_access(env, cabinet):
    state = cabinet.get("/api/remote").json()
    assert state["enabled"] is True and state["hasPin"] and state["hardLimit"] == env.rm.HARD_LIMIT and state["devices"] == []
    env.signin()
    device = cabinet.get("/api/remote").json()["devices"][0]
    assert set(device) == {"id", "name", "created", "lastSeen", "lastIp", "expires", "current"}
    assert cabinet.put("/api/remote", json={"days": 3}).status_code == 400
    ok = cabinet.put("/api/remote", json={"days": 30, "transcript": True})
    assert ok.status_code == 200 and ok.json()["days"] == 30 and ok.json()["transcript"] is True
    assert cabinet.put("/api/remote", json={"enabled": False}).json()["enabled"] is False
    assert cabinet.put("/api/remote", json={"enabled": True}).status_code == 401                 # без PIN не включается
    assert cabinet.put("/api/remote", json={"enabled": True, "pin": "0000"}).status_code == 401
    assert cabinet.put("/api/remote", json={"enabled": True, "pin": PIN}).json()["enabled"] is True
    assert cabinet.delete(f"/api/remote/devices/{device['id']}").status_code == 200
    assert cabinet.delete(f"/api/remote/devices/{device['id']}").status_code == 404
    assert env.phone.get("/api/today").status_code == 401
    env.signin()
    assert cabinet.post("/api/remote/revoke-all").json()["devices"] == []
    codes = [r["action"] for r in env.lines()]
    assert {"remote.settings", "remote.revoke", "remote.revoke_all"} <= set(codes)
    for row in env.lines():
        if row["action"] == "remote.settings":
            assert PIN not in json.dumps(row)


def test_removing_pin_switches_remote_access_off(env, cabinet):
    assert env.remote.enabled
    assert cabinet.post("/api/lock/remove", json={"current": PIN}).status_code == 200
    assert env.remote.enabled is False and env.remote.status()["enabled"] is False
    env.lock.write_record(env.lock.make_record(PIN))                  # PIN задали снова — доступ сам не возвращается
    env.remote.forget_pin()
    assert env.remote.enabled is False and env.phone.get("/api/session").status_code == 403


# --------------------------------------------------------------------------- настоящий второй сервер

def test_real_listener_starts_serves_and_stops(env, monkeypatch):
    rm = env.rm
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setenv("MEMORY_DESK_REMOTE_BIND", "127.0.0.1")
    monkeypatch.setattr(rm.Remote, "start", env.original_start)
    env.remote.configure(port=port)                                   # смена порта перезапускает сервер
    try:
        assert env.remote.running
        data = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/session", timeout=5).read())
        assert data["authenticated"] is False
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/session", headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(req, timeout=5)
        assert refused.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as hidden:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/today", timeout=5)
        assert hidden.value.code == 401
        env.remote.configure(enabled=False)
        assert env.remote.running is False
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/session", timeout=2)
    finally:
        env.remote.stop()


def test_busy_port_is_reported(env, monkeypatch):
    rm = env.rm
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    monkeypatch.setenv("MEMORY_DESK_REMOTE_BIND", "127.0.0.1")
    monkeypatch.setattr(rm.Remote, "start", env.original_start)
    try:
        env.remote.configure(port=port)
        assert env.remote.running is False and str(port) in env.remote.error
    finally:
        busy.close()
        env.remote.stop()


# --------------------------------------------------------------------------- строки страницы телефона

def test_phone_strings_are_complete():
    data = json.loads((ROOT / "memory_desk/remote_web/strings.json").read_text(encoding="utf-8"))
    assert set(data) == {"ru", "en", "fr", "de", "es"}
    keys = set(data["ru"])
    for lang, table in data.items():
        assert set(table) == keys, lang
        for key, value in table.items():
            assert value.strip() and value.count("{0}") == data["ru"][key].count("{0}"), (lang, key)
    source = (ROOT / "memory_desk/remote_web/app.js").read_text(encoding="utf-8")
    import re
    used = set(re.findall(r"\bt\('([A-Za-z0-9]+)'", source))
    assert used <= keys, used - keys


# --------------------------------------------------------------------------- черновик приложения macOS (не собирался)

def test_macos_draft_is_consistent():
    """Swift здесь недоступен, поэтому проверяется только то, что видно без компилятора."""
    import plistlib
    base = ROOT / "macos"
    info = plistlib.loads((base / "Resources/Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "MemoryDesk" and info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["memorydesk"]
    sources = sorted((base / "Sources/MemoryDesk").glob("*.swift"))
    assert {p.name for p in sources} == {"main.swift", "AppDelegate.swift", "Server.swift", "WebWindow.swift", "StatusMenu.swift"}
    for path in sources:
        text = path.read_text(encoding="utf-8")
        import re
        stripped = "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in text.splitlines())      # комментарии с символами не считаем
        for open_, close in ("{}", "()", "[]"):
            assert stripped.count(open_) == stripped.count(close), (path.name, open_)
    package = (base / "Package.swift").read_text(encoding="utf-8")
    assert 'name: "MemoryDesk"' in package and "ЧЕРНОВИК" in package
    script = (base / "build_app.sh").read_text(encoding="utf-8")
    assert script.startswith("#!/bin/zsh") and "codesign" in script and "set -euo pipefail" in script
    assert "не собиралось" in (base / "README.md").read_text(encoding="utf-8")
    # сервер приложения запускает тот же скрипт, что и start.command, и порт по умолчанию совпадает
    server = (base / "Sources/MemoryDesk/Server.swift").read_text(encoding="utf-8")
    assert "meeting-assistant" in server and "8502" in server
    assert (ROOT / "meeting-assistant").exists() and "MEETING_WEBUI_PORT" in (ROOT / "meeting-assistant").read_text(encoding="utf-8")

