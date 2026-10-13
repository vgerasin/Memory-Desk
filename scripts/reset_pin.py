"""Снимает PIN Memory Desk, если вы его забыли. Запускается на вашем компьютере (двойной клик по «Сбросить PIN.command»).

PIN не шифрует файлы, поэтому его сброс ничего не открывает и не стирает: заметки, записи и база остаются на месте.
Сброс доступен только тому, кто может запускать программы на этом компьютере, то есть вам. В журнал действий
добавляется строка «PIN сброшен с компьютера».
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_desk import audit, lock  # noqa: E402


def main() -> int:
    lock.reset()
    audit.record("cli", "lock.reset", target="system", detail="PIN сброшен с компьютера")
    print("PIN снят. Откройте Memory Desk и задайте новый в разделе «Настройки → Безопасность».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
