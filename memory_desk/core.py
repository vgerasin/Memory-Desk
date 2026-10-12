"""Состояние приложения: встречи в памяти, индекс, наблюдение за хранилищем и папкой записей."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config, jobs, knowledge, storage, voice


def vault_fingerprint(vault: Path) -> str:
    """Дешёвая проверка изменений: только stat() файлов заметок, без чтения."""
    parts = []
    if vault.exists():
        for root, _dirs, files in os.walk(vault):
            for name in files:
                if name.endswith(".md"):
                    try:
                        stat = os.stat(os.path.join(root, name))
                    except OSError:
                        continue
                    parts.append(f"{root}/{name}:{stat.st_mtime_ns}:{stat.st_size}")
    parts.sort()
    return str(hash(tuple(parts)))


class Core:
    def __init__(self) -> None:
        self.cfg = config.load_config()
        knowledge.configure_paths(self.cfg)
        self.index = knowledge.SearchIndex()
        self.meetings: list[knowledge.Meeting] = []
        self.by_id: dict[str, knowledge.Meeting] = {}
        self.lock = threading.RLock()
        self.data_version = 0
        self.jobs_version = 0
        self.fingerprint = ""
        self._reindex_event = threading.Event()
        self._reindex_guard = threading.Lock()
        self._reindex_thread: threading.Thread | None = None
        self._stop = False
        self.booted_at = time.time()

    # --- запуск
    def boot(self) -> None:
        storage.initialize()
        self.reload(force=True)
        self.index.start(list(self.meetings))
        self._reindex_thread = threading.Thread(target=self._reindex_loop, name="reindex", daemon=True)
        self._reindex_thread.start()

    def shutdown(self) -> None:
        self._stop = True
        self._reindex_event.set()

    # --- заметки
    def reload(self, force: bool = False) -> bool:
        """Перечитывает заметки Obsidian и синхронизирует SQLite. True — если что-то изменилось."""
        with self.lock:
            fingerprint = vault_fingerprint(knowledge.VAULT)
            if not force and fingerprint == self.fingerprint:
                return False
            fresh = knowledge.load_meetings()
            storage.sync_meetings(fresh, knowledge.sections)
            storage.sync_projects(knowledge.load_projects())
            self.meetings = fresh
            self.by_id = {m.id: m for m in fresh}
            self.fingerprint = fingerprint
            self.data_version += 1
            self._ingest_voices(fresh)
        self.schedule_reindex()
        return True

    def _ingest_voices(self, meetings: list[knowledge.Meeting]) -> None:
        """Отпечатки голосов, которые оставила фоновая обработка, попадают в базу; узнанные люди — в участники записи."""
        try:
            by_path = {str(m.path): m.id for m in meetings}
            for meeting_id, names in voice.ingest_pending(by_path):
                if names:
                    storage.add_meeting_people(meeting_id, names)
        except Exception:  # noqa: BLE001 — отпечатки вторичны: заметки важнее
            pass

    def schedule_reindex(self) -> None:
        self._reindex_event.set()

    def start_reindex(self) -> bool:
        """Полная перестройка поискового индекса в фоне; ход виден в index.reindex. False — если уже идёт."""
        with self._reindex_guard:
            status = self.index.reindex
            if status["state"] == "running":
                return False
            status.update(state="running", done=0, total=0, records=0, message="Читаю заметки", startedAt=time.time(), finishedAt=0.0)
        threading.Thread(target=self._run_reindex, name="reindex", daemon=True).start()
        return True

    def _run_reindex(self) -> None:
        status = self.index.reindex
        try:
            if not self.index.encoder or self.index.client is None:
                raise RuntimeError("Поисковая модель ещё загружается: повторите через минуту")
            self.reload(force=True)
            with self.lock:
                meetings = list(self.meetings)
            status.update(records=len(meetings), message="Считаю векторы")
            previous = self.index.state
            self.index.state = "indexing"
            try:
                self.index.sync(meetings, force=True,
                                progress=lambda done, total: status.update(done=done, total=total))
            finally:
                self.index.state = "ready" if previous == "ready" else previous
            self.data_version += 1
            status.update(state="done", finishedAt=time.time(), message="Индекс перестроен")
        except Exception as exc:  # noqa: BLE001 — причина должна дойти до интерфейса
            status.update(state="error", finishedAt=time.time(), message=f"{type(exc).__name__}: {exc}")

    def _reindex_loop(self) -> None:
        while not self._stop:
            self._reindex_event.wait()
            if self._stop:
                return
            time.sleep(1.0)  # объединяем серию правок
            self._reindex_event.clear()
            if not self.index.encoder:
                continue  # индекс ещё грузится — _boot сам синхронизирует текущий набор
            try:
                with self.lock:
                    meetings = list(self.meetings)
                changed = self.index.sync(meetings)
                if changed:
                    self.data_version += 1
            except Exception as exc:  # noqa: BLE001
                self.index.error = f"{type(exc).__name__}: {exc}"

    def apply_settings(self, values: dict) -> dict:
        saved = config.save_config(values)
        for key in ("recordings_dir", "obsidian_dir", "archive_dir"):
            Path(saved[key]).mkdir(parents=True, exist_ok=True)
        self.cfg = saved
        knowledge.configure_paths(saved)
        self.reload(force=True)
        return saved

    # --- справочники (кэшируются по mtime)
    def projects(self) -> list[dict]:
        return knowledge.load_projects()

    def record_types(self) -> list[dict]:
        return knowledge.load_record_types()

    def archived_ids(self) -> set[str]:
        return {row["id"] for row in storage.rows("SELECT id FROM meetings WHERE archived=1")}

    def live_meetings(self) -> list[knowledge.Meeting]:
        archived = self.archived_ids()
        return sorted((m for m in self.meetings if m.id not in archived),
                      key=lambda m: (m.recorded_at or m.date, m.time), reverse=True)

    # --- очередь
    def queue(self) -> dict:
        return jobs.build_queue(self.cfg)


_core: Core | None = None


def get_core() -> Core:
    global _core
    if _core is None:
        _core = Core()
    return _core


def reset_core() -> None:
    global _core
    if _core:
        _core.shutdown()
    _core = None
