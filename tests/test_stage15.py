"""Этап 8: токены доступа, командная строка и MCP-сервер."""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import memory_desk.access, memory_desk.audit, memory_desk.cli, memory_desk.client, memory_desk.config, memory_desk.jobs, memory_desk.mcp  # noqa: F401
    globals().update(access=memory_desk.access, audit=memory_desk.audit, cli=memory_desk.cli, client_mod=memory_desk.client,
                     config=memory_desk.config, jobs=memory_desk.jobs, mcp=memory_desk.mcp)


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "path", lambda: tmp_path / "audit.log")
    return lambda: audit.read_all()


@pytest.fixture()
def plain(client):
    """Клиент без заголовка кабинета и без cookie: так ходит командная строка."""
    from fastapi.testclient import TestClient
    return TestClient(client.app)


@pytest.fixture()
def tokens(client):
    made = []

    def make(name="тест", kind="cli", write=False):
        response = client.post("/api/access", json={"name": name, "kind": kind, "write": write})
        assert response.status_code == 200, response.text
        made.append(response.json()["id"])
        return response.json()
    yield make
    for token_id in made:
        client.delete(f"/api/access/{token_id}")


def bearer(token):
    return {"Authorization": "Bearer " + token["token"]}


# --------------------------------------------------------------------------- токены

def test_token_is_shown_once_and_stored_hashed(client, tokens):
    made = tokens("Мой Claude", "mcp", True)
    assert made["token"].startswith("mdt_") and len(made["token"]) > 40
    assert made["write"] is True and made["kindLabel"] == "MCP"
    listed = client.get("/api/access").json()
    assert all("token" not in row and "hash" not in row for row in listed["items"])
    assert any(row["id"] == made["id"] and row["hint"].startswith(made["token"][:8]) for row in listed["items"])
    raw = access.path().read_text("utf-8")
    assert made["token"] not in raw
    assert (access.path().stat().st_mode & 0o777) == 0o600


def test_token_validation(client):
    assert client.post("/api/access", json={"name": "x", "kind": "web"}).status_code == 400
    assert client.post("/api/access", json={"name": "я" * 81, "kind": "cli"}).status_code == 422
    assert access.verify("") is None and access.verify("abc") is None and access.verify("mdt_" + "x" * 300) is None
    row, token = access.create("", "cli")
    try:
        assert row["name"] == "Командная строка"       # пустое имя заменяется видом клиента
        assert access.verify(token)["id"] == row["id"]
        assert access.verify(token[:-1] + ("a" if token[-1] != "a" else "b")) is None
    finally:
        access.revoke(row["id"])


def test_token_limit(client):
    made = []
    try:
        for _ in range(access.MAX_TOKENS - len(access.listing())):
            made.append(access.create("п", "cli")[0]["id"])
        with pytest.raises(access.AccessError):
            access.create("лишний", "cli")
    finally:
        for token_id in made:
            access.revoke(token_id)


def test_revoked_token_stops_working(client, plain, tokens):
    made = tokens()
    assert plain.get("/api/meetings", headers=bearer(made)).status_code == 200
    assert client.delete(f"/api/access/{made['id']}").json() == {"revoked": True}
    assert plain.get("/api/meetings", headers=bearer(made)).status_code == 401
    assert client.delete(f"/api/access/{made['id']}").status_code == 404


def test_bearer_parsing():
    assert access.bearer("Bearer mdt_abc") == "mdt_abc" and access.bearer("bearer  mdt_abc ") == "mdt_abc"
    assert access.bearer("Basic abc") == "" and access.bearer("") == ""


# --------------------------------------------------------------------------- что токену можно

def test_read_token_reads_but_does_not_change(client, plain, tokens, items):
    made = tokens()
    headers = bearer(made)
    meeting_id = next(iter(items.values()))["id"]
    for path in ["/api/health", "/api/meetings", f"/api/meetings/{meeting_id}", "/api/tasks", "/api/people", "/api/projects",
                 "/api/jobs", "/api/access/me", "/api/search?q=контур"]:
        assert plain.get(path, headers=headers).status_code == 200, path
    me = plain.get("/api/access/me", headers=headers).json()
    assert me["name"] == "тест" and me["kind"] == "cli" and me["write"] is False
    # запись и всё остальное — нет, даже с заголовком кабинета
    for method, path in [("POST", "/api/ingest?name=a.mp3"), ("POST", "/api/jobs/scan"),
                         ("POST", f"/api/meetings/{meeting_id}/attachments/text"), ("POST", f"/api/meetings/{meeting_id}/archive"),
                         ("PUT", "/api/settings"), ("GET", "/api/access"), ("POST", "/api/access"), ("GET", "/api/audit"),
                         ("GET", f"/api/meetings/{meeting_id}/audio"), ("POST", "/api/export"), ("PATCH", f"/api/meetings/{meeting_id}")]:
        response = plain.request(method, path, headers={**headers, "x-memory-desk": "1"}, json={})
        assert response.status_code == 403, (method, path, response.status_code)


def test_cabinet_session_is_not_a_token(client):
    assert client.get("/api/access/me").status_code == 404
    assert client.get("/api/access").status_code == 200


def test_wrong_token_is_401_and_does_not_fall_back_to_cabinet(plain):
    assert plain.get("/api/meetings", headers={"Authorization": "Bearer mdt_nope"}).status_code == 401
    assert plain.post("/api/jobs/scan", headers={"Authorization": "Bearer mdt_nope", "x-memory-desk": "1"}).status_code == 401
    assert plain.post("/api/jobs/scan").status_code == 403         # без токена и без заголовка кабинета — по-прежнему отказ


def test_pin_lock_does_not_apply_to_tokens(client, plain, tokens, tmp_path, monkeypatch):
    import memory_desk.lock as lock
    monkeypatch.setattr(audit, "path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(lock, "_file", lambda: tmp_path / "lock.json")
    monkeypatch.setattr(lock, "_state_file", lambda: tmp_path / "lock-state.json")
    monkeypatch.setattr(lock, "_have_keychain", lambda: False)
    handle = client.app.state.lock
    made = tokens()
    try:
        assert client.post("/api/lock/pin", json={"pin": "4821"}).status_code == 200
        client.post("/api/lock/lock")
        assert client.get("/api/meetings").status_code == 423
        assert plain.get("/api/meetings", headers=bearer(made)).status_code == 200
    finally:
        lock.reset()
        handle.close()
        handle.forget_cache()
        client.cookies.clear()


# --------------------------------------------------------------------------- журнал

def test_journal_names_the_client_and_hides_the_question(client, plain, tokens, journal):
    reader = tokens("Чтение")
    writer = tokens("Запись", "mcp", True)
    secret = "секретная формулировка вопроса"
    plain.get("/api/search", params={"q": secret}, headers=bearer(reader))
    plain.post("/api/jobs/scan", headers=bearer(reader))                                  # отказ: нет права записи
    plain.post("/api/ingest", params={"name": "x.txt"}, content=b"1", headers=bearer(writer))   # отказ: не аудио
    rows = journal()
    read = [r for r in rows if r["action"] == "access.read"]
    assert read and read[-1]["actor"] == "cli" and "Чтение" in read[-1]["detail"] and "/api/search" in read[-1]["detail"]
    assert secret not in json.dumps(rows, ensure_ascii=False)
    denied = [r for r in rows if r["action"] == "access.denied"]
    assert denied and denied[0]["result"] == "denied" and denied[0]["actor"] == "cli"
    assert [r["actor"] for r in rows if r["action"] == "access.create"] == ["cabinet", "cabinet"]
    assert reader["token"] not in json.dumps(rows) and writer["token"] not in json.dumps(rows)
    assert audit.labels()["access.read"] and audit.labels()["ingest.file"]


def test_token_creation_and_revoke_are_journaled(client, journal):
    made = client.post("/api/access", json={"name": "Для журнала", "kind": "cli"}).json()
    client.delete(f"/api/access/{made['id']}")
    rows = journal()
    assert [r["action"] for r in rows[-2:]] == ["access.create", "access.revoke"]
    assert rows[-2]["actor"] == "cabinet" and "Для журнала" in rows[-2]["detail"] and made["token"] not in json.dumps(rows)


# --------------------------------------------------------------------------- приём файла

@pytest.fixture()
def recordings(client, tmp_path):
    folder = tmp_path / "rec"
    old = client.app.state.core.cfg
    client.app.state.core.cfg = {**old, "recordings_dir": str(folder)}
    yield folder
    client.app.state.core.cfg = old


def test_ingest_saves_audio_and_never_overwrites(client, plain, tokens, recordings, monkeypatch):
    writer = tokens("Запись", "cli", True)
    first = plain.post("/api/ingest", params={"name": "созвон.mp3"}, content=b"ID3data", headers=bearer(writer))
    assert first.status_code == 200 and first.json() == {"file": "созвон.mp3", "bytes": 7, "queued": True}
    second = plain.post("/api/ingest", params={"name": "созвон.mp3"}, content=b"other", headers=bearer(writer))
    assert second.json()["file"] == "созвон (2).mp3"
    assert (recordings / "созвон.mp3").read_bytes() == b"ID3data"
    assert sorted(p.name for p in recordings.iterdir()) == ["созвон (2).mp3", "созвон.mp3"]     # ни одного .part
    monkeypatch.setattr(jobs, "start", lambda cfg, files=None: {"created": True, "count": 2, "duplicates": 0, "message": "ok"})
    third = plain.post("/api/ingest", params={"name": "ещё.wav", "process": "true"}, content=b"RIFF", headers=bearer(writer))
    assert third.json()["scan"]["created"] is True


def test_ingest_rejects_bad_names_and_empty_files(client, plain, tokens, recordings):
    writer = tokens("Запись", "cli", True)
    for name in ["", "заметки.txt", "../../etc/x.mp3.exe", ".скрытый.mp3"]:
        assert plain.post("/api/ingest", params={"name": name}, content=b"x", headers=bearer(writer)).status_code == 400, name
    assert plain.post("/api/ingest", params={"name": "пусто.mp3"}, content=b"", headers=bearer(writer)).status_code == 400
    traversal = plain.post("/api/ingest", params={"name": "../../выход.mp3"}, content=b"x", headers=bearer(writer))
    assert traversal.json()["file"] == "выход.mp3" and (recordings / "выход.mp3").exists()      # путь отброшен, остаётся имя
    assert not list(recordings.glob("*.part"))


def test_ingest_target_unit(tmp_path):
    assert jobs.ingest_target(str(tmp_path), "a\\b\\c.M4A").name == "c.M4A"
    (tmp_path / "c.M4A").write_bytes(b"1")
    assert jobs.ingest_target(str(tmp_path), "c.M4A").name == "c (2).M4A"
    with pytest.raises(ValueError):
        jobs.ingest_target(str(tmp_path), "c.opus")


# --------------------------------------------------------------------------- поиск

def test_search_returns_passages_with_sources(client):
    data = client.get("/api/search", params={"q": "тестовый контур", "limit": 3}).json()
    assert 0 < len(data["items"]) <= 3 and data["total"] == len(data["items"])
    first = data["items"][0]
    assert {"meetingId", "title", "date", "t", "section", "text", "score"} <= set(first) and first["meetingId"]
    assert client.get("/api/search", params={"q": " "}).status_code == 400
    assert client.get("/api/search", params={"q": "контур", "project": "нет-такого"}).json()["items"] == []


# --------------------------------------------------------------------------- клиент (настоящий HTTP)

class _Stub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        replies = {"/ok": (200, {"hello": "мир", "auth": self.headers.get("Authorization", "")}), "/denied": (403, {"detail": "Токену этот маршрут недоступен"}),
                   "/bad": (401, {"detail": "Токен доступа не подошёл"})}
        if self.path.startswith("/html"):
            body, status = b"<html>not json</html>", 200
        else:
            status, payload = replies.get(self.path.split("?")[0], (500, {}))
            body = json.dumps(payload).encode()
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def stub():
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_client_call_and_error_mapping(stub, monkeypatch):
    monkeypatch.setenv("MEMORY_DESK_URL", stub)
    monkeypatch.setenv("MEMORY_DESK_TOKEN", "mdt_abc")
    assert client_mod.call("GET", "/ok") == {"hello": "мир", "auth": "Bearer mdt_abc"}
    for path, code, text in [("/denied", 4, "недоступен"), ("/bad", 4, "не подошёл"), ("/other", 1, "500")]:
        with pytest.raises(client_mod.ClientError) as caught:
            client_mod.call("GET", path)
        assert caught.value.code == code and text in str(caught.value)
    with pytest.raises(client_mod.ClientError) as caught:
        client_mod.call("GET", "/html")
    assert "не JSON" in str(caught.value)
    monkeypatch.delenv("MEMORY_DESK_TOKEN")
    monkeypatch.setenv("MEMORY_DESK_CLIENT_CONFIG", str(Path(os.devnull).parent / "нет-файла.json"))
    with pytest.raises(client_mod.ClientError) as caught:
        client_mod.call("GET", "/ok")
    assert caught.value.code == 4 and "memory-desk login" in str(caught.value)


def test_client_saved_config_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DESK_CLIENT_CONFIG", str(tmp_path / "дир" / "client.json"))
    monkeypatch.delenv("MEMORY_DESK_URL", raising=False)
    monkeypatch.delenv("MEMORY_DESK_TOKEN", raising=False)
    path = client_mod.save("http://127.0.0.1:8502/", "mdt_xyz")
    assert (path.stat().st_mode & 0o777) == 0o600
    assert client_mod.connection() == ("http://127.0.0.1:8502", "mdt_xyz")


# --------------------------------------------------------------------------- командная строка

@pytest.fixture()
def routed(client, plain, monkeypatch):
    """`client.call` ходит в приложение из тестов с настоящим токеном, а не по сети."""
    state = {"token": ""}

    def fake(method, path, params=None, body=None, data=None, token=True, timeout=60, url=None, auth=None):
        headers = {"Authorization": "Bearer " + (auth if auth is not None else state["token"])} if token else {}
        response = plain.request(method, path, params={k: v for k, v in (params or {}).items() if v not in (None, "")},
                                 json=body, content=data, headers=headers)
        if response.status_code >= 400:
            raise client_mod.ClientError(response.json().get("detail", "ошибка"), 4 if response.status_code in (401, 403) else 1, response.status_code)
        return response.json()
    monkeypatch.setattr(client_mod, "call", fake)
    return state


def run_cli(*argv):
    return cli.main(list(argv))


def test_parse_since():
    from datetime import date
    today = date(2026, 10, 14)
    assert cli.parse_since("7d", today) == "2026-10-07" and cli.parse_since("2w", today) == "2026-09-30"
    assert cli.parse_since("1m", today) == "2026-09-14" and cli.parse_since("2026-10-01", today) == "2026-10-01"
    for bad in ["", "вчера", "7", "d7", "2026-13-40"]:
        with pytest.raises(ValueError):
            cli.parse_since(bad, today)


def test_cli_list_search_status_json(routed, tokens, capsys):
    routed["token"] = tokens()["token"]
    assert run_cli("list", "--json") == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["total"] == len(listed["items"]) > 0
    assert run_cli("list", "--since", "1d", "--json") == 0
    assert json.loads(capsys.readouterr().out)["total"] <= listed["total"]
    assert run_cli("list", "decisions", "--limit", "2", "--json") == 0
    assert len(json.loads(capsys.readouterr().out)["items"]) <= 2
    assert run_cli("search", "тестовый контур", "--json") == 0
    assert json.loads(capsys.readouterr().out)["items"]
    assert run_cli("list", "--since", "вчера") == 2
    assert "Срок задаётся" in capsys.readouterr().err
    assert run_cli("list") == 0 and "id=" in capsys.readouterr().out


def test_cli_status_when_server_is_not_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_DESK_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("MEMORY_DESK_TOKEN", "mdt_x")
    assert run_cli("status") == 3
    assert "не запущен" in capsys.readouterr().out
    assert run_cli("status", "--json") == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False and "start.command" in payload["hint"]
    assert run_cli("list") == 3
    assert "не запущен" in capsys.readouterr().err


def test_cli_without_token_says_how_to_get_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_DESK_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("MEMORY_DESK_TOKEN", raising=False)
    monkeypatch.setenv("MEMORY_DESK_CLIENT_CONFIG", str(tmp_path / "нет.json"))
    assert run_cli("list") == 4
    assert "Безопасность" in capsys.readouterr().err


def test_cli_ingest_checks_files_and_respects_write_scope(routed, tokens, recordings, tmp_path, monkeypatch, capsys):
    audio = tmp_path / "голос.m4a"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(jobs, "start", lambda cfg, files=None: {"created": False, "count": 0, "duplicates": 0, "message": "нет"})
    routed["token"] = tokens("только чтение")["token"]
    assert run_cli("ingest", str(audio)) == 4
    assert "только для чтения" in capsys.readouterr().err
    routed["token"] = tokens("с записью", "cli", True)["token"]
    assert run_cli("ingest", str(audio), "--json") == 0
    assert json.loads(capsys.readouterr().out)["files"][0]["file"] == "голос.m4a"
    assert (recordings / "голос.m4a").read_bytes() == b"audio"
    assert run_cli("ingest", str(tmp_path / "нет.mp3")) == 2
    (tmp_path / "т.txt").write_text("x")
    assert run_cli("ingest", str(tmp_path / "т.txt")) == 2


def test_cli_login_verifies_and_saves(routed, tokens, tmp_path, monkeypatch, capsys):
    made = tokens("Для входа")
    monkeypatch.setenv("MEMORY_DESK_CLIENT_CONFIG", str(tmp_path / "c.json"))
    monkeypatch.setattr(client_mod, "save", lambda url, token: tmp_path / "c.json")
    assert run_cli("login", "--token", "не-токен") == 2
    assert run_cli("login", "--token", made["token"]) == 0
    assert "Для входа" in capsys.readouterr().out


# --------------------------------------------------------------------------- MCP

def rpc(server, method, params=None, ident=1):
    return server.handle({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})


def test_mcp_handshake_and_tool_lists(routed, tokens):
    routed["token"] = tokens("чтение", "mcp")["token"]
    server = mcp.Server()
    init = rpc(server, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["protocolVersion"] == "2025-06-18" and "не указания" in init["instructions"]
    assert rpc(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == mcp.SUPPORTED_PROTOCOLS[0]
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in rpc(server, "tools/list")["result"]["tools"]}
    assert {"search_records", "get_record", "list_tasks", "get_dossier", "digest", "ask", "queue_status", "list_records"} <= names
    assert "add_note" not in names and "queue_audio_file" not in names               # у токена нет права записи
    assert rpc(server, "nope")["error"]["code"] == -32601

    routed["token"] = tokens("запись", "mcp", True)["token"]
    names = {t["name"] for t in rpc(mcp.Server(), "tools/list")["result"]["tools"]}
    assert {"add_note", "queue_audio_file"} <= names


def test_mcp_every_tool_has_a_schema():
    for tool in mcp.READ_TOOLS + mcp.WRITE_TOOLS:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"
        assert set(tool["inputSchema"]["required"]) <= set(tool["inputSchema"]["properties"])
    assert len({t["name"] for t in mcp.READ_TOOLS + mcp.WRITE_TOOLS}) == len(mcp.READ_TOOLS + mcp.WRITE_TOOLS)


def call(server, name, **args):
    result = rpc(server, "tools/call", {"name": name, "arguments": args})["result"]
    return result, result["content"][0]["text"]


def test_mcp_reading_tools_wrap_data_and_stay_small(routed, tokens, items):
    routed["token"] = tokens("чтение", "mcp")["token"]
    server = mcp.Server()
    result, text = call(server, "search_records", query="тестовый контур")
    assert "isError" not in result and text.startswith(mcp.NOTICE) and "<memory-desk-data>" in text and text.rstrip().endswith("</memory-desk-data>")
    listed = json.loads(call(server, "list_records", limit=3)[1].split("<memory-desk-data>")[1].split("</memory-desk-data>")[0])
    assert 0 < len(listed["items"]) <= 3
    record_id = listed["items"][0]["id"]
    record = json.loads(call(server, "get_record", id=record_id, with_transcript=True)[1].split("<memory-desk-data>")[1].split("</memory-desk-data>")[0])
    assert record["id"] == record_id and "transcript" in record and len(record["transcript"]) < mcp.MAX_CHARS
    without = json.loads(call(server, "get_record", id=record_id)[1].split("<memory-desk-data>")[1].split("</memory-desk-data>")[0])
    assert "transcript" not in without
    for tool, args in [("list_tasks", {"kind": "decisions"}), ("list_people_projects", {}), ("queue_status", {}), ("digest", {"period": "this_month"})]:
        result, text = call(server, tool, **args)
        assert "isError" not in result or "записей нет" in text, (tool, text)
    people = json.loads(call(server, "list_people_projects")[1].split("<memory-desk-data>")[1].split("</memory-desk-data>")[0])
    dossier = call(server, "get_dossier", kind="person", id=people["people"][0]["id"])
    assert "isError" not in dossier[0]


def test_mcp_errors_are_results_not_crashes(routed, tokens):
    routed["token"] = tokens("чтение", "mcp")["token"]
    server = mcp.Server()
    assert call(server, "get_record", id="../etc")[0]["isError"] is True
    assert call(server, "get_record", id="нет-такой-записи")[0]["isError"] is True
    assert call(server, "no_such_tool")[0]["isError"] is True
    assert call(server, "list_tasks", since="вчера")[0]["isError"] is True
    result, text = call(server, "add_note", record_id="x", text="привет")
    assert result["isError"] is True and "только для чтения" in text


def test_mcp_write_tools_with_write_token(routed, tokens, items, recordings, tmp_path, monkeypatch, client):
    routed["token"] = tokens("запись", "mcp", True)["token"]
    server = mcp.Server()
    meeting_id = next(iter(items.values()))["id"]
    result, text = call(server, "add_note", record_id=meeting_id, text="Заметка из MCP: проверить доступ", title="Из MCP")
    assert "isError" not in result, text
    attached = client.get(f"/api/meetings/{meeting_id}/attachments").json()["items"]
    mine = [a for a in attached if a.get("name", "").startswith("Из MCP")]
    assert mine
    for entry in mine:
        client.delete(f"/api/meetings/{meeting_id}/attachments/{entry['id']}")
    assert call(server, "add_note", record_id=meeting_id, text="   ")[0]["isError"] is True
    audio = tmp_path / "заметка.mp3"
    audio.write_bytes(b"ID3")
    monkeypatch.setattr(jobs, "start", lambda cfg, files=None: {"created": False, "count": 0, "duplicates": 0, "message": "нет"})
    assert "isError" not in call(server, "queue_audio_file", path=str(audio))[0]
    assert (recordings / "заметка.mp3").exists()
    assert call(server, "queue_audio_file", path=str(tmp_path / "нет.mp3"))[0]["isError"] is True


def test_mcp_clip_marks_truncation():
    short = mcp._clip({"a": 1})
    assert "обрезан" not in short
    long = mcp._clip({"rows": ["строка " * 20] * 2000})
    assert len(long) < mcp.MAX_CHARS + 300 and "ответ обрезан" in long


def test_mcp_stdio_loop(routed, tokens):
    import io
    routed["token"] = tokens("чтение", "mcp")["token"]
    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nнеjson\n[1]\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    stdout = io.StringIO()
    mcp.serve(stdin, stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert replies[1]["error"]["code"] == -32700 and replies[2]["error"]["code"] == -32600 and len(replies) == 3
