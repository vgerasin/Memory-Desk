"""Командная строка: `memory-desk ask | search | list | status | ingest | login`.

Работает с запущенным кабинетом по токену доступа (Настройки → Безопасность → Доступ для программ). Если кабинет не
запущен, команда так и говорит и подсказывает, как его открыть (код выхода 3). Везде, где есть смысл, доступен `--json`.

Коды выхода: 0 — готово, 1 — ошибка, 2 — неверный вызов, 3 — кабинет не запущен, 4 — нет токена или он не подошёл.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from . import client
from .client import ClientError, Offline

AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4"}


def parse_since(text: str, today: date | None = None) -> str:
    """«7d», «2w», «3m» или «2026-10-01» → дата ГГГГ-ММ-ДД, с которой показывать."""
    today = today or date.today()
    text = (text or "").strip().lower()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        date.fromisoformat(text)
        return text
    match = re.fullmatch(r"(\d{1,4})\s*([dwm])", text)
    if not match:
        raise ValueError("Срок задаётся как 7d (дни), 2w (недели), 3m (месяцы) или датой 2026-10-01")
    number, unit = int(match.group(1)), match.group(2)
    days = number * {"d": 1, "w": 7, "m": 30}[unit]
    return (today - timedelta(days=days)).isoformat()


def out(args, payload, text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(text)


# --------------------------------------------------------------------------- команды

def cmd_login(args) -> int:
    url = (args.url or client.connection()[0]).rstrip("/")
    token = args.token or (input("Токен доступа (mdt_…): ").strip() if sys.stdin.isatty() else sys.stdin.readline().strip())
    if not token.startswith("mdt_"):
        raise ClientError("Токен выглядит как mdt_… — скопируйте его из кабинета целиком", 2)
    who = client.call("GET", "/api/access/me", url=url, auth=token, timeout=10)
    path = client.save(url, token)
    out(args, {"saved": str(path), **who},
        f"Готово: «{who['name']}» ({'чтение и запись' if who['write'] else 'только чтение'}). Токен сохранён в {path}")
    return 0


def cmd_mcp(args) -> int:
    from . import mcp
    mcp.serve()
    return 0


def cmd_status(args) -> int:
    url, token = client.connection()
    try:
        health = client.call("GET", "/api/health", token=False, timeout=5)
    except Offline as exc:
        payload = {"running": False, "url": url, "hint": client.START_HINT}
        out(args, payload, str(exc))
        return exc.code
    info = {"running": True, "url": url, "version": health.get("version"), "index": health.get("indexState")}
    lines = [f"Memory Desk {health.get('version')} запущен ({url}); поисковый индекс: {health.get('indexState')}"]
    if token:
        jobs = client.call("GET", "/api/jobs", timeout=10)
        tasks = client.call("GET", "/api/tasks", params={"limit": 1}, timeout=10)
        info["queue"] = {"active": bool(jobs.get("active")), "files": len(jobs.get("jobs", []))}
        info["counts"] = tasks.get("counts", {})
        counts = info["counts"]
        lines.append(f"Очередь обработки: {'идёт' if jobs.get('active') else 'пуста' if not jobs.get('jobs') else 'ждёт запуска'}"
                     f" · файлов {len(jobs.get('jobs', []))}")
        lines.append(f"Задач: {counts.get('tasks', 0)} · решений: {counts.get('decisions', 0)} · застрявших: {counts.get('stuck', 0)}")
    else:
        lines.append("Токена нет: очередь и счётчики не показаны (memory-desk login).")
    out(args, info, "\n".join(lines))
    return 0


def cmd_list(args) -> int:
    since = parse_since(args.since) if args.since else ""
    if args.what == "meetings":
        data = client.call("GET", "/api/meetings", params={"project": args.project, "person": args.person, "limit": 1000})
        items = [m for m in data.get("items", []) if not since or (m.get("date") or "") >= since]
        items = items[: args.limit]
        rows = [f"{m['date']} {m.get('time', '')}  {m['title']}" + (f"  [{m['project']['title']}]" if m.get("project") else "") + f"  id={m['id']}"
                for m in items]
    else:
        data = client.call("GET", "/api/tasks", params={"kind": "decisions" if args.what == "decisions" else "tasks", "limit": 1000})
        items = [t for t in data.get("items", []) if not since or (t.get("meetingDate") or "") >= since]
        if args.status:
            items = [t for t in items if t.get("status") == args.status]
        items = items[: args.limit]
        rows = [f"{t.get('meetingDate', '')}  {t['text']}" + (f"  — {t['owner']}" if t.get("owner") else "") + (f"  срок {t['due']}" if t.get("due") else "")
                + f"  [{t.get('status', '')}]" for t in items]
    out(args, {"items": items, "total": len(items), "since": since or None}, "\n".join(rows) if rows else "Ничего не найдено.")
    return 0


def cmd_search(args) -> int:
    data = client.call("GET", "/api/search", params={"q": args.query, "limit": args.limit, "project": args.project, "person": args.person})
    rows = [f"{h['date']} {h['t']}  {h['title']}\n    {h['text'][:300]}\n    id={h['meetingId']}" for h in data["items"]]
    out(args, data, "\n".join(rows) if rows else "Ничего не найдено.")
    return 0


def cmd_ask(args) -> int:
    scope = {"kind": "project", "id": args.project} if args.project else {"kind": "all"}
    data = client.call("POST", "/api/chat", body={"question": args.question, "scope": scope}, timeout=300)
    sources = data.get("sources", [])
    lines = [data.get("answer") or data.get("markdown") or ""]
    if sources:
        lines.append("\nИсточники:")
        lines += [f"  · {s.get('date', '')} {s.get('t', '')}  {s.get('title', '')}  (id={s.get('meetingId', '')})" for s in sources]
    out(args, data, "\n".join(lines).strip())
    return 0


def cmd_ingest(args) -> int:
    files = [Path(f).expanduser() for f in args.files]
    for f in files:
        if not f.is_file():
            raise ClientError(f"Нет такого файла: {f}", 2)
        if f.suffix.lower() not in AUDIO:
            raise ClientError(f"{f.name}: принимаются только аудиофайлы ({', '.join(sorted(AUDIO))})", 2)
    process = args.process or args.wait
    results = []
    for index, f in enumerate(files):
        last = index == len(files) - 1
        with f.open("rb") as handle:
            result = client.call("POST", "/api/ingest", params={"name": f.name, "process": "true" if process and last else ""},
                                 data=handle.read(), timeout=1800)
        results.append(result)
        if not args.json:
            print(f"Принят: {result['file']} ({result['bytes'] // 1024} КБ)")
    final = {"files": results}
    if args.wait:
        final["jobs"] = wait_for_queue(args.json, args.timeout)
    out(args, final, "Готово: файлы в очереди." if not args.wait else "Обработка закончена.")
    return 0


def wait_for_queue(quiet: bool, limit: int) -> list[dict]:
    started = time.time()
    seen = ""
    while True:
        data = client.call("GET", "/api/jobs", timeout=15)
        running = data.get("running") or {}
        label = f"{running.get('file', '')}: {running.get('stage_label', '')} {running.get('percent', '')}%" if running else ""
        if label != seen and not quiet and label:
            print(label)
            seen = label
        if not data.get("active"):
            return data.get("jobs", [])
        if time.time() - started > limit:
            raise ClientError(f"Обработка не закончилась за {limit} с; она продолжится в кабинете", 1)
        time.sleep(3)


# --------------------------------------------------------------------------- разбор аргументов

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory-desk", description="Командная строка Memory Desk. Нужен запущенный кабинет и токен доступа.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_, func):
        p = sub.add_parser(name, help=help_, description=help_)
        p.add_argument("--json", action="store_true", help="вывод в JSON")
        p.set_defaults(func=func)
        return p

    p = add("login", "сохранить токен доступа и проверить его", cmd_login)
    p.add_argument("--url", help="адрес кабинета (по умолчанию http://127.0.0.1:8502)")
    p.add_argument("--token", help="токен mdt_… (без флага спросит; можно передать на stdin)")
    add("mcp", "запустить MCP-сервер (stdio) для Claude Desktop и других клиентов", cmd_mcp)
    add("status", "запущен ли кабинет, очередь, счётчики", cmd_status)
    p = add("list", "записи, задачи или решения за период", cmd_list)
    p.add_argument("what", nargs="?", default="meetings", choices=["meetings", "tasks", "decisions"])
    p.add_argument("--since", help="7d, 2w, 3m или дата 2026-10-01")
    p.add_argument("--project")
    p.add_argument("--person")
    p.add_argument("--status", help="только для tasks/decisions: open, done, draft…")
    p.add_argument("--limit", type=int, default=50)
    p = add("search", "найти фрагменты в записях (без языковой модели)", cmd_search)
    p.add_argument("query")
    p.add_argument("--project")
    p.add_argument("--person")
    p.add_argument("--limit", type=int, default=8)
    p = add("ask", "задать вопрос по записям, ответ с источниками", cmd_ask)
    p.add_argument("question")
    p.add_argument("--project")
    p = add("ingest", "положить аудиофайлы в очередь обработки (нужен токен с записью)", cmd_ingest)
    p.add_argument("files", nargs="+")
    p.add_argument("--process", action="store_true", help="сразу запустить обработку")
    p.add_argument("--wait", action="store_true", help="запустить и дождаться конца обработки")
    p.add_argument("--timeout", type=int, default=3600, help="сколько секунд ждать с --wait")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except ClientError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc), "code": exc.code}, ensure_ascii=False))
        else:
            print(("" if isinstance(exc, Offline) else "Ошибка: ") + str(exc), file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
