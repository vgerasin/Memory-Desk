"""Шлюз «только чтение» для телефона: набор маршрутов и проекций, больше у него ничего нет (см. remote.py).

Ответы собираются из белого списка полей. Пути к файлам на компьютере, ссылки Obsidian, адреса звука, заметки о людях,
образцы голосов и всё прочее, чего нет в списке, телефону не уходит, даже если появится в ответах кабинета.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import remote as rm
from . import services

HEADER = "x-memory-desk"
REFUSED = {"detail": "Недоступно"}

ITEM_KEYS = ("id", "text", "owner", "due", "dueText", "t", "status", "done", "draft", "meetingId", "meetingTitle", "meetingDate", "stuck")
PERSON_KEYS = ("id", "name", "initials", "color")
PROJECT_KEYS = ("id", "title", "color")
CARD_KEYS = ("id", "title", "date", "time", "duration", "status", "excerpt", "important")


def pick(source, keys) -> dict:
    return {k: source[k] for k in keys if isinstance(source, dict) and k in source}


def person_ref(person) -> dict:
    return pick(person, PERSON_KEYS)


def project_ref(project) -> dict | None:
    return pick(project, PROJECT_KEYS) if isinstance(project, dict) and project else None


def item(value):
    return pick(value, ITEM_KEYS) if isinstance(value, dict) else value


def text_item(value):
    return pick(value, ("text", "t")) if isinstance(value, dict) else value


def card(meeting: dict) -> dict:
    kind = meeting.get("type")
    return {**pick(meeting, CARD_KEYS), "project": project_ref(meeting.get("project")),
            "type": {"title": kind.get("title", "")} if isinstance(kind, dict) else None,
            "people": [person_ref(p) for p in meeting.get("people") or []]}


def hero(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    columns = value.get("columns") or {}
    return {**pick(value, ("kind", "title", "meetingId", "date", "time")), "project": project_ref(value.get("project")),
            "people": [person_ref(p) for p in value.get("people") or []],
            "columns": {"decisions": [item(i) for i in columns.get("decisions") or []],
                        "tasks": [item(i) for i in columns.get("tasks") or []],
                        "questions": [text_item(i) for i in columns.get("questions") or []]}}


def today_view(data: dict) -> dict:
    return {"hero": hero(data.get("hero")), "newRecords": [card(m) for m in data.get("newRecords") or []],
            "tasks": [item(i) for i in data.get("tasks") or []], "decisions": [item(i) for i in data.get("decisions") or []],
            "drafts": data.get("drafts", 0), "noProject": data.get("noProject", 0), "reviewCount": data.get("reviewCount", 0)}


def meeting_view(data: dict, transcript: bool) -> dict:
    out = {**card(data), "summary": list(data.get("summary") or []), "topics": list(data.get("topics") or []),
           "decisions": [item(i) for i in data.get("decisions") or []], "tasks": [item(i) for i in data.get("tasks") or []],
           "risks": [text_item(i) for i in data.get("risks") or []], "questions": [text_item(i) for i in data.get("questions") or []],
           "nextSteps": [text_item(i) for i in data.get("nextSteps") or []]}
    if transcript:
        out["transcript"] = [pick(row, ("t", "speaker", "text")) for row in data.get("transcript") or []]
    else:
        out["transcript"] = None
    out["transcriptAllowed"] = transcript
    return out


def person_view(person: dict) -> dict:
    return {**person_ref(person), **pick(person, ("role", "organization", "meetings", "obligations")),
            "projects": [p for p in person.get("projects") or [] if isinstance(p, str)]}


def project_view(project: dict) -> dict:
    return {**project_ref(project), **pick(project, ("status", "goal", "counts")),
            "people": [person_ref(p) for p in project.get("people") or []]}


class LoginBody(BaseModel):
    pin: str = Field("", max_length=32)


# --------------------------------------------------------------------------- приложение

def build_gateway(remote: rm.Remote) -> FastAPI:
    core = remote.core
    app = FastAPI(title="Memory Desk · телефон", docs_url=None, redoc_url=None, openapi_url=None)
    loopback_ok = os.environ.get("MEMORY_DESK_REMOTE_LOOPBACK") == "1"
    denied_at: dict[tuple[str, str], float] = {}

    def refuse(request: Request, ip: str, reason: str, status: int = 403) -> JSONResponse:
        import time
        key, at = (ip, reason), time.monotonic()
        if at - denied_at.get(key, -1e9) > 600:     # одна строка журнала на причину и адрес в 10 минут: журнал не засорить
            denied_at[key] = at
            if len(denied_at) > 200:
                denied_at.clear()
            remote.denied(ip, reason)
        return JSONResponse(REFUSED, status_code=status)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        ip = request.client.host if request.client else ""
        parsed = rm.parse_ip(ip)
        if not (rm.is_lan(ip) or (loopback_ok and parsed is not None and parsed.is_loopback)):
            return refuse(request, ip, "адрес не из локальной сети")
        if any(h in request.headers for h in rm.PROXY_HEADERS):
            return refuse(request, ip, "заголовки прокси")
        if not remote.enabled:
            return JSONResponse(REFUSED, status_code=403)
        if not remote.allowed_host(request.headers.get("host", "")):
            return refuse(request, ip, "чужое имя в Host")
        if request.method not in {"GET", "HEAD"}:
            if request.method != "POST" or request.headers.get(HEADER) != "1" or not rm.origin_ok(request.headers.get("origin", ""), request.headers.get("host", "")):
                return refuse(request, ip, "запрос не со страницы шлюза")
            if int(request.headers.get("content-length") or 0) > 2048:
                return JSONResponse(REFUSED, status_code=413)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = rm.CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else "no-cache"
        return response

    def device_of(request: Request) -> dict:
        ip = request.client.host if request.client else ""
        device = remote.authenticate(request.cookies.get(rm.COOKIE), ip)
        if device is None:
            raise HTTPException(401, "Нужен вход")
        return device

    # --- страница и вход
    def static(name: str, kind: str):
        def handler():
            return Response((rm.WEB_DIR / name).read_bytes(), media_type=kind)
        return handler

    for route, (name, kind) in rm.STATIC.items():
        app.add_api_route(route, static(name, kind), methods=["GET"], include_in_schema=False)

    @app.get("/api/session")
    def session(request: Request):
        ip = request.client.host if request.client else ""
        device = remote.authenticate(request.cookies.get(rm.COOKIE), ip)
        base = {"lang": core.cfg.get("ui_lang", "ru"), "authenticated": device is not None}
        if device is None:
            return {**base, "retryAfter": remote.retry_after()}
        return {**base, "device": {"id": device["id"], "name": device.get("name", ""), "expires": device.get("expires", 0)},
                "transcript": remote.transcript}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request):
        ip = request.client.host if request.client else ""
        try:
            token, device = remote.login(body.pin, ip, request.headers.get("user-agent", ""))
        except rm.RemoteError as exc:
            headers = {"Retry-After": str(exc.retry)} if exc.retry else {}
            return JSONResponse({"detail": str(exc), "retryAfter": exc.retry}, status_code=exc.status, headers=headers)
        response = JSONResponse({"ok": True, "device": {"id": device["id"], "name": device["name"], "expires": device["expires"]}})
        response.set_cookie(rm.COOKIE, token, httponly=True, samesite="strict", secure=remote.secure, path="/",
                            max_age=int(device["expires"] - device["created"]))
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        device = device_of(request)
        remote.logout(device["id"], request.client.host if request.client else "")
        response = JSONResponse({"ok": True})
        response.delete_cookie(rm.COOKIE, path="/")
        return response

    # --- чтение
    @app.get("/api/today")
    def today(request: Request):
        device_of(request)
        return today_view(services.today_payload(core))

    @app.get("/api/records")
    def records(request: Request, q: str = Query("", max_length=200), project: str = Query("", max_length=80),
                limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0)):
        device_of(request)
        data = services.list_meetings(core, q=q, project=project, person="", type_="", status="", period="", archived=False,
                                      limit=limit, offset=offset)
        return {"items": [card(m) for m in data.get("items") or []], "total": data.get("total", 0)}

    @app.get("/api/records/{meeting_id}")
    def record(meeting_id: str, request: Request):
        device = device_of(request)
        meeting = core.by_id.get(meeting_id)
        if not meeting:
            raise HTTPException(404, "Запись не найдена")
        remote.viewed(device["id"], meeting_id, request.client.host if request.client else "")
        return meeting_view(services.meeting_detail(core, meeting), remote.transcript)

    @app.get("/api/tasks")
    def tasks(request: Request, kind: str = Query("tasks", pattern="^(tasks|decisions)$"), limit: int = Query(100, ge=1, le=200),
              offset: int = Query(0, ge=0), stuck: bool = False):
        device_of(request)
        data = services.tasks_registry(core, kind, limit, offset, stuck)
        return {"items": [item(i) for i in data.get("items") or []], "total": data.get("total", 0), "counts": data.get("counts", {})}

    @app.get("/api/people")
    def people(request: Request):
        device_of(request)
        return {"items": [person_view(p) for p in services.people_overview(core)]}

    @app.get("/api/projects")
    def projects(request: Request):
        device_of(request)
        return {"items": [project_view(p) for p in services.projects_overview(core)]}

    return app
