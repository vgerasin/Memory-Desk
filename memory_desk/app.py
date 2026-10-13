"""HTTP-слой Memory Desk: JSON API, SSE-поток обновлений и раздача собранного фронтенда."""

import asyncio
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, answers, apple, bookmarks, config, conflicts, daily, dictionary, housekeeping, integrations, jobs, knowledge, llm, materials, rebuild, series, services, speakers, storage, templates, views, voice, waveform
from .core import Core, get_core

AUDIO_TYPES = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".aac": "audio/aac",
               ".flac": "audio/flac", ".ogg": "audio/ogg", ".mp4": "video/mp4"}
HEADER = "x-memory-desk"


# --------------------------------------------------------------------------- фоновый наблюдатель

class Watcher:
    """Раз в ~1.5 с: очередь обработки → SSE; раз в ~4.5 с: заметки Obsidian; плюс автозапуск и резервные копии."""

    def __init__(self, core: Core):
        self.core = core
        self.queue: dict = {"jobs": [], "running": None, "active": False}
        self.fingerprint = ""
        self.state_version = 0
        self._tick = 0
        self._last_backup_check = 0.0
        self._was_active = False

    def tick(self) -> None:
        core = self.core
        self._tick += 1
        queue = core.queue()
        fp = jobs.queue_fingerprint(queue)
        if fp != self.fingerprint:
            self.fingerprint = fp
            core.jobs_version += 1
        self.queue = queue
        if self._was_active and not queue["active"]:
            before = {m.id for m in core.meetings}
            core.reload(force=True)  # задание завершилось — подхватываем новые заметки сразу
            self._notify_done(before)
        self._was_active = queue["active"]
        if self._tick % 3 == 0:
            core.reload()
        if core.cfg.get("auto_process") and not queue["active"] and self._tick % 4 == 0:
            self._auto_process(queue)
        if core.cfg.get("reminders_enabled") and self._tick % 13 == 0:
            integrations.schedule_sync(core.cfg)
        self.backup_if_due()
        if self._tick % 40 == 0:  # раз в минуту
            self._morning()

    def _morning(self) -> None:
        try:
            daily.send_morning_notification(self.core)
        except Exception:  # noqa: BLE001 — сводка не должна ронять наблюдателя
            pass

    def backup_if_due(self, now: float | None = None) -> bool:
        """Копия базы по расписанию из настроек; после каждой копии лишние старые удаляются."""
        cfg = self.core.cfg
        now = time.time() if now is None else now
        if not cfg.get("backup_auto", True) or now - self._last_backup_check < 600:
            return False
        self._last_backup_check = now
        backups = storage.list_backups()
        interval = max(1, int(cfg.get("backup_interval_hours", 24))) * 3600
        made = False
        if not backups or now - backups[0].stat().st_mtime >= interval:
            try:
                storage.create_backup()
                made = True
            except (OSError, sqlite3.Error):
                pass
        storage.prune_backups(int(cfg.get("backup_keep", 7)))
        return made

    def _notify_done(self, before: set[str]) -> None:
        """Уведомление macOS: «запись готова, проверьте N задач» по записям, появившимся после обработки."""
        if not self.core.cfg.get("notify", True):
            return
        fresh = [m for m in self.core.meetings if m.id not in before]
        if not fresh:
            return
        drafts = storage.one(
            "SELECT COUNT(*) n FROM tasks WHERE status='draft' AND meeting_id IN (%s)" % ",".join("?" * len(fresh)),
            tuple(m.id for m in fresh))
        count = int(drafts["n"]) if drafts else 0
        title = fresh[0].title if len(fresh) == 1 else f"Готово записей: {len(fresh)}"
        message = (f"Запись готова, проверьте {_plural(count, 'задачу', 'задачи', 'задач')}" if count
                   else ("Запись готова" if len(fresh) == 1 else "Записи готовы"))
        apple.notify(title or "Memory Desk", message)

    def _auto_process(self, queue: dict) -> None:
        # Только новые файлы (не упавшие ранее) и не раньше, чем через 10 секунд после последней записи в файл.
        fresh = []
        for item in queue["jobs"]:
            if item["state"] != "queued":
                continue
            path = Path(self.core.cfg["recordings_dir"]).expanduser() / item["file"]
            try:
                if time.time() - path.stat().st_mtime >= 10:
                    fresh.append(path)
            except OSError:
                continue
        if fresh:
            jobs.start(self.core.cfg, fresh)


def _plural(n: int, one: str, few: str, many: str) -> str:
    word = one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many
    return f"{n} {word}"


def _snapshot(core: Core, watcher: Watcher) -> dict:
    running = watcher.queue.get("running")
    reviews = storage.one("SELECT COUNT(*) n FROM meetings WHERE review_status='needs_review' AND archived=0")["n"]
    return {
        "queue": watcher.queue["jobs"], "running": running, "inboxCount": len(watcher.queue["jobs"]) + reviews,
        "dataVersion": core.data_version, "jobsVersion": core.jobs_version, "indexState": core.index.state,
    }


# --------------------------------------------------------------------------- приложение

def create_app() -> FastAPI:
    core = get_core()
    watcher = Watcher(core)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await asyncio.to_thread(core.boot)
        await asyncio.to_thread(watcher.tick)

        async def loop():
            while True:
                await asyncio.sleep(1.5)
                try:
                    await asyncio.to_thread(watcher.tick)
                except Exception:  # noqa: BLE001 — наблюдатель не должен падать от разовой ошибки
                    pass
        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            task.cancel()
            core.shutdown()

    app = FastAPI(title="Memory Desk", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.core = core
    app.state.watcher = watcher

    # --- безопасность: приложение слушает только 127.0.0.1, но браузер любого сайта может обратиться к нему.
    allowed_hosts = {f"127.0.0.1:{config.PORT}", f"localhost:{config.PORT}", "127.0.0.1", "localhost"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if os.environ.get("MEMORY_DESK_ALLOW_ANY_HOST") != "1" and request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"detail": "Недопустимый адрес"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get(HEADER) != "1":
            return JSONResponse({"detail": "Запрос отклонён"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else response.headers.get("Cache-Control", "no-cache")
        return response

    # ------------------------------------------------------------------ служебное
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "indexState": core.index.state}

    @app.get("/api/state")
    def state():
        return _snapshot(core, watcher)

    @app.get("/api/meta")
    def meta():
        projects = [{"id": p["id"], "title": p["title"], "color": services.color_for("project:" + p["id"])} for p in core.projects()]
        people = [services.person_dto(p) for p in storage.rows("SELECT * FROM people ORDER BY name")]
        return {"projects": projects, "types": [{"id": t["id"], "title": t["title"]} for t in core.record_types()],
                "people": people, "calendarConfigured": bool(core.cfg.get("calendar_source") or core.cfg.get("apple_calendar")),
                "calendarWrite": integrations.apple_ready(core.cfg)}

    @app.get("/api/system")
    def system():
        return services.system_status(core)

    @app.post("/api/system/backup")
    def backup():
        path = storage.create_backup()
        return {"path": path.name, "message": "Резервная копия создана"}

    @app.get("/api/system/backups")
    def backups():
        items = []
        for path in storage.list_backups():
            stat = path.stat()
            items.append({"name": path.name, "size": stat.st_size,
                          "createdAt": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
        return {"items": items}

    class RestoreBody(BaseModel):
        name: str

    @app.post("/api/system/restore")
    def restore_backup(body: RestoreBody):
        if jobs.is_running():
            raise HTTPException(409, "Идёт обработка записей: дождитесь её окончания")
        path = storage.backup_dir() / Path(body.name).name
        try:
            storage.restore_backup(path)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"Не удалось восстановить копию: {exc}") from exc
        core.reload(force=True)
        core.data_version += 1
        return {"message": "База восстановлена из копии. Перед этим сохранена копия текущего состояния"}

    # ------------------------------------------------------------------ Календарь и Напоминания macOS
    @app.get("/api/integrations/apple")
    def apple_status():
        return integrations.status_payload(core.cfg)

    @app.post("/api/integrations/apple/access")
    def apple_access():
        be = apple.backend()
        if not be.status()["available"]:
            raise HTTPException(409, be.status()["reason"])
        be.request_access()
        return integrations.status_payload(core.cfg)

    @app.post("/api/integrations/reminders/sync")
    def reminders_sync():
        if not core.cfg.get("reminders_enabled"):
            raise HTTPException(409, "Синхронизация с Напоминаниями выключена в настройках")
        try:
            summary = integrations.run_sync(core.cfg)
        except apple.AppleError as exc:
            raise HTTPException(409, str(exc)) from exc
        core.data_version += 1
        return {"summary": summary, **integrations.status_payload(core.cfg)}

    class EventBody(BaseModel):
        title: str
        start: str
        end: str | None = None
        allDay: bool = False
        meetingId: str | None = None
        notes: str = ""

    @app.post("/api/calendar/events")
    def calendar_event(body: EventBody):
        title = " ".join(body.title.split())
        if not title:
            raise HTTPException(400, "Название не может быть пустым")
        try:
            start = datetime.fromisoformat(body.start)
            end = datetime.fromisoformat(body.end) if body.end else None
        except ValueError as exc:
            raise HTTPException(400, "Неверная дата или время") from exc
        url, notes = "", body.notes.strip()
        if body.meetingId and body.meetingId in core.by_id:
            url = f"http://127.0.0.1:{config.PORT}/records/{body.meetingId}"
            notes = (notes + "\n" if notes else "") + f"Из записи «{core.by_id[body.meetingId].title}»"
        try:
            event_id = integrations.create_event(core.cfg, title=title, start=start, end=end, all_day=body.allDay, notes=notes, url=url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except apple.AppleError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": event_id, "message": "Событие добавлено в Календарь"}

    @app.get("/api/storage")
    def storage_usage():
        return housekeeping.usage(core.cfg)

    class CleanBody(BaseModel):
        category: str
        days: int | None = None

    @app.post("/api/storage/clean")
    def storage_clean(body: CleanBody):
        if body.category.startswith("work") and jobs.is_running():
            raise HTTPException(409, "Идёт обработка записей: дождитесь её окончания")
        if body.days is not None and body.days < 0:
            raise HTTPException(400, "Число дней не может быть отрицательным")
        try:
            result = housekeeping.clean(core.cfg, body.category, body.days)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        core.data_version += 1
        return {**result, "usage": housekeeping.usage(core.cfg)}

    def reindex_view() -> dict:
        status = dict(core.index.reindex)
        end = status["finishedAt"] or time.time()
        status["seconds"] = round(end - status["startedAt"], 1) if status["startedAt"] else 0
        return status

    @app.post("/api/system/reindex")
    def reindex():
        if not core.index.encoder:
            raise HTTPException(409, "Поисковая модель ещё загружается: повторите через минуту")
        started = core.start_reindex()
        return {"started": started, "message": "Переиндексация запущена" if started else "Переиндексация уже идёт", **reindex_view()}

    @app.get("/api/system/reindex")
    def reindex_status():
        return reindex_view()

    class SettingsBody(BaseModel):
        recordings_dir: str
        obsidian_dir: str
        archive_dir: str
        retention_days: int = 14
        auto_process: bool = False
        diarize: bool = True
        voice_enabled: bool = False
        calendar_source: str = ""
        apple_calendar: bool = False
        reminders_enabled: bool = False
        reminders_list: str = "Memory Desk"
        reminders_scope: str = "mine"
        my_name: str = ""
        notify: bool = True
        morning_brief: bool = True
        morning_hour: int = 8
        backup_auto: bool = True
        backup_interval_hours: int = 24
        backup_keep: int = 7
        default_template: str = "meeting"

    @app.get("/api/settings")
    def get_settings():
        cfg = core.cfg
        return {**cfg, "exists": {k: Path(cfg[k]).expanduser().exists() for k in ("recordings_dir", "obsidian_dir", "archive_dir")}}

    @app.put("/api/settings")
    def put_settings(body: SettingsBody):
        for key, label in (("recordings_dir", "записей"), ("obsidian_dir", "заметок Obsidian")):
            value = getattr(body, key).strip()
            if value and value != str(core.cfg.get(key, "")) and not Path(value).expanduser().is_dir():
                raise HTTPException(400, f"Папка {label} не найдена: {value}")
        if not 1 <= body.retention_days <= 3650:
            raise HTTPException(400, "Срок хранения записей — от 1 до 3650 дней")
        if body.default_template not in templates.load_all()[0]:
            raise HTTPException(400, "Такого шаблона заметки нет")
        try:
            saved = core.apply_settings(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {**saved, "message": "Настройки сохранены"}

    # ------------------------------------------------------------------ словарь терминов
    def dictionary_view(warnings: list[str] | None = None) -> dict:
        text = dictionary.read_text()
        entries, found = dictionary.parse(text)
        return {"text": text, "count": len(entries), "warnings": warnings if warnings is not None else found,
                "path": str(dictionary.path())}

    @app.get("/api/dictionary")
    def get_dictionary():
        return dictionary_view()

    @app.put("/api/dictionary")
    def put_dictionary(body: dict = Body(...)):
        text = body.get("text")
        if not isinstance(text, str):
            raise HTTPException(400, "Нужен текст словаря")
        try:
            entries, warnings = dictionary.save_text(text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"Не удалось сохранить словарь: {exc}") from exc
        return {**dictionary_view(warnings), "message": f"Словарь сохранён: слов {len(entries)}"}

    @app.post("/api/dictionary/apply")
    def apply_dictionary():
        return services.apply_dictionary(core)

    # ------------------------------------------------------------------ шаблоны заметок
    def templates_view() -> dict:
        found, problems = templates.load_all()
        return {"items": [templates.public(t) for t in templates.listing()], "problems": problems,
                "default": core.cfg.get("default_template") if core.cfg.get("default_template") in found else templates.DEFAULT_ID,
                "dir": str(templates.user_dir())}

    @app.get("/api/templates")
    def get_templates():
        return templates_view()

    @app.get("/api/templates/{template_id}")
    def get_template(template_id: str):
        found, _ = templates.load_all()
        template = found.get(template_id)
        if not template:
            raise HTTPException(404, "Шаблон не найден")
        text = Path(template.path).read_text(encoding="utf-8") if template.path else templates.dump_yaml(template)
        return {**templates.public(template), "text": text}

    @app.put("/api/templates/{template_id}")
    def put_template(template_id: str, body: dict = Body(...)):
        text = body.get("text")
        if not isinstance(text, str) or len(text) > 50_000:
            raise HTTPException(400, "Нужен текст шаблона")
        try:
            saved = templates.save_user(template_id, text)
        except templates.TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"Не удалось сохранить шаблон: {exc}") from exc
        return {**templates_view(), "saved": templates.public(saved), "message": f"Шаблон «{saved.name}» сохранён"}

    @app.delete("/api/templates/{template_id}")
    def delete_template(template_id: str):
        found, _ = templates.load_all()
        if template_id not in found:
            raise HTTPException(404, "Шаблон не найден")
        if not templates.delete_user(template_id):
            raise HTTPException(400, "Поставляемый шаблон удалить нельзя: можно сохранить свой с тем же именем или выбрать другой")
        if core.cfg.get("default_template") == template_id and template_id not in templates.load_all()[0]:
            core.apply_settings({**core.cfg, "default_template": templates.DEFAULT_ID})
        return {**templates_view(), "message": "Шаблон удалён"}

    @app.post("/api/templates/draft")
    def draft_template(body: dict = Body(...)):
        """Шаблон по описанию: модель предлагает структуру, пользователь смотрит и правит её до сохранения."""
        def ask(prompt: str) -> dict:
            return llm.ollama_json(prompt, model=config.OLLAMA_MODEL, url=config.OLLAMA_URL, num_predict=1200,
                                   attempts=2, timeout=180)
        try:
            template = templates.draft_from_model(str(body.get("description") or ""), ask)
        except templates.TemplateError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {**templates.public(template), "text": templates.dump_yaml(template)}

    class FileTemplateBody(BaseModel):
        file: str
        templateId: str = ""

    @app.post("/api/jobs/template")
    def set_file_template(body: FileTemplateBody):
        recording_path(body.file)
        if body.templateId and body.templateId not in templates.load_all()[0]:
            raise HTTPException(404, "Шаблон не найден")
        jobs.set_file_template(body.file, body.templateId)
        watcher.tick()
        return {"file": body.file, "templateId": jobs.template_for(body.file, core.cfg)}

    class RebuildBody(BaseModel):
        templateId: str

    @app.post("/api/meetings/{meeting_id}/rebuild")
    def rebuild_note(meeting_id: str, body: RebuildBody):
        try:
            return rebuild.start(core, meeting_id, body.templateId)
        except rebuild.RebuildError as exc:
            raise HTTPException(409 if "уже" in str(exc) else 404 if "не найден" in str(exc) else 400, str(exc)) from exc

    @app.get("/api/meetings/{meeting_id}/rebuild")
    def rebuild_status(meeting_id: str):
        return rebuild.status(meeting_id)

    # ------------------------------------------------------------------ закладки
    class BookmarkBody(BaseModel):
        sec: int = Field(..., ge=0)
        text: str = Field("", max_length=1000)
        color: str = "yellow"

    class BookmarkPatch(BaseModel):
        text: str | None = Field(None, max_length=1000)
        color: str | None = None

    def bookmark_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except bookmarks.BookmarkError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"Не удалось записать заметку: {exc}") from exc

    @app.get("/api/meetings/{meeting_id}/bookmarks")
    def list_bookmarks(meeting_id: str):
        meeting = get_meeting(meeting_id)
        return {"items": services.bookmark_dtos(meeting, knowledge.transcript_segments(meeting))}

    @app.post("/api/meetings/{meeting_id}/bookmarks")
    def add_bookmark(meeting_id: str, body: BookmarkBody):
        return bookmark_call(bookmarks.add, core, meeting_id, body.sec, body.text, body.color)

    @app.patch("/api/meetings/{meeting_id}/bookmarks/{sec}")
    def update_bookmark(meeting_id: str, sec: int, body: BookmarkPatch):
        return bookmark_call(bookmarks.update, core, meeting_id, sec, body.text, body.color)

    @app.delete("/api/meetings/{meeting_id}/bookmarks/{sec}")
    def delete_bookmark(meeting_id: str, sec: int):
        if not bookmark_call(bookmarks.remove, core, meeting_id, sec):
            raise HTTPException(404, "Такой закладки нет")
        return {"ok": True}

    # ------------------------------------------------------------------ взгляды на запись
    class ViewBody(BaseModel):
        presetId: str = ""
        title: str = ""
        instruction: str = ""
        limit: int | None = None

    def view_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except views.ViewError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.get("/api/views/presets")
    def view_presets():
        return {"presets": views.public_presets(), "max": views.MAX_VIEWS}

    @app.post("/api/meetings/{meeting_id}/views")
    def add_view(meeting_id: str, body: ViewBody):
        return view_call(views.start, core, meeting_id, body.model_dump())

    @app.post("/api/meetings/{meeting_id}/views/{view_id}/refresh")
    def refresh_view(meeting_id: str, view_id: str, force: bool = False):
        return view_call(views.start, core, meeting_id, {"viewId": view_id}, refresh=True, force=force)

    @app.delete("/api/meetings/{meeting_id}/views/{view_id}")
    def delete_view(meeting_id: str, view_id: str):
        if not view_call(views.delete, core, meeting_id, view_id):
            raise HTTPException(404, "Такого взгляда в записи нет")
        return {"ok": True}

    @app.get("/api/meetings/{meeting_id}/views/status")
    def view_status(meeting_id: str):
        return views.status(meeting_id)

    # ------------------------------------------------------------------ SSE
    @app.get("/api/stream")
    async def stream(request: Request):
        async def gen():
            last = None
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                snap = _snapshot(core, watcher)
                running = snap["running"]
                key = (snap["dataVersion"], snap["jobsVersion"], snap["indexState"], running["percent"] if running else None)
                if key != last:
                    last = key
                    yield f"event: state\ndata: {json.dumps(snap, ensure_ascii=False, default=str)}\n\n"
                else:
                    yield ": ping\n\n"
                await asyncio.sleep(1.5)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ записи
    @app.get("/api/today")
    def today():
        return services.today_payload(core)

    @app.get("/api/morning")
    def morning():
        return daily.morning_payload(core)

    @app.get("/api/meetings")
    def meetings(q: str = "", project: str = "", person: str = "", type: str = "", status: str = "",
                 period: str = "", archived: bool = False, limit: int = Query(0, ge=0, le=1000), offset: int = Query(0, ge=0)):
        return services.list_meetings(core, q=q, project=project, person=person, type_=type,
                                      status=status, period=period, archived=archived, limit=limit, offset=offset)

    def get_meeting(meeting_id: str):
        meeting = core.by_id.get(meeting_id)
        if not meeting:
            raise HTTPException(404, "Запись не найдена")
        return meeting

    @app.get("/api/series")
    def series_list():
        return {"items": series.listing(core.live_meetings())}

    @app.get("/api/meetings/{meeting_id}/series")
    def meeting_series(meeting_id: str):
        if meeting_id not in core.by_id:
            raise HTTPException(404, "Запись не найдена")
        return {"series": series.for_meeting(core.live_meetings(), meeting_id)}

    @app.get("/api/meetings/{meeting_id}")
    def meeting(meeting_id: str):
        return services.meeting_detail(core, get_meeting(meeting_id))

    class MeetingPatch(BaseModel):
        project_id: str | None = None
        record_type_id: str | None = None
        people: list[str] | None = None
        important: bool | None = None
        title: str | None = None
        summary: str | None = None

    @app.patch("/api/meetings/{meeting_id}")
    def patch_meeting(meeting_id: str, body: MeetingPatch):
        m = get_meeting(meeting_id)
        try:
            if body.project_id is not None:
                project = next((p for p in core.projects() if p["id"] == body.project_id), None)
                if body.project_id and not project:
                    raise HTTPException(404, "Проект не найден")
                knowledge.assign_project(m, project)
            if body.record_type_id is not None:
                record_type = next((t for t in core.record_types() if t["id"] == body.record_type_id), None)
                if not record_type:
                    raise HTTPException(404, "Тип записи не найден")
                knowledge.assign_record_type(m, record_type)
            if body.title is not None or body.summary is not None:
                title = " ".join((body.title or "").split())
                if len(title) > 200:
                    raise HTTPException(400, "Название слишком длинное: не больше 200 знаков")
                if len(body.summary or "") > 20000:
                    raise HTTPException(400, "Краткое содержание слишком длинное")
                knowledge.update_meeting_text(m, title, body.summary or "")
        except (OSError, ValueError) as exc:
            raise HTTPException(400, f"Не удалось изменить заметку: {exc}") from exc
        if body.people is not None:
            storage.set_meeting_people(meeting_id, body.people)
        if body.important is not None:
            storage.update_item("meetings", meeting_id, important=1 if body.important else 0)
        core.reload(force=True)
        return services.meeting_detail(core, get_meeting(meeting_id))

    @app.post("/api/meetings/{meeting_id}/publish")
    def publish(meeting_id: str):
        m = get_meeting(meeting_id)
        if not m.project_id or m.source_type in {"", services.NO_TYPE}:
            raise HTTPException(409, "Сначала выберите проект и тип записи")
        storage.update_item("meetings", meeting_id, review_status="reviewed")
        core.data_version += 1
        return services.meeting_detail(core, m)

    @app.post("/api/meetings/{meeting_id}/archive")
    def archive(meeting_id: str):
        get_meeting(meeting_id)
        storage.update_item("meetings", meeting_id, archived=1)
        core.data_version += 1
        return {"ok": True}

    @app.post("/api/meetings/{meeting_id}/restore")
    def restore(meeting_id: str):
        get_meeting(meeting_id)
        storage.update_item("meetings", meeting_id, archived=0)
        core.data_version += 1
        return {"ok": True}

    # ------------------------------------------------------------------ материалы по запросу
    class MaterialScope(BaseModel):
        meetingId: str | None = None
        projectId: str | None = None
        personId: str | None = None
        days: int | None = Field(None, ge=1, le=3650)
        since: str | None = Field(None, max_length=10)
        until: str | None = Field(None, max_length=10)
        save: bool = False

    def material_errors(call):
        try:
            return call()
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ImportError as exc:
            raise HTTPException(500, "Не установлен модуль python-pptx: запустите install.command или start.command") from exc
        except OSError as exc:
            raise HTTPException(500, f"Не удалось сохранить файл: {exc}") from exc

    @app.post("/api/meetings/{meeting_id}/protocol")
    def meeting_protocol(meeting_id: str, save: bool = False):
        get_meeting(meeting_id)

        def run():
            result = materials.protocol(core, meeting_id, services.meeting_date)
            if save:
                path = materials.save_protocol(result)
                result["savedAs"] = knowledge.note_relative_path(path)
                result["obsidianUrl"] = knowledge.obsidian_url(path)
            return result
        return material_errors(run)

    @app.post("/api/meetings/{meeting_id}/protocol/start")
    def protocol_start(meeting_id: str):
        """Составление протокола в фоне с ходом работы: интерфейс опрашивает `GET …/protocol/progress`."""
        get_meeting(meeting_id)
        return material_errors(lambda: materials.start_protocol(core, meeting_id, services.meeting_date))

    @app.get("/api/meetings/{meeting_id}/protocol/progress")
    def protocol_progress(meeting_id: str):
        return materials.protocol_progress(meeting_id)

    @app.post("/api/materials/mindmap")
    def material_mindmap(body: MaterialScope):
        def run():
            result = materials.mindmap(core, body.model_dump(), services.meeting_date)
            if body.save:
                path = materials.save_text("map", "Карта — " + result["title"], materials.mindmap_note(result))
                result["savedAs"] = knowledge.note_relative_path(path)
                result["obsidianUrl"] = knowledge.obsidian_url(path)
            return result
        return material_errors(run)

    class MaterialSave(BaseModel):
        kind: str
        name: str = Field(..., min_length=1, max_length=200)
        content: str = Field(..., max_length=500_000)

    @app.post("/api/materials/save")
    def material_save(body: MaterialSave):
        """Сохранение показанного в окне текста (с правками пользователя) в Obsidian: Протоколы/ или Карты/."""
        def run():
            path = materials.save_text(body.kind, body.name, body.content)
            return {"savedAs": knowledge.note_relative_path(path), "obsidianUrl": knowledge.obsidian_url(path)}
        return material_errors(run)

    @app.post("/api/materials/deck")
    def material_deck(body: MaterialScope):
        result = material_errors(lambda: materials.build_deck(core, body.model_dump(), services.meeting_date))
        return result

    @app.get("/api/materials/decks")
    def material_decks():
        return {"items": [{**d, "url": materials.deck_url(d["name"])} for d in materials.list_decks()]}

    @app.get("/api/materials/deck/{name}")
    def material_deck_file(name: str):
        try:
            path = materials.deck_path(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "Презентация не найдена") from exc
        return FileResponse(path, filename=path.name,
                            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

    @app.get("/api/calendar")
    def calendar_range(start: str = Query("", alias="from", max_length=10), to: str = Query("", max_length=10)):
        today = date.today()
        try:
            first = date.fromisoformat(start) if start else today - timedelta(days=today.weekday())
            last = date.fromisoformat(to) if to else first + timedelta(days=6)
        except ValueError:
            raise HTTPException(400, "Даты должны быть в формате ГГГГ-ММ-ДД") from None
        try:
            return integrations.calendar_range(core, first, last)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    class CalendarAction(BaseModel):
        action: str
        useTitle: bool = False
        useProject: bool = True

    @app.get("/api/meetings/{meeting_id}/calendar")
    def meeting_calendar(meeting_id: str):
        return {"suggestion": integrations.calendar_suggestion(core, get_meeting(meeting_id))}

    @app.post("/api/meetings/{meeting_id}/calendar")
    def meeting_calendar_action(meeting_id: str, body: CalendarAction):
        m = get_meeting(meeting_id)
        if body.action == "dismiss":
            integrations.dismiss_suggestion(meeting_id)
            return {"suggestion": None}
        if body.action != "apply":
            raise HTTPException(400, "Неизвестное действие")
        try:
            integrations.apply_suggestion(core, m, use_title=body.useTitle, use_project=body.useProject)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, f"Не удалось изменить заметку: {exc}") from exc
        return {"suggestion": integrations.calendar_suggestion(core, get_meeting(meeting_id)),
                "meeting": services.meeting_detail(core, get_meeting(meeting_id))}

    class SpeakerChange(BaseModel):
        i: int
        speaker: str = ""

    class SpeakersBody(BaseModel):
        rename: dict[str, str] = {}
        segments: list[SpeakerChange] = []

    @app.put("/api/meetings/{meeting_id}/speakers")
    def put_speakers(meeting_id: str, body: SpeakersBody):
        m = get_meeting(meeting_id)
        if speakers.status(meeting_id) and speakers.status(meeting_id)["state"] == "running":
            raise HTTPException(409, "Определение говорящих ещё идёт")
        rename = {}
        for old, new in body.rename.items():
            old = speakers.clean_label(old)
            if old:
                rename[old] = speakers.canonical_name(speakers.clean_label(new)) if speakers.clean_label(new) else ""
        changes = {c.i: (speakers.canonical_name(speakers.clean_label(c.speaker)) if speakers.clean_label(c.speaker) else "")
                   for c in body.segments}
        if not rename and not changes:
            raise HTTPException(400, "Нечего менять")
        try:
            labels = knowledge.change_speakers(m, rename, changes)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, f"Не удалось изменить стенограмму: {exc}") from exc
        for old, new in rename.items():
            voice.rename_label(meeting_id, old, new)
        speakers.link_people(meeting_id, labels)
        core.reload(force=True)
        return services.meeting_detail(core, get_meeting(meeting_id))

    @app.post("/api/meetings/{meeting_id}/diarize")
    def diarize_meeting(meeting_id: str, mode: str = Query("text", pattern="^(text|voice)$")):
        m = get_meeting(meeting_id)
        if mode == "voice":
            ok, reason = voice.available()
            if not ok:
                raise HTTPException(409, reason)
            if not knowledge.meeting_audio(m):
                raise HTTPException(409, "Аудиофайл не найден: возможно, он удалён по сроку хранения")
        if not speakers.start(core, m, mode):
            raise HTTPException(409, "Определение говорящих уже идёт")
        return speakers.status(meeting_id)

    @app.get("/api/meetings/{meeting_id}/diarize")
    def diarize_status(meeting_id: str):
        get_meeting(meeting_id)
        return speakers.status(meeting_id) or {"state": "idle"}

    @app.get("/api/meetings/{meeting_id}/audio")
    def audio(meeting_id: str):
        path = knowledge.meeting_audio(get_meeting(meeting_id))
        if not path:
            raise HTTPException(404, "Аудиофайл не найден: возможно, он удалён по сроку хранения")
        return FileResponse(path, media_type=AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream"))

    @app.get("/api/meetings/{meeting_id}/waveform")
    def wave(meeting_id: str):
        path = knowledge.meeting_audio(get_meeting(meeting_id))
        if not path:
            raise HTTPException(404, "Аудиофайл не найден")
        return {"bars": waveform.cached(meeting_id, path)}

    # ------------------------------------------------------------------ справочники
    class ProjectBody(BaseModel):
        title: str

    @app.post("/api/projects")
    def create_project(body: ProjectBody):
        try:
            project = knowledge.create_project(body.title)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        storage.sync_projects(core.projects())
        core.data_version += 1
        return {"id": project["id"], "title": project["title"], "color": services.color_for("project:" + project["id"]),
                "created": project["created"]}

    @app.get("/api/prep")
    def prep(project: str | None = None, meeting: str | None = None):
        try:
            return services.prep_brief(core, project, meeting)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc

    @app.get("/api/prep/headline")
    def prep_headline(project: str | None = None, meeting: str | None = None):
        try:
            return services.prep_headline(core, project, meeting)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc

    @app.get("/api/analytics")
    def analytics(days: int = Query(90, ge=0, le=3650)):
        return services.analytics(core, days)

    class ConflictPair(BaseModel):
        old: str
        new: str

    @app.get("/api/decisions/conflicts")
    def decision_conflicts():
        items = conflicts.candidates(services._decision_dto)
        return {"items": items, "total": len(items)}

    def conflict_pair(body: ConflictPair) -> tuple[dict, dict]:
        pair = storage.rows("SELECT d.*, m.title mt, m.meeting_date md FROM decisions d JOIN meetings m ON m.id=d.meeting_id WHERE d.id IN (?,?)",
                            (body.old, body.new))
        info = {r["id"]: services._decision_dto(r, r["mt"], r["md"]) for r in pair}
        if body.old not in info or body.new not in info or body.old == body.new:
            raise HTTPException(404, "Решение не найдено")
        return {**info[body.old], "date": info[body.old]["meetingDate"]}, {**info[body.new], "date": info[body.new]["meetingDate"]}

    @app.post("/api/decisions/conflicts/check")
    def check_conflict(body: ConflictPair):
        old, new = conflict_pair(body)
        try:
            return conflicts.ask_model(old, new)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/decisions/conflicts/dismiss")
    def dismiss_conflict(body: ConflictPair):
        conflict_pair(body)
        conflicts.dismiss(body.old, body.new)
        return {"dismissed": True}

    @app.get("/api/decisions/tree")
    def decisions_tree(project: str = Query("", max_length=80), days: int = Query(0, ge=0, le=3650)):
        return services.decision_tree(core, project, days)

    @app.get("/api/projects")
    def projects():
        return {"items": services.projects_overview(core)}

    class PersonBody(BaseModel):
        name: str
        role: str = ""

    @app.get("/api/people")
    def people():
        return {"items": services.people_overview(core)}

    @app.post("/api/people")
    def create_person(body: PersonBody):
        try:
            person = storage.create_person(body.name, body.role)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        core.data_version += 1
        return services.person_dto(person)

    # ------------------------------------------------------------------ голоса
    def voice_state():
        ok, reason = voice.available()
        deps_ok, deps_reason = voice.dependencies()
        return {"enabled": bool(core.cfg.get("voice_enabled")), "available": ok, "reason": reason,
                "dependencies": deps_ok, "dependenciesReason": deps_reason, "modelReady": voice.model_ready(),
                "download": voice.download_status(), "profiles": len(voice.all_profile_stats())}

    @app.get("/api/voice")
    def voice_info():
        return voice_state()

    @app.post("/api/voice/model")
    def voice_model():
        started = voice.start_download()
        return {**voice_state(), "started": started}

    class VoiceSample(BaseModel):
        meetingId: str
        label: str
        consent: bool = False

    @app.post("/api/people/{person_id}/voice")
    def add_person_voice(person_id: str, body: VoiceSample):
        if not body.consent:
            raise HTTPException(400, "Голосовой отпечаток — биометрические данные. Подтвердите согласие человека на его хранение")
        get_meeting(body.meetingId)
        try:
            stats = voice.add_sample(person_id, body.meetingId, speakers.clean_label(body.label))
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        core.data_version += 1
        return {"voice": stats}

    @app.delete("/api/people/{person_id}/voice")
    def delete_person_voice(person_id: str):
        if not voice.remove_profile(person_id):
            raise HTTPException(404, "У этого человека нет голосового отпечатка")
        core.data_version += 1
        return {"ok": True}

    @app.patch("/api/people/{person_id}")
    def patch_person(person_id: str, body: dict = Body(...)):
        fields = {k: " ".join(str(v).split())[:200] for k, v in body.items() if k in {"role"}}
        if not fields or not storage.update_item("people", person_id, **fields):
            raise HTTPException(404, "Человек не найден")
        core.data_version += 1
        return {"ok": True}

    # ------------------------------------------------------------------ задачи и решения
    @app.get("/api/tasks")
    def tasks(kind: str = Query("tasks", pattern="^(tasks|decisions)$"), limit: int = Query(0, ge=0, le=1000), offset: int = Query(0, ge=0),
              stuck: bool = False):
        return services.tasks_registry(core, kind, limit, offset, stuck)

    class ItemPatch(BaseModel):
        status: str | None = None
        text: str | None = None
        owner: str | None = None
        deadline: str | None = None
        replaced_by: str | None = None   # только решения: id действующего решения; "" снимает отметку «заменено»

    def check_replacement(item_id: str, target_id: str) -> str | None:
        """Решение можно заменить другим из того же проекта; цепочка не должна замыкаться."""
        if not target_id:
            return None
        if target_id == item_id:
            raise HTTPException(400, "Решение нельзя заменить самим собой")
        pair = storage.rows("SELECT d.id, d.replaced_by, d.status, m.project_id FROM decisions d JOIN meetings m ON m.id=d.meeting_id WHERE d.id IN (?,?)", (item_id, target_id))
        info = {r["id"]: r for r in pair}
        if target_id not in info:
            raise HTTPException(400, "Решение, которым заменяем, не найдено")
        if (info[item_id]["project_id"] or "") != (info[target_id]["project_id"] or ""):
            raise HTTPException(400, "Заменить можно только решением из того же проекта")
        if info[target_id]["status"] == "rejected":
            raise HTTPException(400, "Отклонённое решение не может заменять другое")
        seen, cursor = {target_id}, info[target_id]["replaced_by"]
        while cursor:
            if cursor == item_id:
                raise HTTPException(400, "Так цепочка замкнётся: это решение уже заменяет выбранное")
            if cursor in seen:
                break
            seen.add(cursor)
            row = storage.one("SELECT replaced_by FROM decisions WHERE id=?", (cursor,))
            cursor = row["replaced_by"] if row else None
        return target_id

    def patch_item(table: str, item_id: str, body: ItemPatch, allowed_status: set[str], quiet: bool = False):
        fields = {k: v for k, v in body.model_dump().items()
                  if v is not None and (table == "tasks" or k != "deadline") and (table == "decisions" or k != "replaced_by")}
        if "status" in fields and fields["status"] not in allowed_status:
            raise HTTPException(400, "Недопустимый статус")
        if "text" in fields and not fields["text"].strip():
            raise HTTPException(400, "Текст не может быть пустым")
        if "text" in fields and len(fields["text"]) > 1000:
            raise HTTPException(400, "Текст слишком длинный: не больше 1000 знаков")
        if "owner" in fields:
            fields["owner"] = " ".join(fields["owner"].split())
            if len(fields["owner"]) > 120:
                raise HTTPException(400, "Имя ответственного слишком длинное")
        if "deadline" in fields:
            raw = fields["deadline"].strip()
            if raw.casefold() in {"", "не указан", "не указано"}:
                fields["deadline"] = "не указан"
            else:
                try:
                    fields["deadline"] = date.fromisoformat(raw).isoformat()
                except ValueError:
                    raise HTTPException(400, "Срок должен быть датой в формате ГГГГ-ММ-ДД") from None
        if not fields:
            raise HTTPException(400, "Нечего менять")
        if "text" in fields:
            fields["text"] = " ".join(fields["text"].split())
        before = storage.one(f"SELECT * FROM {table} WHERE id=?", (item_id,))
        if not before:
            raise HTTPException(404, "Элемент не найден")
        if "replaced_by" in fields:
            fields["replaced_by"] = check_replacement(item_id, fields["replaced_by"].strip())
        try:
            found = storage.update_item(table, item_id, **fields)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "В этой записи уже есть такая формулировка") from exc
        if not found:
            raise HTTPException(404, "Элемент не найден")
        core.data_version += 1
        if table == "tasks" and not quiet:
            integrations.schedule_sync(core.cfg)
        row = storage.one(f"SELECT x.*, m.title mt, m.meeting_date md, m.path mpath FROM {table} x JOIN meetings m ON m.id=x.meeting_id WHERE x.id=?", (item_id,))
        note_state = "missing"
        try:  # правка попадает и в строку заметки Obsidian; сбой записи не отменяет правку в кабинете
            note_state = knowledge.update_item_line(Path(row["mpath"]), table, before, row)
        except OSError:
            pass
        if note_state == "updated" and not quiet:
            core.reload(force=True)
        dto = (services._task_dto if table == "tasks" else services._decision_dto)(row, row["mt"], row["md"])
        return {**dto, "noteSynced": note_state != "missing", "_noteUpdated": note_state == "updated"}

    TASK_STATUSES = {"draft", "open", "done", "rejected"}
    DECISION_STATUSES = {"draft", "confirmed", "rejected"}
    BULK_LIMIT = 500

    @app.patch("/api/tasks/{item_id}")
    def patch_task(item_id: str, body: ItemPatch):
        result = patch_item("tasks", item_id, body, TASK_STATUSES)
        result.pop("_noteUpdated", None)
        return result

    @app.patch("/api/decisions/{item_id}")
    def patch_decision(item_id: str, body: ItemPatch):
        result = patch_item("decisions", item_id, body, DECISION_STATUSES)
        result.pop("_noteUpdated", None)
        return result

    class BulkItems(BaseModel):
        ids: list[str]
        status: str | None = None
        owner: str | None = None
        deadline: str | None = None

    def bulk_ids(ids: list[str]) -> list[str]:
        unique = list(dict.fromkeys(i for i in ids if i))
        if not unique:
            raise HTTPException(400, "Не выбрано ни одного элемента")
        if len(unique) > BULK_LIMIT:
            raise HTTPException(400, f"За один раз можно изменить не больше {BULK_LIMIT} элементов")
        return unique

    def bulk_items(table: str, body: BulkItems, allowed: set[str]):
        ids = bulk_ids(body.ids)
        patch = ItemPatch(status=body.status, owner=body.owner, deadline=body.deadline)
        updated, missing_note, failed, reload_needed = 0, 0, [], False
        for item_id in ids:
            try:
                result = patch_item(table, item_id, patch, allowed, quiet=True)
            except HTTPException as exc:
                if exc.status_code == 400 and not updated and not failed:
                    raise  # неверное значение одинаково для всех элементов — сообщаем сразу
                failed.append({"id": item_id, "error": str(exc.detail)})
                continue
            updated += 1
            missing_note += 0 if result["noteSynced"] else 1
            reload_needed = reload_needed or result["_noteUpdated"]
        if reload_needed:
            core.reload(force=True)
        if table == "tasks" and updated:
            integrations.schedule_sync(core.cfg)
        return {"updated": updated, "noteMissing": missing_note, "failed": failed[:20], "failedCount": len(failed)}

    @app.post("/api/tasks/bulk")
    def bulk_tasks(body: BulkItems):
        return bulk_items("tasks", body, TASK_STATUSES)

    @app.post("/api/decisions/bulk")
    def bulk_decisions(body: BulkItems):
        return bulk_items("decisions", body.model_copy(update={"owner": None, "deadline": None}), DECISION_STATUSES)

    class BulkMeetings(BaseModel):
        ids: list[str]
        action: str = Field(pattern="^(archive|restore|publish|project|type)$")
        value: str | None = None

    @app.post("/api/meetings/bulk")
    def bulk_meetings(body: BulkMeetings):
        ids = bulk_ids(body.ids)
        project = record_type = None
        if body.action == "project" and body.value:
            project = next((p for p in core.projects() if p["id"] == body.value), None)
            if not project:
                raise HTTPException(404, "Проект не найден")
        if body.action == "type":
            record_type = next((t for t in core.record_types() if t["id"] == body.value), None)
            if not record_type:
                raise HTTPException(404, "Тип записи не найден")
        done, failed = 0, []
        for meeting_id in ids:
            m = core.by_id.get(meeting_id)
            if not m:
                failed.append({"id": meeting_id, "error": "Запись не найдена"})
                continue
            try:
                if body.action == "archive":
                    storage.update_item("meetings", meeting_id, archived=1)
                elif body.action == "restore":
                    storage.update_item("meetings", meeting_id, archived=0)
                elif body.action == "publish":
                    if not m.project_id or m.source_type in {"", services.NO_TYPE}:
                        failed.append({"id": meeting_id, "error": "Нужны проект и тип записи"})
                        continue
                    storage.update_item("meetings", meeting_id, review_status="reviewed")
                elif body.action == "project":
                    knowledge.assign_project(m, project)
                else:
                    knowledge.assign_record_type(m, record_type)
            except (OSError, ValueError) as exc:
                failed.append({"id": meeting_id, "error": f"Не удалось изменить заметку: {exc}"})
                continue
            done += 1
        if body.action in {"project", "type"} and done:
            core.reload(force=True)
        core.data_version += 1
        return {"updated": done, "failed": failed[:20], "failedCount": len(failed)}

    # ------------------------------------------------------------------ очередь обработки
    @app.get("/api/jobs")
    def get_jobs():
        return {"jobs": watcher.queue["jobs"], "running": watcher.queue["running"], "active": watcher.queue["active"]}

    @app.post("/api/jobs/scan")
    def scan():
        result = jobs.start(core.cfg)
        watcher.tick()
        return result

    @app.post("/api/jobs/cancel")
    def cancel():
        ok = jobs.cancel()
        watcher.tick()
        return {"ok": ok, "message": "Обработка остановлена" if ok else "Сейчас ничего не обрабатывается"}

    class FileBody(BaseModel):
        file: str

    def recording_path(name: str) -> Path:
        if "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(400, "Недопустимое имя файла")
        path = Path(core.cfg["recordings_dir"]).expanduser() / name
        if not path.is_file():
            raise HTTPException(404, "Файл не найден в папке записей")
        return path

    @app.post("/api/jobs/retry")
    def retry(body: FileBody):
        path = recording_path(body.file)
        jobs.unignore_file(path.name)
        result = jobs.start(core.cfg, [path])
        watcher.tick()
        return result

    @app.post("/api/jobs/dismiss")
    def dismiss(body: FileBody):
        recording_path(body.file)
        jobs.ignore_file(body.file)
        watcher.tick()
        return {"ok": True}

    @app.get("/api/jobs/log")
    def job_log(file: str):
        return {"events": jobs.log_for(file), "tail": jobs.worker_log_tail()}

    # ------------------------------------------------------------------ чат
    class ChatBody(BaseModel):
        question: str
        scope: dict = {"kind": "all"}
        previous: list[str] = []
        asked: list[str] = []

    @app.post("/api/chat")
    def chat(body: ChatBody):
        if not core.index.ready:
            detail = ("Поисковый индекс не запустился: " + core.index.error) if core.index.state == "error" \
                else "Поисковый индекс ещё готовится — это занимает до минуты после запуска."
            raise HTTPException(503, detail)
        try:
            return services.chat(core, body.question, body.scope, body.previous, body.asked[-20:])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except knowledge.IndexNotReady as exc:
            raise HTTPException(503, "Поисковый индекс ещё готовится") from exc

    class ChatSaveBody(BaseModel):
        question: str = Field(..., min_length=1, max_length=2000)
        answer: str = Field(..., min_length=1, max_length=30_000)
        sources: list[dict] = Field(default_factory=list, max_length=20)
        scope: str = Field("", max_length=200)

    @app.post("/api/chat/save")
    def chat_save(body: ChatSaveBody):
        """«Сохранить ответ в Obsidian»: заметка Ответы/… с источниками; повторное сохранение того же ответа дублей не создаёт."""
        try:
            return answers.save(core, body.question, body.answer, body.sources, body.scope)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"Не удалось сохранить заметку: {exc}") from exc

    # ------------------------------------------------------------------ фронтенд
    index_file = config.WEB_DIR / "index.html"
    if (config.WEB_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=config.WEB_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Не найдено")
        candidate = (config.WEB_DIR / path).resolve()
        if path and candidate.is_file() and config.WEB_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        if index_file.exists():
            return FileResponse(index_file, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<h1>Memory Desk</h1><p>Интерфейс не собран. Выполните <code>cd frontend && npm run build</code>.</p>",
                            status_code=503)

    return app
