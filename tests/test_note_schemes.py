"""Правка задачи или решения обновляет и схемы Mermaid в заметке (карта, схема решений, план задач)."""


NOTE = """# Встреча

## Предполагаемые решения и договорённости

- Перейти на дельта-загрузку — `⏱ 00:01:00`

## Задачи

- [ ] Подготовить отчёт (черновик) — **ответственный:** Анна; **срок:** 2026-11-01; `⏱ 00:02:00`

## Карта

```mermaid
mindmap
  root((Встреча))
    group1[Решения]
      item1_5[Перейти на дельта-загрузку]
    group2[Задачи]
      item2_8[Подготовить отчёт черновик]
```

```mermaid
flowchart LR
  root[Обсуждение вариантов]
  confirmed --> d1[Перейти на дельта-загрузку]
```

```mermaid
gantt
  title План выполнения
  dateFormat YYYY-MM-DD
  section Задачи
  Подготовить отчёт черновик :milestone, task1, 2026-11-01, 0d
```
"""


def test_schemes_follow_text_and_deadline(tmp_path, client):
    from memory_desk import knowledge  # после фикстуры: пути модуля берутся из окружения теста
    note = tmp_path / "n.md"
    note.write_text(NOTE, encoding="utf-8")
    decision_old = {"text": "Перейти на дельта-загрузку", "status": "draft"}
    assert knowledge.update_item_line(note, "decisions", decision_old, {**decision_old, "text": "Грузить витрины дельтой"}) == "updated"
    text = note.read_text(encoding="utf-8")
    assert text.count("Грузить витрины дельтой") == 3 and "Перейти на дельта-загрузку" not in text   # раздел, карта и схема решений

    task_old = {"text": "Подготовить отчёт (черновик)", "owner": "Анна", "deadline": "2026-11-01", "status": "open"}
    task_new = {**task_old, "text": "Подготовить квартальный отчёт", "deadline": "2026-12-05"}
    assert knowledge.update_item_line(note, "tasks", task_old, task_new) == "updated"
    text = note.read_text(encoding="utf-8")
    assert "item2_8[Подготовить квартальный отчёт]" in text
    assert "  Подготовить квартальный отчёт :milestone, task1, 2026-12-05, 0d" in text and "2026-11-01, 0d" not in text

    # срок убрали → вехи на плане больше нет, остальное цело
    cleared = {**task_new, "deadline": "не указан"}
    assert knowledge.update_item_line(note, "tasks", task_new, cleared) == "updated"
    text = note.read_text(encoding="utf-8")
    assert ":milestone" not in text and "section Задачи" in text and "item2_8[Подготовить квартальный отчёт]" in text

    # сменили только статус — схемы не трогаются
    before = note.read_text(encoding="utf-8")
    knowledge.update_item_line(note, "tasks", cleared, {**cleared, "status": "done"})
    after = note.read_text(encoding="utf-8")
    assert after.count("```mermaid") == before.count("```mermaid") and "- [x] Подготовить квартальный отчёт" in after
