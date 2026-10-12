"""Генератор тестового окружения: Obsidian-заметки в формате пайплайна, записи, очередь, конфиг.

python tests/make_fixture.py <папка> [--processing]
Печатает export-команды для запуска сервера на этих данных."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import meetings_process as mp  # noqa: E402

TODAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

SEED = [
    dict(off=0, time="09:00", dur=1820, project="КХД", type="Встреча один на один", people=["Анна Соколова"], reviewed=True,
         title="Встреча один на один с Анной",
         summary=["Анна берёт на себя миграцию справочников в новое хранилище", "Для работы нужен доступ к тестовому контуру до пятницы", "Обсудили нагрузку: тестовый контур перегружен ночными расчётами"],
         decisions=[("Миграцию справочников ведёт Анна", 40)], tasks=[("Выдать Анне доступ к тестовому контуру", "Вы", "+4", 95)],
         risks=[("Тестовый контур перегружен ночными расчётами", 110)], questions=[("Нужен ли отдельный контур для справочников?", 110)],
         lines=[(0, "Вы", "Как ощущения после первой недели на проекте?"), (12, "Анна", "Нормально, но хочется взять что-то целиком."), (40, "Вы", "Давай миграция справочников будет полностью твоей."), (52, "Анна", "Отлично. Тогда мне нужен доступ к тестовому контуру."), (95, "Вы", "Выдам до пятницы."), (110, "Анна", "И ещё — контур ночью перегружен расчётами.")]),
    dict(off=1, time="20:54", dur=754, project="КХД", type="Очная встреча", people=["Михаил Орлов", "Анна Соколова"], reviewed=True,
         title="Обсуждение истории и книжного наследия",
         summary=["Участники обсуждают историю, связанную с датой 12 ноября 1955 года", "Говорится о книге, содержащей результаты спортивных состязаний до конца века", "Предполагается, что владелец книги может ставить на победителя без риска проигрыша", "Упоминается родственник, который предоставил книгу и исчез"],
         decisions=[], tasks=[("Найти родственника, предоставившего книгу", "Михаил", None, 16)], risks=[],
         questions=[("Откуда на самом деле появилась книга?", 11)],
         lines=[(0, "Михаил", "Давайте начнём с даты — двенадцатое ноября пятьдесят пятого."), (5, "Анна", "Это ключевая точка всей истории."), (11, "Михаил", "А книга? Откуда она вообще взялась?"), (16, "Анна", "Её принёс родственник. Сказал, что там всё до конца века."), (24, "Михаил", "И после этого он просто исчез.")]),
    dict(off=1, time="20:19", dur=1268, project="", type="", people=["Михаил Орлов"], reviewed=False,
         title="Обсуждение машинного времени и последствий",
         summary=["Обсуждается история с машиной времени, изобретённой Доком Брауном", "Упоминается риск разрушения Вселенной из-за эксперимента со временем", "Планируется отправка в будущее для предотвращения катастрофы", "Предлагается перепрограммировать дом для улучшения безопасности"],
         decisions=[("Перепрограммировать систему безопасности дома", 33)], tasks=[("Проверить освещение у входа в дом", "Вы", "+1", 22)],
         risks=[("Дженнифер в опасности из-за возможного контакта с будущим", 12)], questions=[("Кто ещё знает о спортивном альманахе?", 22)],
         lines=[(0, "Михаил", "Это что-то так."), (3, "Вы", "Кто бы мог подумать?"), (6, "Михаил", "Он вернулся к началу сезона и поставил на кон."), (12, "Вы", "Что вы сказали?"), (22, "Михаил", "Спортивный альманах. И включи свет у входа."), (33, "Вы", "Дом надо перепрограммировать, так безопаснее.")]),
    dict(off=3, time="11:30", dur=2710, project="КХД", type="Zoom", people=["Анна Соколова", "Михаил Орлов"], reviewed=True,
         title="Созвон по архитектуре хранилища",
         summary=["Команда переходит на инкрементальную загрузку для крупных витрин", "Слой сырых данных остаётся без изменений до конца квартала", "Открыт вопрос версионирования справочников"],
         decisions=[("Перейти на инкрементальную загрузку витрин продаж", 14), ("Не менять слой сырых данных до конца квартала", 48)],
         tasks=[("Подготовить схему зависимостей витрин", "Анна", "+3", 30), ("Оценить объём истории справочников", "Михаил", "+2", 62)],
         risks=[("Полная перезагрузка уже занимает более 4 часов", 6)], questions=[("Где хранить историю изменений справочников?", 21)],
         lines=[(0, "Вы", "Всем привет, давайте сразу к схеме."), (6, "Михаил", "Полная перезагрузка занимает уже больше четырёх часов."), (14, "Вы", "Предлагаю инкремент по дате изменения для витрин продаж."), (21, "Анна", "Согласна, но справочники надо версионировать."), (30, "Анна", "Схему зависимостей соберу к среде."), (48, "Михаил", "Сырой слой предлагаю не трогать до конца квартала."), (62, "Вы", "Михаил, оценишь объём истории справочников?"), (70, "Михаил", "Да, до вторника.")]),
    dict(off=7, time="08:12", dur=186, project="Личное", type="Голосовая мысль", people=[], reviewed=True,
         title="Идеи для заметки о привычках",
         summary=["Привычка закрепляется быстрее, если привязана к существующему ритуалу", "Начинать с двух минут, а не с часа", "Пропуск одного дня не страшен"],
         decisions=[], tasks=[("Написать черновик заметки о привычках", "Вы", "0", 22)], risks=[], questions=[],
         lines=[(0, "Вы", "Мысль для заметки: привязывать новую привычку к старой."), (7, "Вы", "Например, читать сразу после утреннего кофе."), (14, "Вы", "И начинать с двух минут — не больше."), (22, "Вы", "Написать черновик сегодня.")]),
]

PROJECTS = {"КХД": ("kxd", "Корпоративное хранилище данных: перевод витрин на инкрементальную загрузку к концу квартала."),
            "Личное": ("lichnoe", "Заметки, идеи и голосовые мысли.")}


def stamp(sec: int) -> str:
    return mp.format_time(sec)


def make(dest: Path, processing: bool = False, with_audio: bool = True) -> dict[str, str]:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / ".obsidian").mkdir(exist_ok=True)
    vault = dest / "Claude" / "Встречи"
    projects_dir = dest / "Claude" / "Проекты"
    recordings = dest / "PLAUD"
    archive = recordings / "Обработано"
    project_root = dest / "project"
    for p in (vault, projects_dir, recordings, archive / TODAY.strftime("%Y-%m-%d"), project_root / "logs", project_root / "runtime"):
        p.mkdir(parents=True, exist_ok=True)
    for title, (pid, goal) in PROJECTS.items():
        (projects_dir / f"{title}.md").write_text(
            f'---\ntype: project\nproject_id: "{pid}"\ntitle: "{title}"\nstatus: active\n---\n\n# {title}\n\n## О проекте\n\n{goal}\n\n## Цели\n\n{goal}\n',
            encoding="utf-8")
    for index, seed in enumerate(SEED):
        recorded = TODAY - timedelta(days=seed["off"])
        hour, minute = map(int, seed["time"].split(":"))
        recorded = recorded.replace(hour=hour, minute=minute)
        audio_name = f"PLAUD_{recorded:%Y%m%d_%H%M}.mp3"
        data = {
            "title": seed["title"], "summary": seed["summary"], "topics": ["Хранилище данных", "Планирование"] if index in (0, 3) else [],
            "proposals": [], "decisions": [{"text": t, "evidence_time": stamp(s)} for t, s in seed["decisions"]],
            "tasks": [{"task": t, "owner": o, "deadline": ((TODAY + timedelta(days=int(d))).strftime("%Y-%m-%d") if d else "не указан") if d not in (None,) else "не указан",
                       "evidence_time": stamp(s)} for t, o, d, s in seed["tasks"]],
            "risks": [{"text": t, "evidence_time": stamp(s)} for t, s in seed["risks"]],
            "open_questions": [{"text": t, "evidence_time": stamp(s)} for t, s in seed["questions"]],
            "next_steps": [],
        }
        for task in data["tasks"]:
            if task["owner"] == "Вы":
                task["owner"] = "Вы"
        transcript = "\n\n".join(f"[{stamp(sec)}] **{who}:** {text}" if who != "Вы" or True else "" for sec, who, text in seed["lines"])
        day_folder = vault / f"{recorded:%Y}" / f"{recorded:%m}" / f"{recorded:%d}"
        (day_folder / "Транскрипты").mkdir(parents=True, exist_ok=True)
        note_name = f"{recorded:%Y-%m-%d %H-%M} — {mp.slug(seed['title'])}.md"
        transcript_name = f"{Path(note_name).stem} — стенограмма.md"
        source = Path(audio_name)
        note = mp.render_note(source, recorded, seed["dur"], data, transcript_name)
        if seed["project"]:
            pid = PROJECTS[seed["project"]][0]
            note = note.replace('project_id: ""', f'project_id: "{pid}"\nproject: "{seed["project"]}"')
        if seed["type"]:
            note = note.replace("source_type: Не указан", f'source_type: {seed["type"]}')
        if seed["people"]:
            block = "participants:\n" + "".join(f'  - "{n}"\n' for n in seed["people"])
            note = note.replace("status: processed\n", block + "status: processed\n", 1)
        (day_folder / note_name).write_text(note, encoding="utf-8")
        (day_folder / "Транскрипты" / transcript_name).write_text(
            mp.render_transcript(source, recorded, seed["dur"], seed["title"], note_name, transcript), encoding="utf-8")
        if with_audio:
            target = archive / TODAY.strftime("%Y-%m-%d") / audio_name
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "sine=frequency=330:duration=6,tremolo=f=3:d=0.9", "-q:a", "9", str(target)], check=True)
    # Очередь: два файла ждут, один — с ошибкой
    if with_audio:
        for name in ("Zoom_Ретро_спринта.m4a", "Голосовая_заметка_1130.mp3"):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=4", str(recordings / name)], check=True)
        (recordings / "Звонок_подрядчик.amr").write_bytes(b"not-audio")
    events = project_root / "logs" / "processing.jsonl"
    now = datetime.now()
    lines = []
    config = {"recordings_dir": str(recordings), "obsidian_dir": str(vault), "archive_dir": str(archive), "retention_days": 14,
              "auto_process": False, "calendar_source": ""}
    (dest / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    script = project_root / "meetings-process"
    script.write_text("#!/bin/sh\nsleep 600\n", encoding="utf-8")
    script.chmod(0o755)
    if processing:
        proc = subprocess.Popen(["sleep", "900"], start_new_session=True)
        started = now - timedelta(minutes=4)
        job = {"id": "20261012-111200", "pid": proc.pid, "state": "running", "started_at": started.isoformat(timespec="seconds"),
               "files": ["PLAUD_20261012_1104.mp3"], "count": 1}
        (project_root / "runtime" / "processing-job.json").write_text(json.dumps(job, ensure_ascii=False))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2468", "-q:a", "9",
                        str(recordings / "PLAUD_20261012_1104.mp3")], check=True)
        lines += [
            {"created_at": started.isoformat(timespec="seconds"), "event": "started", "source": "PLAUD_20261012_1104.mp3", "detail": "Начинаю обработку", "job_id": job["id"], "stage": "checking", "percent": 1},
            {"created_at": (started + timedelta(seconds=3)).isoformat(timespec="seconds"), "event": "progress", "source": "PLAUD_20261012_1104.mp3", "detail": "Расшифровываю запись локально", "job_id": job["id"], "stage": "transcribing", "percent": 10},
        ]
    events.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n", encoding="utf-8")
    env = {
        "MEMORY_DESK_ROOT": str(project_root), "MEMORY_DESK_CONFIG": str(dest / "config.json"),
        "MEMORY_DESK_DATA_DIR": str(project_root / "data"), "MEMORY_DESK_FAKE_EMBED": "1",
        "MEMORY_DESK_ALLOW_ANY_HOST": "1", "MEMORY_DESK_OLLAMA_URL": "http://127.0.0.1:9",
    }
    return env


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve()
    env = make(target, processing="--processing" in sys.argv)
    for k, v in env.items():
        print(f"export {k}='{v}'")
