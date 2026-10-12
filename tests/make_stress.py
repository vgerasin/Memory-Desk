"""Нагрузочный набор данных: ~60 записей с крайними случаями.

python tests/make_stress.py <папка>   → печатает export-команды (как make_fixture.py)

Краевые случаи: очень длинные названия и тексты, символы разметки и HTML в тексте, эмодзи, латиница,
однофамильцы, просроченные и бессрочные задачи, записи без стенограммы/резюме/проекта, 3-часовая запись
на 900 реплик, записи с одинаковым временем, давние записи (год назад), будущие сроки."""
from __future__ import annotations

import random
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_fixture as mf  # noqa: E402
from make_fixture import TODAY, mp, stamp  # noqa: E402

rnd = random.Random(7)

PEOPLE = ["Анна Соколова", "Михаил Орлов", "Ирина Петрова", "Сергей Волков", "Ольга Кузнецова", "Дмитрий Лебедев",
          "John Smith", "Анна Иванова", "Мария-Анна Фёдорова", "Ли Вэй", "Ёлкин Пётр", "О'Коннор Шон"]
PROJECTS = {"КХД": ("kxd", "Корпоративное хранилище данных."), "Личное": ("lichnoe", "Заметки."),
            "Отчётность": ("ckp", "Пересборка форм отчётности."), "Качество данных": ("dg", "Правила и метрики качества данных."),
            "Регламенты": ("org", "Новые регламенты работы команды."), "Обучение": ("stepik", "Внутренние обучающие материалы."),
            "Проект с очень длинным названием, которое не помещается ни в одну карточку интерфейса целиком": ("long", "Длинное название.")}
TYPES = ["Встреча один на один", "Очная встреча", "Zoom", "Голосовая мысль", "Планёрка", "Звонок"]
TOPICS = ["Хранилище данных", "Планирование", "Бюджет", "Найм", "Регламент", "Качество данных", "KPI", "Стенограммы"]
TASKS = ["Подготовить отчёт", "Согласовать регламент", "Собрать требования", "Отправить письмо подрядчику", "Проверить выгрузку из 1С",
         "Обновить дашборд", "Написать заметку", "Позвонить в ИТ", "Завести тикет", "Проверить права доступа"]
WEIRD = [
    "Задача с **жирным**, _курсивом_ и `кодом`", "Текст с <script>alert(1)</script> и <b>тегами</b>", "Кавычки \"двойные\" и 'одинарные' и «ёлочки»",
    "Символ | в тексте | таблицы", "Эмодзи 🚀 и ✅ в задаче", "Очень длинная задача " + "слово " * 60, "Строка\nс переносом", "# Заголовок как текст",
    "[[Ссылка Obsidian]] и [md](http://x)", "Юникод: ёЁ №§ — – … ‘’", "  пробелы   по краям  ", "A" * 300,
]


def long_lines(n: int) -> list[tuple[int, str, str]]:
    out, sec = [], 0
    who = ["Вы", "Анна", "Михаил", "Ирина"]
    for i in range(n):
        sec += rnd.randint(2, 14)
        out.append((sec, who[rnd.randint(0, 3)], f"Реплика номер {i + 1}: " + " ".join(rnd.choice(
            ["данные", "витрина", "план", "срок", "риск", "решение", "договорились", "проверим", "хранилище", "справочник"]) for _ in range(rnd.randint(3, 25)))))
    return out


def build_seed() -> list[dict]:
    seeds = []
    for i in range(52):
        off = rnd.choice([0, 0, 1, 1, 2, 3, 5, 8, 12, 20, 33, 50, 70, 95, 130, 200, 300, 380])
        proj = rnd.choice(list(PROJECTS) + [""])
        n_people = rnd.randint(0, 4)
        people = rnd.sample(PEOPLE, n_people)
        tasks = []
        for _ in range(rnd.randint(0, 4)):
            text = rnd.choice(TASKS + WEIRD[: rnd.randint(0, len(WEIRD))] if rnd.random() < .3 else TASKS)
            owner = rnd.choice(["Вы", "Анна", "Михаил", "Ирина", "", "Сергей Волков"])
            dl = rnd.choice([None, "-20", "-5", "-1", "0", "+1", "+3", "+10", "+60"])
            tasks.append((text, owner, dl, rnd.randint(5, 600)))
        decisions = [(rnd.choice(TASKS) if rnd.random() < .8 else rnd.choice(WEIRD), rnd.randint(5, 600)) for _ in range(rnd.randint(0, 3))]
        seeds.append(dict(
            off=off, time=f"{rnd.randint(8, 19):02d}:{rnd.choice([0, 15, 30, 45]):02d}", dur=rnd.choice([40, 186, 754, 1820, 3600, 5400]),
            project=proj, type=rnd.choice(TYPES + [""]), people=people, reviewed=rnd.random() < .7,
            title=rnd.choice(["Планёрка", "Созвон по бюджету", "Встреча: итоги недели", "Обсуждение найма", "Ретро спринта", "Мысли вслух",
                              "Очень длинное название встречи, которое никак не помещается в одну строку списка и должно аккуратно обрезаться " * 1,
                              "Встреча «в кавычках» / со слэшем", "Ревью регламента КХД (черновик)", "Звонок с подрядчиком"]) + (f" №{i}" if i % 3 else ""),
            summary=[] if i % 9 == 0 else [rnd.choice(["Договорились о сроках", "Обсудили риски", "Согласован бюджет", "Определён владелец задачи"]) for _ in range(rnd.randint(1, 4))],
            decisions=decisions, tasks=tasks,
            risks=[(rnd.choice(["Срок под угрозой", "Нет ресурса", "Зависимость от ИТ"]), 30)] if rnd.random() < .4 else [],
            questions=[(rnd.choice(["Кто владелец данных?", "Где хранить историю?", "Когда релиз?"]), 40)] if rnd.random() < .5 else [],
            lines=[] if i % 11 == 0 else [(0, "Вы", "Начинаем."), (10, "Анна", "Да."), (25, "Михаил", rnd.choice(WEIRD)), (40, "Вы", "Договорились.")]
            if i % 5 else [(s, w, t) for s, w, t in long_lines(20)],
        ))
    # крайние: 3-часовая запись на 900 реплик; два файла в одну минуту
    seeds.append(dict(off=4, time="10:00", dur=10800, project="КХД", type="Zoom", people=["Анна Соколова", "Михаил Орлов", "Ирина Петрова"], reviewed=True,
                      title="Трёхчасовой воркшоп", summary=["Длинное обсуждение архитектуры"] * 3, decisions=[("Принять архитектуру", 600)],
                      tasks=[("Согласовать архитектуру", "Анна", "+5", 700)], risks=[], questions=[], lines=long_lines(900)))
    for k in range(2):
        seeds.append(dict(off=6, time="15:30", dur=120, project="Личное", type="", people=[], reviewed=True, title="Две записи в одну минуту",
                          summary=["Дубль по времени"], decisions=[], tasks=[], risks=[], questions=[], lines=[(0, "Вы", f"Запись {k}")]))
    return seeds


def make(dest: Path) -> dict[str, str]:
    mf.PROJECTS = PROJECTS
    mf.SEED = build_seed()
    # make_fixture использует свои глобальные SEED/PROJECTS и один ffmpeg-файл на запись: аудио берём короче
    return mf.make(dest)


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve()
    env = make(target)
    for k, v in env.items():
        print(f"export {k}='{v}'")
