"""Разбор русских формулировок даты и времени («в четверг в 15:00», «через две недели», «до пятницы»).

Даты разрешает код, а не модель: модель ошибается в арифметике календаря, а здесь результат проверяем и повторяем.
Отсчёт идёт от даты записи, а не от сегодняшнего дня. Если формулировка допускает два прочтения («в следующий вторник»,
сказанное в понедельник), «на этой неделе» без дня, «через пару дней», несколько разных дат в одной реплике, дата не
угадывается: `ambiguous=True`, и карточка отправляет человека в «Изменить».
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, time, timedelta

WEEKDAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресень": 6}
WEEKDAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
NUMBERS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
           "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12, "пятнадцать": 15, "двадцать": 20,
           "тридцать": 30}
ORDINALS = {"первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5, "шестого": 6, "седьмого": 7,
            "восьмого": 8, "девятого": 9, "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12}
PAST_DAYS = 90   # дата без года, прошедшая не более чем на столько дней, считается прошлым, а не следующим годом
_NUM = r"(\d{1,3}|" + "|".join(sorted(NUMBERS, key=len, reverse=True)) + r")"
_WD = r"(понедельник\w*|вторник\w*|сред[ауыеой]\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)"
_MONTH = r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[яйе]\w*|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"


@dataclass
class When:
    day: date | None = None
    clock: time | None = None
    ambiguous: bool = False     # время названо, но однозначно разрешить его нельзя
    deadline: bool = False      # «до пятницы»: срок, а не момент встречи
    phrase: str = ""

    @property
    def found(self) -> bool:
        return self.day is not None or self.clock is not None or self.ambiguous


def _norm(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def _num(token: str) -> int:
    return int(token) if token.isdigit() else NUMBERS[token]


def _weekday(text: str) -> int:
    for stem, index in WEEKDAYS.items():
        if text.startswith(stem):
            return index
    raise ValueError(text)


def _month(text: str) -> int:
    for stem, number in MONTHS.items():
        if text.startswith(stem):
            return number
    raise ValueError(text)


def add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def next_weekday(base: date, weekday: int) -> date:
    """Ближайший день недели строго после `base`: сказанное в четверг «в четверг» означает следующий четверг."""
    return base + timedelta(days=(weekday - base.weekday() - 1) % 7 + 1)


def _with_marker(hour: int, marker: str | None) -> int:
    if marker == "утра":
        return hour
    if marker in ("дня", "вечера"):
        return hour + 12 if hour < 12 else hour
    if marker == "ночи":
        return 0 if hour == 12 else hour
    return hour + 12 if 1 <= hour <= 7 else hour    # «в три часа» в деловой речи — это 15:00


def _clock(raw: str) -> time | None:
    """Первое названное время суток или None."""
    match = re.search(r"\b(?:в|около|ровно)\s+(\d{1,2})[:.](\d{2})\b(?!\.\d)|\b(?:на|к)\s+(\d{1,2}):(\d{2})\b", raw)
    if match:
        hour = int(match.group(1) or match.group(3))
        minute = int(match.group(2) or match.group(4))
        marker = re.match(r"\s*(утра|дня|вечера|ночи)", raw[match.end():])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            if marker:
                hour = _with_marker(hour, marker.group(1))
            return time(hour % 24, minute)
    match = re.search(r"\b(?:в|к|около|ровно)\s+" + _NUM + r"(?:\s+час\w*)?(?:\s+(утра|дня|вечера|ночи))?(?=[\s,.;!?]|$)", raw)
    if match and (match.group(2) or "час" in match.group(0)):
        hour = _num(match.group(1))
        if 0 <= hour <= 24:
            return time(_with_marker(hour, match.group(2)) % 24, 0)
    match = re.search(r"\bв\s+половине\s+(\w+)", raw)
    if match and match.group(1) in ORDINALS:
        ordinal = ORDINALS[match.group(1)]
        return time(12 if ordinal == 1 else _with_marker(ordinal - 1, None), 30)
    return None


def resolve(text: str, base: date) -> When:
    """Дата и время из реплики; `base` — дата записи."""
    raw = _norm(text)
    days: dict[date, str] = {}
    ambiguous = False

    def add(day: date, phrase: str) -> None:
        days.setdefault(day, phrase)

    deadline = bool(re.search(r"\bдо\s+(?:" + _WD + r"|завтра|послезавтра)", raw))

    # «на следующей/этой неделе во вторник»: однозначно, разбирается раньше обычных дней недели
    def week_phrase(match: re.Match) -> str:
        nonlocal ambiguous
        weekday = _weekday(match.group(2))
        if match.group(1) == "следующей":
            add(monday_of(base) + timedelta(days=7 + weekday), match.group(0))
        else:
            target = monday_of(base) + timedelta(days=weekday)
            if target > base:
                add(target, match.group(0))
            else:
                ambiguous = True       # день этой недели уже прошёл или это сегодня
        return " "
    raw = re.sub(r"\bна\s+(следующей|этой)\s+неделе\s+(?:в|во|до|к)\s+" + _WD, week_phrase, raw)

    if re.search(r"\bпослезавтра\b", raw):
        add(base + timedelta(days=2), "послезавтра")
    if re.search(r"\bзавтра\b", raw):
        add(base + timedelta(days=1), "завтра")
    if re.search(r"\bсегодня\b", raw):
        add(base, "сегодня")

    for match in re.finditer(r"\bчерез\s+" + _NUM + r"\s+(дн\w*|день|недел\w*|месяц\w*)", raw):
        n, unit = _num(match.group(1)), match.group(2)
        if unit.startswith(("дн", "день")):
            add(base + timedelta(days=n), match.group(0))
        elif unit.startswith("недел"):
            add(base + timedelta(days=7 * n), match.group(0))
        else:
            add(add_months(base, n), match.group(0))
    for match in re.finditer(r"\bчерез\s+(недел\w*|месяц\w*)", raw):
        add(base + timedelta(days=7) if match.group(1).startswith("недел") else add_months(base, 1), match.group(0))
    if re.search(r"\bчерез\s+(?:пару|несколько|пол|полчаса|час)\b", raw):
        ambiguous = True
    if re.search(r"\bна\s+(?:следующей|этой|той|будущей)\s+неделе\b", raw) or \
            re.search(r"\b(?:в\s+конце|до\s+конца|в\s+начале|в\s+середине)\s+(?:недели|месяца|квартала|года)\b", raw):
        ambiguous = True

    for match in re.finditer(r"(?:\b(следующ\w+|ближайш\w+|эт\w+)\s+)?" + _WD, raw):
        weekday = _weekday(match.group(2))
        qualifier = match.group(1) or ""
        nearest = next_weekday(base, weekday)
        if qualifier.startswith("следующ"):
            target = monday_of(base) + timedelta(days=7 + weekday)
            if target != nearest:
                ambiguous = True      # «в следующий вторник» в понедельник: завтра или через неделю?
            else:
                add(target, match.group(0))
        elif qualifier.startswith("эт"):
            target = monday_of(base) + timedelta(days=weekday)
            if target > base:
                add(target, match.group(0))
            else:
                ambiguous = True
        else:
            add(nearest, match.group(0))

    for match in re.finditer(r"\b(\d{1,2})\s+" + _MONTH + r"(?:\s+(\d{4}))?\b", raw):
        number, month = int(match.group(1)), _month(match.group(2))
        year = int(match.group(3)) if match.group(3) else base.year
        try:
            day = date(year, month, number)
            if not match.group(3) and day < base:
                if (base - day).days <= PAST_DAYS:
                    ambiguous = True       # недавняя дата: скорее всего о прошлом, год не додумываем
                    continue
                day = date(year + 1, month, number)
        except ValueError:
            ambiguous = True
            continue
        add(day, match.group(0))
    for match in re.finditer(r"(?:\b(?:на|к|до|с|от|числа)\s+)?(?<![\d:.])(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?![\d:])", raw):
        if not match.group(3) and not re.match(r"(?:на|к|до|с|от|числа)\s", match.group(0)):
            continue       # «версия 2.1» — не дата: без предлога и года короткая запись через точку датой не считается
        number, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            continue
        year = base.year
        if match.group(3):
            year = int(match.group(3)) + (2000 if len(match.group(3)) == 2 else 0)
        try:
            day = date(year, month, number)
            if not match.group(3) and day < base:
                if (base - day).days <= PAST_DAYS:
                    ambiguous = True
                    continue
                day = date(year + 1, month, number)
        except ValueError:
            ambiguous = True
            continue
        add(day, match.group(0))
    for match in re.finditer(r"\b(?:к|до|на)\s+(\d{1,2})\s+числ\w*", raw):
        number = int(match.group(1))
        for shift in (0, 1):
            first = add_months(base.replace(day=1), shift)
            try:
                candidate = first.replace(day=number)
            except ValueError:
                continue
            if candidate >= base:
                add(candidate, match.group(0))
                break

    clock = _clock(raw)
    if len(days) > 1:
        return When(None, clock, True, deadline, "; ".join(days.values()))   # две даты в одной реплике: не угадываем
    if days:
        (day, phrase), = days.items()
        return When(day, clock, False, deadline, phrase)
    return When(None, clock, ambiguous, deadline, "")
