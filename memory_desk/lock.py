"""Блокировка PIN: без PIN интерфейс и API отвечают отказом (423), пока человек не введёт его.

**PIN не шифрует файлы.** Заметки, записи и база лежат на диске как обычно; от чужих глаз на выключенном или украденном
компьютере защищает шифрование диска (FileVault), а блокировка нужна, когда вы отошли от открытого компьютера или
им пользуется кто-то ещё в вашей сессии. Об этом сказано и в настройках.

Хранение: от PIN остаётся только хэш (scrypt с солью). На macOS он лежит в Связке ключей, а если ею воспользоваться
нельзя, в файле `lock.json` с правами только для владельца. Неверные попытки считаются, после третьей включается
задержка, которая растёт вдвое (5 с, 10 с, 20 с … до 15 минут) и переживает перезапуск приложения.

Сессия: после верного PIN браузер получает случайный токен в cookie (`HttpOnly`, `SameSite=Strict`). Сессия живёт, пока
есть действия человека: интерфейс шлёт «я здесь» при вводе и кликах, а любое изменение данных тоже считается
действием. Фоновые запросы (обновление списков, потоки событий) сессию не продлевают, поэтому бездействие
заканчивается блокировкой, даже если страница открыта.

Для MCP и командной строки есть отдельные токены (`access.py`); PIN кабинета на них не распространяется.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import audit, config

MIN_LEN, MAX_LEN = 4, 12
TIMEOUTS = (1, 5, 15, 30, 60)
DEFAULT_TIMEOUT = 15
FREE_ATTEMPTS = 3
MAX_DELAY = 900
MAX_SESSION = 12 * 3600
COOKIE = "md_session"
KEYCHAIN_SERVICE = "Memory Desk PIN"
KEYCHAIN_ACCOUNT = "memory-desk"
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


class LockError(RuntimeError):
    """Действие отклонено; текст понятен пользователю. `status` — код ответа API, `retry` — через сколько секунд повторить."""

    def __init__(self, message: str, status: int = 400, retry: int = 0):
        super().__init__(message)
        self.status = status
        self.retry = retry


def now() -> float:
    return time.time()


# --------------------------------------------------------------------------- хранилище хэша

def _file() -> Path:
    return config.DATA_DIR / "lock.json"


def _state_file() -> Path:
    return config.DATA_DIR / "lock-state.json"


def _have_keychain() -> bool:
    return sys.platform == "darwin" and shutil.which("security") is not None


def _keychain_read() -> dict | None:
    if not _have_keychain():
        return None
    try:
        done = subprocess.run(["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w"],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout.strip())
    except ValueError:
        return None


def _keychain_write(record: dict) -> bool:
    if not _have_keychain():
        return False
    try:
        done = subprocess.run(["security", "add-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-U",
                               "-w", json.dumps(record)], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _keychain_delete() -> None:
    if _have_keychain():
        try:
            subprocess.run(["security", "delete-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE],
                           capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass


def read_record() -> dict | None:
    record = _keychain_read()
    if record:
        return record
    try:
        value = json.loads(_file().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and value.get("hash") else None
    except (OSError, ValueError):
        return None


def write_record(record: dict) -> str:
    """Сохраняет хэш; возвращает, куда: «keychain» или «file»."""
    if _keychain_write(record):
        _file().unlink(missing_ok=True)
        return "keychain"
    _file().parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(record, handle)
    return "file"


def delete_record() -> None:
    _keychain_delete()
    _file().unlink(missing_ok=True)


# --------------------------------------------------------------------------- PIN

def valid_pin(pin: str) -> str:
    pin = str(pin or "").strip()
    if not pin.isdigit() or not MIN_LEN <= len(pin) <= MAX_LEN:
        raise LockError(f"PIN — от {MIN_LEN} до {MAX_LEN} цифр")
    return pin


def _hash(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode("utf-8"), salt=salt, **_SCRYPT)


def make_record(pin: str) -> dict:
    salt = secrets.token_bytes(16)
    return {"algo": "scrypt", **{k: v for k, v in _SCRYPT.items()}, "salt": salt.hex(), "hash": _hash(pin, salt).hex()}


def check(pin: str, record: dict) -> bool:
    try:
        params = {k: int(record.get(k, _SCRYPT[k])) for k in _SCRYPT}
        expected = bytes.fromhex(record["hash"])
        actual = hashlib.scrypt(str(pin).encode("utf-8"), salt=bytes.fromhex(record["salt"]), **params)
    except (KeyError, ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# --------------------------------------------------------------------------- состояние попыток

def _read_state() -> dict:
    try:
        value = json.loads(_state_file().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        _state_file().parent.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def delay_after(failures: int) -> int:
    return 0 if failures < FREE_ATTEMPTS else min(MAX_DELAY, 5 * 2 ** (failures - FREE_ATTEMPTS))


class Lock:
    """Сессии и счётчик попыток. Один экземпляр на приложение."""

    def __init__(self, core) -> None:
        self.core = core
        self._sessions: dict[str, float] = {}
        self._started: dict[str, float] = {}
        self._mutex = threading.Lock()
        self._enabled: bool | None = None
        self._enabled_at = 0.0

    # --- настройки
    @property
    def timeout_min(self) -> int:
        try:
            value = int(self.core.cfg.get("lock_timeout_min", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            value = DEFAULT_TIMEOUT
        return value if value in TIMEOUTS else DEFAULT_TIMEOUT

    @property
    def enabled(self) -> bool:
        """Есть ли PIN. Связка ключей — вызов внешней программы, поэтому ответ держится несколько секунд."""
        at = time.monotonic()
        if self._enabled is None or at - self._enabled_at > 3:
            self._enabled = read_record() is not None
            self._enabled_at = at
        return self._enabled

    def forget_cache(self) -> None:
        self._enabled = None

    # --- сессии
    def _expired(self, token: str, at: float) -> bool:
        last = self._sessions.get(token)
        return last is None or at - last > self.timeout_min * 60 or at - self._started.get(token, at) > MAX_SESSION

    def _drop(self, token: str) -> None:
        """Сессия закончилась сама: бездействие или предел в 12 часов. Это попадает в журнал."""
        existed = token in self._sessions
        self._sessions.pop(token, None)
        self._started.pop(token, None)
        if existed:
            audit.record("system", "lock.timeout", target="system", detail=f"{self.timeout_min} мин без действий")

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        at = now()
        with self._mutex:
            if self._expired(token, at):
                self._drop(token)
                return False
            return True

    def touch(self, token: str | None) -> bool:
        """Отмечает действие человека; False, если сессии уже нет (время вышло или блокировка включена)."""
        if not token:
            return False
        at = now()
        with self._mutex:
            if self._expired(token, at):
                self._drop(token)
                return False
            self._sessions[token] = at
            return True

    def open_session(self) -> str:
        token = secrets.token_urlsafe(32)
        at = now()
        with self._mutex:
            self._sessions[token] = at
            self._started[token] = at
        return token

    def close(self, token: str | None = None) -> None:
        with self._mutex:
            if token:
                self._sessions.pop(token, None)
                self._started.pop(token, None)
            else:
                self._sessions.clear()
                self._started.clear()

    # --- вход
    def retry_after(self) -> int:
        state = _read_state()
        return max(0, int(float(state.get("until", 0)) - now() + 0.999))

    def unlock(self, pin: str) -> str:
        record = read_record()
        if record is None:
            return self.open_session()
        wait = self.retry_after()
        if wait:
            raise LockError(f"Слишком много попыток. Подождите {wait} с", 429, wait)
        if not check(str(pin or ""), record):
            state = _read_state()
            failures = int(state.get("failures", 0)) + 1
            delay = delay_after(failures)
            _write_state({"failures": failures, "until": now() + delay if delay else 0})
            raise LockError("Неверный PIN" + (f". Следующая попытка через {delay} с" if delay else ""), 401, delay)
        _write_state({"failures": 0, "until": 0})
        return self.open_session()

    def status(self, token: str | None) -> dict:
        enabled = self.enabled
        return {"enabled": enabled, "locked": enabled and not self.valid(token), "timeoutMin": self.timeout_min,
                "timeouts": list(TIMEOUTS), "retryAfter": self.retry_after() if enabled else 0,
                "minLength": MIN_LEN, "maxLength": MAX_LEN, "storage": ("keychain" if _keychain_read() else "file") if enabled else ""}

    # --- PIN
    def set_pin(self, pin: str, current: str = "") -> str:
        """Устанавливает или меняет PIN. Если PIN уже есть, нужен прежний."""
        pin = valid_pin(pin)
        record = read_record()
        if record is not None:
            wait = self.retry_after()
            if wait:
                raise LockError(f"Слишком много попыток. Подождите {wait} с", 429, wait)
            if not check(str(current or ""), record):
                self._fail()
                raise LockError("Прежний PIN указан неверно", 401)
        where = write_record(make_record(pin))
        self.forget_cache()
        _write_state({"failures": 0, "until": 0})
        self.close()         # сессии, открытые по прежнему PIN, закрываются
        return where

    def remove_pin(self, current: str) -> None:
        record = read_record()
        if record is None:
            return
        wait = self.retry_after()
        if wait:
            raise LockError(f"Слишком много попыток. Подождите {wait} с", 429, wait)
        if not check(str(current or ""), record):
            self._fail()
            raise LockError("PIN указан неверно", 401)
        delete_record()
        self.forget_cache()
        _write_state({"failures": 0, "until": 0})
        self.close()

    def _fail(self) -> None:
        state = _read_state()
        failures = int(state.get("failures", 0)) + 1
        delay = delay_after(failures)
        _write_state({"failures": failures, "until": now() + delay if delay else 0})


def reset() -> None:
    """Снимает PIN без ввода старого: вызывается из `scripts/reset_pin.py` на компьютере владельца."""
    delete_record()
    _state_file().unlink(missing_ok=True)
