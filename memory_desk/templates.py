"""Шаблоны итоговой заметки: набор разделов, инструкции модели и порядок.

Шаблон — файл `templates/<id>.yaml` в папке программы (поставляемые) или `<данные>/templates/<id>.yaml`
(свои; обновление программы их не трогает, а при одинаковом id побеждает файл пользователя).

    name: Интервью
    description: Вопросы и ответы, цитаты, выводы.
    record_types: [interview]          # какие типы записей этот шаблон предлагается по умолчанию
    focus: Это интервью, а не совещание.   # необязательно: общая подсказка модели
    signature: Составлено по шаблону «Интервью»   # необязательно: строка в конце заметки
    sections:
      - key: summary                   # стандартный раздел: заголовок и формат строк задаёт кабинет
        instruction: Два-три тезиса: кто, о чём, главный вывод.
      - key: quotes                    # любой другой ключ — свой раздел
        title: Яркие цитаты            # заголовок обязателен только у своих разделов
        instruction: Дословные фразы собеседника.

Стандартные разделы (`summary`, `topics`, `decisions`, `tasks`, `open_questions`, `risks`, `next_steps`)
пишутся под теми же заголовками и в том же формате, что и раньше: именно их читает кабинет (реестр задач,
решения, вопросы). Поэтому у них нельзя менять заголовок. Разделы `map`, `flow`, `plan` — схемы, которые
строятся из найденного без участия модели.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ID = "meeting"
BUNDLED_DIR = Path(__file__).resolve().parents[1] / "templates"

# ключ → заголовок в заметке. Совпадает с тем, что читают knowledge.py и storage.py (проверяется тестом).
STANDARD: dict[str, str] = {
    "summary": "Кратко",
    "map": "Карта встречи",
    "flow": "Схема решений",
    "plan": "План по срокам",
    "topics": "Основные темы",
    "decisions": "Предполагаемые решения и договорённости",
    "tasks": "Задачи",
    "open_questions": "Открытые вопросы",
    "risks": "Риски",
    "next_steps": "Следующие шаги",
}
# ключи, которых просит у модели стандартный разбор (схемы строятся сами)
MODEL_KEYS = ("summary", "topics", "decisions", "tasks", "open_questions", "risks", "next_steps")
RESERVED_TITLES = {t.casefold() for t in STANDARD.values()} | {
    "стенограмма", "полная стенограмма", "краткое резюме", "карточка записи"}
MAX_SECTIONS = 14
MAX_FREE = 8
MAX_TEXT = 600

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,29}$")


class TemplateError(ValueError):
    """Шаблон не удалось прочитать или он не проходит проверку."""


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    instruction: str = ""

    @property
    def free(self) -> bool:
        return self.key not in STANDARD

    @property
    def data_key(self) -> str:
        """Ключ в JSON-ответе модели и в analysis.json."""
        return f"x_{self.key}" if self.free else self.key


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    description: str = ""
    sections: tuple[Section, ...] = ()
    record_types: tuple[str, ...] = ()
    focus: str = ""
    signature: str = ""
    builtin: bool = False
    path: str = field(default="", compare=False)

    def has(self, key: str) -> bool:
        return any(s.key == key for s in self.sections)

    @property
    def free_sections(self) -> tuple[Section, ...]:
        return tuple(s for s in self.sections if s.free)


# --------------------------------------------------------------------------- разбор YAML (подмножество)

def _unquote(token: str, number: int) -> object:
    token = token.strip()
    if not token:
        return ""
    if token[0] == '"':
        try:
            value = json.loads(token)
        except json.JSONDecodeError:
            raise TemplateError(f"Строка {number}: кавычки не закрыты или в строке лишние знаки") from None
        if not isinstance(value, str):
            raise TemplateError(f"Строка {number}: ожидался текст в кавычках")
        return value
    if token[0] == "'":
        if len(token) < 2 or token[-1] != "'":
            raise TemplateError(f"Строка {number}: кавычки не закрыты")
        return token[1:-1].replace("''", "'")
    token = re.sub(r"\s+#.*$", "", token).strip()
    low = token.casefold()
    if low in {"true", "yes"}:
        return True
    if low in {"false", "no"}:
        return False
    if low in {"null", "~", ""}:
        return ""
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    return token


def _split_inline(body: str, number: int) -> list[str]:
    parts, current, quote = [], "", ""
    for ch in body:
        if quote:
            current += ch
            if ch == quote and not current.endswith("\\" + quote):
                quote = ""
        elif ch in "\"'":
            quote = ch
            current += ch
        elif ch == ",":
            parts.append(current)
            current = ""
        else:
            current += ch
    if quote:
        raise TemplateError(f"Строка {number}: кавычки не закрыты")
    if current.strip() or parts:
        parts.append(current)
    return [p for p in parts if p.strip()]


def _scalar(token: str, number: int) -> object:
    token = token.strip()
    if token.startswith("["):
        if not token.endswith("]"):
            raise TemplateError(f"Строка {number}: список в [ ] не закрыт")
        return [_unquote(p, number) for p in _split_inline(token[1:-1], number)]
    if token.startswith("{"):
        raise TemplateError(f"Строка {number}: запись в { { } } не поддерживается, пишите ключи с новой строки")
    return _unquote(token, number)


def parse_yaml(text: str) -> dict:
    """Разбирает то подмножество YAML, которое нужно шаблонам: словари, списки, текст, `|` и `>`."""
    raw = text.replace("\t", "    ").splitlines()
    rows: list[tuple[int, int, str]] = []  # (номер строки, отступ, содержимое)
    for number, line in enumerate(raw, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        rows.append((number, len(line) - len(line.lstrip(" ")), line.strip()))
    if not rows:
        return {}
    position = 0

    def block_scalar(parent_indent: int, start_number: int, folded: bool) -> str:
        # строки сырого текста после start_number с отступом больше родительского
        lines: list[str] = []
        index = start_number  # raw индексируется с нуля: следующая строка — raw[start_number]
        while index < len(raw):
            line = raw[index]
            if line.strip() and len(line) - len(line.lstrip(" ")) <= parent_indent:
                break
            lines.append(line)
            index += 1
        while lines and not lines[-1].strip():
            lines.pop()
        indents = [len(l) - len(l.lstrip(" ")) for l in lines if l.strip()]
        base = min(indents) if indents else 0
        body = [l[base:] if l.strip() else "" for l in lines]
        return (" ".join(l.strip() for l in body if l.strip()) if folded else "\n".join(body)).strip()

    def skip_block(after_number: int, parent_indent: int) -> None:
        nonlocal position
        while position < len(rows) and rows[position][0] > after_number and rows[position][1] > parent_indent:
            position += 1

    def parse_mapping(indent: int) -> dict:
        nonlocal position
        result: dict = {}
        while position < len(rows):
            number, ind, content = rows[position]
            if ind < indent:
                break
            if ind > indent:
                raise TemplateError(f"Строка {number}: лишний отступ")
            if content.startswith("- "):
                break
            key, sep, rest = content.partition(":")
            if not sep or not key.strip():
                raise TemplateError(f"Строка {number}: ожидалось «ключ: значение»")
            key = key.strip()
            rest = rest.strip()
            position += 1
            if rest in {"|", ">", "|-", ">-"}:
                result[key] = block_scalar(ind, number, folded=rest.startswith(">"))
                skip_block(number, ind)
            elif rest and not rest.startswith("#"):
                result[key] = _scalar(rest, number)
            elif position < len(rows) and (rows[position][1] > ind
                                           or (rows[position][1] == ind and rows[position][2].startswith("- "))):
                child = rows[position][1]
                result[key] = parse_list(child) if rows[position][2].startswith("- ") else parse_mapping(child)
            else:
                result[key] = ""
        return result

    def parse_list(indent: int) -> list:
        nonlocal position
        result: list = []
        while position < len(rows):
            number, ind, content = rows[position]
            if ind < indent or not content.startswith("- "):
                break
            if ind > indent:
                raise TemplateError(f"Строка {number}: лишний отступ")
            item = content[2:].strip()
            if re.match(r"^[\w-]+\s*:(\s|$)", item) and not item.startswith(("'", '"')):
                # элемент-словарь: первая строка записана после «- », остальные выровнены под ней
                inner = ind + 2 + (len(content) - 2 - len(content[2:].lstrip()))
                rows[position] = (number, inner, item)
                result.append(parse_mapping(inner))
            else:
                position += 1
                result.append(_scalar(item, number))
        return result

    first = rows[0]
    value = parse_list(first[1]) if first[2].startswith("- ") else parse_mapping(first[1])
    if position < len(rows):
        raise TemplateError(f"Строка {rows[position][0]}: непонятная запись")
    if not isinstance(value, dict):
        raise TemplateError("Шаблон должен начинаться с пар «ключ: значение»")
    return value


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def dump_yaml(template: Template) -> str:
    """Записывает шаблон в том же виде, в каком его читает parse_yaml."""
    def text(key: str, value: str, indent: str = "") -> str:
        if "\n" in value:
            body = "\n".join(f"{indent}  {line}" if line else "" for line in value.split("\n"))
            return f"{indent}{key}: |\n{body}"
        return f"{indent}{key}: {_quote(value)}"

    lines = [f"name: {_quote(template.name)}"]
    if template.description:
        lines.append(text("description", template.description))
    if template.record_types:
        lines.append("record_types: [" + ", ".join(_quote(t) for t in template.record_types) + "]")
    if template.focus:
        lines.append(text("focus", template.focus))
    if template.signature:
        lines.append(text("signature", template.signature))
    lines.append("sections:")
    for section in template.sections:
        lines.append(f"  - key: {section.key}")
        if section.free:
            lines.append(text("title", section.title, "    "))
        if section.instruction:
            lines.append(text("instruction", section.instruction, "    "))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- проверка и загрузка

def _clean(value: object, limit: int = MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def build(template_id: str, data: dict, *, builtin: bool = False, path: str = "") -> Template:
    """Собирает шаблон из словаря и проверяет его; при ошибке — TemplateError с понятным текстом."""
    if not _ID.match(template_id):
        raise TemplateError("Идентификатор шаблона: латинские буквы, цифры, «_» и «-», до 40 знаков")
    name = _clean(data.get("name"), 80)
    if not name:
        raise TemplateError("У шаблона нет названия (name)")
    raw_sections = data.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise TemplateError("В шаблоне нет разделов (sections)")
    if len(raw_sections) > MAX_SECTIONS:
        raise TemplateError(f"Разделов больше {MAX_SECTIONS}")
    sections: list[Section] = []
    seen: set[str] = set()
    titles: set[str] = set()
    for index, raw in enumerate(raw_sections, 1):
        entry = {"key": raw} if isinstance(raw, str) else raw
        if not isinstance(entry, dict):
            raise TemplateError(f"Раздел {index}: ожидался набор полей key, title, instruction")
        key = _clean(entry.get("key"), 30).casefold()
        if not _KEY.match(key):
            raise TemplateError(f"Раздел {index}: ключ «{key}» — латинские буквы, цифры и «_»")
        if key in seen:
            raise TemplateError(f"Раздел «{key}» указан дважды")
        seen.add(key)
        instruction = _clean(entry.get("instruction"))
        if key in STANDARD:
            title = STANDARD[key]
        else:
            title = _clean(entry.get("title"), 60).lstrip("#").strip()
            if not title:
                raise TemplateError(f"Раздел «{key}»: нужен заголовок (title)")
            if title.casefold() in RESERVED_TITLES:
                raise TemplateError(f"Раздел «{key}»: заголовок «{title}» занят служебным разделом")
            if title.casefold() in titles:
                raise TemplateError(f"Заголовок «{title}» повторяется")
            titles.add(title.casefold())
        sections.append(Section(key, title, instruction))
    if not any(s.key == "summary" for s in sections):
        raise TemplateError("Нужен раздел summary («Кратко»): по нему кабинет показывает суть записи")
    if sum(1 for s in sections if s.free) > MAX_FREE:
        raise TemplateError(f"Своих разделов больше {MAX_FREE}")
    record_types = data.get("record_types") or []
    if isinstance(record_types, str):
        record_types = [record_types]
    return Template(
        id=template_id, name=name, description=_clean(data.get("description"), 300),
        sections=tuple(sections), record_types=tuple(_clean(t, 60) for t in record_types if _clean(t, 60)),
        focus=_clean(data.get("focus")), signature=_clean(data.get("signature"), 200),
        builtin=builtin, path=path)


def load_text(template_id: str, text: str, **kwargs) -> Template:
    return build(template_id, parse_yaml(text), **kwargs)


def user_dir() -> Path:
    override = os.environ.get("PLAUD_TEMPLATES_DIR")
    if override:
        return Path(override)
    from . import config
    return config.DATA_DIR / "templates"


def _read_dir(folder: Path, builtin: bool, problems: list[str]) -> dict[str, Template]:
    found: dict[str, Template] = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.yaml")) + sorted(folder.glob("*.yml")):
        if path.name.startswith("."):
            continue
        try:
            if path.stat().st_size > 50_000:
                raise TemplateError("файл больше 50 КБ")
            found[path.stem] = load_text(path.stem, path.read_text(encoding="utf-8"), builtin=builtin, path=str(path))
        except (OSError, UnicodeDecodeError, TemplateError) as exc:
            problems.append(f"{path.name}: {exc}")
    return found


_cache: dict = {"signature": None, "value": None}


def _signature() -> tuple:
    items = []
    for folder in (BUNDLED_DIR, user_dir()):
        try:
            for path in sorted(folder.iterdir()):
                if path.suffix in {".yaml", ".yml"}:
                    stat = path.stat()
                    items.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            items.append((str(folder), 0, 0))
    return tuple(items)


def load_all() -> tuple[dict[str, Template], list[str]]:
    """Все шаблоны (id → шаблон) и список файлов, которые прочитать не удалось.
    Свой шаблон с тем же id, что у поставляемого, заменяет его. Результат кэшируется до изменения файлов."""
    signature = _signature()
    if _cache["signature"] == signature and _cache["value"] is not None:
        return dict(_cache["value"][0]), list(_cache["value"][1])
    problems: list[str] = []
    result = _read_dir(BUNDLED_DIR, True, problems)
    result.update(_read_dir(user_dir(), False, problems))
    if DEFAULT_ID not in result:
        result[DEFAULT_ID] = fallback_default()
    _cache.update(signature=signature, value=(result, problems))
    return dict(result), list(problems)


def listing() -> list[Template]:
    items, _ = load_all()
    ordered = sorted(items.values(), key=lambda t: (t.id != DEFAULT_ID, not t.builtin, t.name.casefold()))
    return ordered


def get(template_id: str | None) -> Template:
    """Шаблон по id; неизвестный id и повреждённый файл дают стандартный шаблон."""
    items, _ = load_all()
    return items.get(template_id or DEFAULT_ID) or items[DEFAULT_ID]


def suggest(record_type_id: str | None) -> Template:
    """Шаблон по умолчанию для типа записи: первый, у которого этот тип указан в record_types."""
    if record_type_id:
        for template in listing():
            if record_type_id in template.record_types:
                return template
    return get(DEFAULT_ID)


_standard: list[Template] = []


def standard() -> Template:
    """Стандартный шаблон из поставки (папки пользователя и настроек не читает): им заметка составляется, если
    другой шаблон не задан или не получился."""
    if not _standard:
        path = BUNDLED_DIR / f"{DEFAULT_ID}.yaml"
        try:
            _standard.append(load_text(DEFAULT_ID, path.read_text(encoding="utf-8"), builtin=True, path=str(path)))
        except (OSError, UnicodeDecodeError, TemplateError):
            _standard.append(fallback_default())
    return _standard[0]


def fallback_default() -> Template:
    """Стандартная структура, если файл поставки потерян: заметка остаётся в формате, который читает кабинет."""
    keys = ("summary", "map", "flow", "plan", "topics", "decisions", "tasks", "open_questions", "risks", "next_steps")
    return Template(
        id=DEFAULT_ID, name="Совещание с решениями",
        description="Тезисы, решения, задачи, вопросы и риски: стандартная структура кабинета.",
        sections=tuple(Section(k, STANDARD[k]) for k in keys), record_types=("meeting", "offline_meeting", "zoom", "phone_call"),
        builtin=True)


def save_user(template_id: str, text: str) -> Template:
    """Проверяет текст шаблона и записывает его в папку пользователя."""
    template = load_text(template_id, text)
    folder = user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{template_id}.yaml"
    temp = target.with_name(f".{target.name}.tmp")
    temp.write_text(dump_yaml(template), encoding="utf-8")
    os.replace(temp, target)
    return load_text(template_id, target.read_text(encoding="utf-8"), path=str(target))


def delete_user(template_id: str) -> bool:
    target = user_dir() / f"{template_id}.yaml"
    if not _ID.match(template_id) or not target.is_file():
        return False
    target.unlink()
    return True


def new_id(name: str) -> str:
    """Свободный id для нового шаблона из названия (кириллица записывается латиницей)."""
    table = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
    base = re.sub(r"[^a-z0-9]+", "-", "".join(table.get(c, c) for c in name.casefold())).strip("-")[:30] or "template"
    if not base[0].isalpha():
        base = "t-" + base
    existing, _ = load_all()
    candidate, number = base, 2
    while candidate in existing:
        candidate = f"{base}-{number}"
        number += 1
    return candidate


# --------------------------------------------------------------------------- запрос к модели и проверка ответа

def prompt_lines(template: Template) -> tuple[str, str]:
    """(строки схемы JSON, пояснения к полям) для запроса к модели; для стандартного шаблона пояснений нет."""
    schema_by_key = {
        "summary": '  "summary": ["3-7 основных тезисов"],',
        "topics": '  "topics": ["основные темы"],',
        "decisions": '  "decisions": [{"text": "принятое решение", "evidence_time": "HH:MM:SS"}],',
        "tasks": '  "tasks": [{"task": "действие", "owner": "имя или не указано", "deadline": "срок или не указан", "evidence_time": "HH:MM:SS"}],',
        "risks": '  "risks": [{"text": "явно обсуждавшийся риск", "evidence_time": "HH:MM:SS"}],',
        "open_questions": '  "open_questions": [{"text": "нерешённый вопрос", "evidence_time": "HH:MM:SS"}],',
        "next_steps": '  "next_steps": [{"text": "явно согласованное действие", "evidence_time": "HH:MM:SS"}],',
    }
    wanted = {s.key for s in template.sections}
    lines = ['  "title": "короткое предметное название",']
    for key in ("summary", "topics"):
        if key in wanted:
            lines.append(schema_by_key[key])
    if "decisions" in wanted or "flow" in wanted:
        lines.append('  "proposals": [{"text": "предложенный вариант", "status": "accepted|rejected|discussed", "evidence_time": "HH:MM:SS"}],')
    for key in ("decisions", "tasks", "risks", "open_questions", "next_steps"):
        if key in wanted:
            lines.append(schema_by_key[key])
    for section in template.free_sections:
        label = section.title.replace('"', "'")
        lines.append(f'  "{section.data_key}": [{{"text": "{label}", "evidence_time": "HH:MM:SS"}}],')
    lines[-1] = lines[-1].rstrip(",")
    notes: list[str] = []
    if template.focus:
        notes.append(template.focus)
    for section in template.sections:
        if section.instruction and section.key not in {"map", "flow", "plan"}:
            notes.append(f"Поле «{section.data_key}» ({section.title}): {section.instruction}")
    return "\n".join(lines), "\n".join(notes)


def _is_standard_layout(template: Template) -> bool:
    return {s.key for s in template.sections} >= set(MODEL_KEYS) and not template.free_sections \
        and not template.focus and not any(s.instruction for s in template.sections)


def normalize_free(data: dict, template: Template, transcript_has_time) -> None:
    """Приводит ответы своих разделов к списку фактов {text, evidence_time}; чужое отбрасывает."""
    for section in template.free_sections:
        values = data.get(section.data_key)
        if isinstance(values, str):
            values = [v.strip(" -•\t") for v in values.splitlines() if v.strip(" -•\t")]
        elif isinstance(values, dict):
            values = [f"{k}: {v}" for k, v in values.items()]
        if not isinstance(values, list):
            values = []
        facts = []
        for value in values:
            if isinstance(value, str):
                text, stamp = value.strip(), ""
            elif isinstance(value, dict):
                text = _clean(value.get("text") or value.get("quote") or value.get("answer") or "", 1200)
                stamp = transcript_has_time(value.get("evidence_time"))
            else:
                continue
            if text:
                facts.append({"text": text, "evidence_time": stamp})
        data[section.data_key] = facts


def problems_in(data: dict, template: Template) -> list[str]:
    """Что не так с ответом модели для этого шаблона (пустой список — всё хорошо). Данные уже нормализованы."""
    problems: list[str] = []
    if template.has("summary") and not [x for x in data.get("summary", []) if str(x).strip()]:
        problems.append("нет краткого содержания")
    free = template.free_sections
    if free and not any(data.get(s.data_key) for s in free):
        problems.append("ни один из собственных разделов не заполнен")
    if not problems and not any(data.get(s.data_key) for s in template.sections if s.key in MODEL_KEYS or s.free):
        problems.append("все разделы пусты")
    return problems


# --------------------------------------------------------------------------- создание шаблона по описанию

DRAFT_PROMPT = """
Ты помогаешь составить шаблон итоговой заметки по записи разговора. По описанию пользователя предложи структуру.

Стандартные разделы (ключи, заголовки у них фиксированные, их читает программа):
- summary — Кратко (обязателен)
- topics — Основные темы
- decisions — Предполагаемые решения и договорённости
- tasks — Задачи (действие, ответственный, срок)
- open_questions — Открытые вопросы
- risks — Риски
- next_steps — Следующие шаги
Свои разделы: придумай ключ латиницей (например quotes), заголовок по-русски и короткую инструкцию для модели,
что именно в него выписывать. Своих разделов не больше 5, всего не больше 9. Добавляй только то, что нужно
для описанного случая; ненужные стандартные разделы не включай.

Верни только JSON:
{{
  "name": "название шаблона, 2-4 слова",
  "description": "одна фраза: для чего шаблон",
  "focus": "одно предложение для модели: что это за запись и на что обращать внимание",
  "sections": [
    {{"key": "summary", "instruction": "что должно быть в кратком содержании"}},
    {{"key": "латиницей_для_своего", "title": "Заголовок по-русски", "instruction": "что выписывать"}}
  ]
}}

Описание пользователя:
{description}
""".strip()


def draft_from_model(description: str, ask) -> Template:
    """Шаблон по текстовому описанию. `ask(prompt) -> dict` — запрос к модели. Результат не сохраняется."""
    description = _clean(description, 1500)
    if len(description) < 10:
        raise TemplateError("Опишите, для чего шаблон, хотя бы одной фразой")
    data = ask(DRAFT_PROMPT.format(description=description))
    if not isinstance(data, dict):
        raise TemplateError("Модель вернула неразборчивый ответ")
    sections = []
    for raw in data.get("sections") or []:
        if not isinstance(raw, dict):
            continue
        key = _clean(raw.get("key"), 30).casefold().replace("-", "_").replace(" ", "_")
        if key in STANDARD and key not in {"summary"} | set(MODEL_KEYS):
            continue
        if not _KEY.match(key) or any(s["key"] == key for s in sections):
            continue
        entry = {"key": key, "instruction": raw.get("instruction", "")}
        if key not in STANDARD:
            title = _clean(raw.get("title"), 60).lstrip("#").strip()
            if not title or title.casefold() in RESERVED_TITLES or any(
                    str(s.get("title", "")).casefold() == title.casefold() for s in sections):
                continue
            entry["title"] = title
        sections.append(entry)
    if not any(s["key"] == "summary" for s in sections):
        sections.insert(0, {"key": "summary", "instruction": ""})
    data = {**data, "sections": sections[:MAX_SECTIONS]}
    if not _clean(data.get("name"), 80):
        data["name"] = "Мой шаблон"
    return build(new_id(str(data["name"])), data)


def public(template: Template) -> dict:
    """Описание для интерфейса."""
    return {
        "id": template.id, "name": template.name, "description": template.description,
        "builtin": template.builtin, "overridesBundled": not template.builtin and (BUNDLED_DIR / f"{template.id}.yaml").is_file(),
        "recordTypes": list(template.record_types), "focus": template.focus,
        "signature": template.signature,
        "sections": [{"key": s.key, "title": s.title, "instruction": s.instruction, "free": s.free}
                     for s in template.sections],
    }
