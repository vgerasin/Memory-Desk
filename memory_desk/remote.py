"""Чтение с телефона по домашней сети: отдельный минимальный «шлюз только для чтения».

Это **не** кабинет, открытый в сеть. Кабинет по-прежнему слушает только 127.0.0.1. Когда человек включает доступ с
телефона, рядом поднимается второй, крошечный сервер с собственным набором маршрутов: сегодня, список записей, одна
запись, задачи и решения, люди, проекты. Ни записи, ни удаления, ни настроек, ни файлов, ни звука, ни чата, ни
путей на диске у него нет вовсе, поэтому их нельзя ни вызвать, ни обойти.

Защита (подробный разбор угроз: docs/REMOTE-THREATS.md):
- по умолчанию выключено; включить можно только при заданном PIN и вводом PIN на компьютере;
- только локальные адреса: частные сети (192.168/16, 10/8, 172.16/12), link-local, Tailscale (100.64/10); любой
  публичный адрес, адрес этого же компьютера (так выглядят туннели) и запросы с заголовками прокси отклоняются;
- заголовок Host проверяется (защита от DNS-rebinding): только IP из локальной сети, имя компьютера и то, что вы
  разрешили сами;
- вход по PIN кабинета; от PIN остаётся отдельная проверка со своим счётчиком: после третьей ошибки задержка растёт
  вдвое, а после десятой подряд доступ с телефона выключается совсем до вашего ручного включения на компьютере;
- вошедшее устройство получает случайный токен (в базе хранится только его хэш), срок жизни 1, 7 или 30 дней;
- смена или снятие PIN отключает все устройства; каждое можно отозвать отдельно;
- в журнал попадают вход, неверный PIN, выход, отзыв, блокировка и открытие записи: номера и адреса, без текстов;
- стенограмма разговора по умолчанию на телефон не отдаётся (включается отдельно).

Состояние лежит в `remote.json` (права только для владельца). В интернет приложение не публикуется никогда.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import audit, config, lock

DEFAULT_PORT = 8734
DAYS = (1, 7, 30)
DEFAULT_DAYS = 7
COOKIE = "md_remote"
MAX_DEVICES = 10
FREE_ATTEMPTS = 3
MAX_DELAY = 900
HARD_LIMIT = 10             # столько неверных PIN подряд — доступ выключается до ручного включения
SEEN_EVERY = 60             # «был в сети» обновляется не чаще раза в минуту
VIEW_GAP = 600              # повторное открытие той же записи тем же устройством в журнал не пишется 10 минут
WEB_DIR = Path(__file__).resolve().parent / "remote_web"
CGNAT = ipaddress.ip_network("100.64.0.0/10")
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip", "cf-connecting-ip",
                 "cf-ray", "true-client-ip", "via", "x-original-forwarded-for")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/strings.json": ("strings.json", "application/json; charset=utf-8"),
          "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
          "/icon.svg": ("icon.svg", "image/svg+xml")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; manifest-src 'self'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class RemoteError(RuntimeError):
    def __init__(self, message: str, status: int = 400, retry: int = 0):
        super().__init__(message)
        self.status = status
        self.retry = retry


def now() -> float:
    return time.time()


def _file() -> Path:
    return config.DATA_DIR / "remote.json"


def cert_files() -> tuple[Path, Path]:
    folder = config.DATA_DIR / "remote"
    return folder / "cert.pem", folder / "key.pem"


def has_tls() -> bool:
    cert, key = cert_files()
    return cert.is_file() and key.is_file()


# --------------------------------------------------------------------------- адреса

def host_of(value: str) -> str:
    """Имя или IP из значения заголовка Host / адреса клиента, без порта и скобок IPv6."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def parse_ip(text: str):
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip


def is_lan(text: str) -> bool:
    """Адрес из домашней или рабочей сети (или Tailscale). Публичные и адрес этого компьютера — нет."""
    ip = parse_ip(text)
    if ip is None or ip.is_loopback or ip.is_unspecified or ip.is_multicast:
        return False
    return bool(ip.is_private or ip.is_link_local or ip in CGNAT)


def lan_addresses() -> list[str]:
    """IPv4-адреса этого компьютера в локальных сетях: их и показываем как адрес для телефона."""
    found: list[str] = []
    probes = [("10.255.255.255", 1), ("192.168.255.255", 1), ("172.31.255.255", 1)]
    for target, port in probes:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((target, port))
            found.append(sock.getsockname()[0])
        except OSError:
            pass
        finally:
            sock.close()
    try:
        found += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    result: list[str] = []
    for address in found:
        if is_lan(address) and address not in result:
            result.append(address)
    return result


def own_names() -> set[str]:
    name = socket.gethostname().strip().lower()
    names = {name} if name else set()
    if name:
        short = name.split(".")[0]
        names |= {short, short + ".local"}
    return names


def valid_hosts(text) -> list[str]:
    items = text if isinstance(text, list) else str(text or "").replace(";", ",").replace("\n", ",").split(",")
    result: list[str] = []
    for item in items:
        name = host_of(str(item))
        if not name or len(name) > 100 or not all(c.isalnum() or c in "-._" for c in name) or name in result:
            continue
        result.append(name)
    return result[:10]


def device_name(user_agent: str) -> str:
    text = user_agent or ""
    low = text.lower()
    kind = "iPhone" if "iphone" in low else "iPad" if "ipad" in low else "Android" if "android" in low else \
        "Mac" if "macintosh" in low else "Windows" if "windows" in low else "Linux" if "linux" in low else "Устройство"
    browser = "Edge" if "edg/" in low else "Chrome" if ("chrome/" in low or "crios/" in low) else "Firefox" if ("firefox/" in low or "fxios/" in low) \
        else "Safari" if "safari/" in low else ""
    return f"{kind} · {browser}" if browser else kind


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def pin_generation(record: dict | None) -> str:
    """Отпечаток текущего PIN: при смене или снятии PIN все выданные токены перестают подходить."""
    if not record:
        return ""
    return hashlib.sha256((str(record.get("salt", "")) + str(record.get("hash", ""))).encode("utf-8")).hexdigest()[:16]


def delay_after(failures: int) -> int:
    return 0 if failures < FREE_ATTEMPTS else min(MAX_DELAY, 5 * 2 ** (failures - FREE_ATTEMPTS))


# --------------------------------------------------------------------------- состояние

class Remote:
    """Настройки, устройства, счётчик попыток и второй сервер. Один экземпляр на приложение."""

    def __init__(self, core) -> None:
        self.core = core
        self._mutex = threading.RLock()
        self._login = threading.Lock()
        self._views: dict[tuple[str, str], float] = {}
        self._server = None
        self._thread: threading.Thread | None = None
        self.error = ""
        self._rec: dict | None = None
        self._rec_at = -1e9

    def _record(self, fresh: bool = False) -> dict | None:
        """Запись PIN. Связка ключей — вызов внешней программы, поэтому ответ держится пару секунд."""
        at = time.monotonic()
        if fresh or at - self._rec_at > 2:
            self._rec, self._rec_at = lock.read_record(), at
        return self._rec

    def forget_pin(self) -> None:
        self._rec_at = -1e9

    # --- файл
    def _read(self) -> dict:
        try:
            value = json.loads(_file().read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _state(self) -> dict:
        data = self._read()
        data.setdefault("enabled", False)
        data.setdefault("port", DEFAULT_PORT)
        data.setdefault("days", DEFAULT_DAYS)
        data.setdefault("transcript", False)
        data.setdefault("hosts", [])
        data.setdefault("devices", [])
        data.setdefault("failures", 0)
        data.setdefault("until", 0)
        return data

    def _save(self, data: dict) -> None:
        _file().parent.mkdir(parents=True, exist_ok=True)
        tmp = _file().with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(tmp, _file())

    # --- свойства
    @property
    def enabled(self) -> bool:
        return bool(self._state()["enabled"]) and self._record() is not None

    @property
    def transcript(self) -> bool:
        return bool(self._state()["transcript"])

    @property
    def port(self) -> int:
        try:
            port = int(self._state()["port"])
        except (TypeError, ValueError):
            return DEFAULT_PORT
        return port if 1024 <= port <= 65535 else DEFAULT_PORT

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._server is not None and getattr(self._server, "started", False))

    @property
    def secure(self) -> bool:
        return has_tls()

    def retry_after(self) -> int:
        return max(0, int(float(self._state()["until"]) - now() + 0.999))

    def allowed_host(self, header: str) -> bool:
        name = host_of(header)
        if not name:
            return False
        if parse_ip(name) is not None:
            return is_lan(name) or (os.environ.get("MEMORY_DESK_REMOTE_LOOPBACK") == "1" and parse_ip(name).is_loopback)
        port = header.rsplit(":", 1)[1] if ":" in header and not header.endswith("]") else ""
        if port and port != str(self.port):
            return False
        return name in own_names() or name in valid_hosts(self._state()["hosts"])

    # --- устройства
    def devices(self) -> list[dict]:
        at = now()
        generation = pin_generation(self._record())
        return [d for d in self._state()["devices"] if float(d.get("expires", 0)) > at and d.get("gen") == generation]

    def _public(self, device: dict, current: str = "") -> dict:
        return {"id": device["id"], "name": device.get("name", ""), "created": device.get("created", 0), "lastSeen": device.get("seen", 0),
                "lastIp": device.get("ip", ""), "expires": device.get("expires", 0), "current": device["id"] == current}

    def status(self) -> dict:
        state = self._state()
        record = self._record()
        port = self.port
        scheme = "https" if has_tls() else "http"
        urls = [f"{scheme}://{ip}:{port}" for ip in lan_addresses()]
        host = socket.gethostname().strip()
        if host and "." in host and host.lower().endswith(".local"):
            urls.append(f"{scheme}://{host}:{port}")
        return {"hasPin": record is not None, "enabled": bool(state["enabled"]) and record is not None, "running": self.running,
                "port": port, "days": state["days"] if state["days"] in DAYS else DEFAULT_DAYS, "daysOptions": list(DAYS),
                "transcript": bool(state["transcript"]), "hosts": state["hosts"], "tls": has_tls(), "urls": urls,
                "devices": [self._public(d) for d in self.devices()], "failures": int(state["failures"]),
                "retryAfter": self.retry_after(), "hardLimit": HARD_LIMIT, "error": self.error,
                "tlsFolder": str(cert_files()[0].parent)}

    def prune(self) -> None:
        with self._mutex:
            data = self._state()
            keep = self.devices_raw_valid(data)
            if len(keep) != len(data["devices"]):
                data["devices"] = keep
                self._save(data)

    def devices_raw_valid(self, data: dict) -> list[dict]:
        at = now()
        generation = pin_generation(self._record())
        return [d for d in data["devices"] if float(d.get("expires", 0)) > at and d.get("gen") == generation]

    # --- настройки (вызывает кабинет на этом компьютере)
    def configure(self, *, enabled: bool | None = None, days: int | None = None, transcript: bool | None = None,
                  hosts=None, port: int | None = None, pin: str = "") -> list[str]:
        """Меняет настройки; возвращает коды изменённого (для журнала, без значений): days, transcript, hosts, port, enabled, disabled."""
        changed: list[str] = []
        with self._mutex:
            data = self._state()
            if days is not None:
                if days not in DAYS:
                    raise RemoteError("Срок входа — 1, 7 или 30 дней")
                if data["days"] != days:
                    data["days"] = days
                    changed.append("days")
            if transcript is not None and bool(transcript) != bool(data["transcript"]):
                data["transcript"] = bool(transcript)
                changed.append("transcript")
            if hosts is not None:
                value = valid_hosts(hosts)
                if value != data["hosts"]:
                    data["hosts"] = value
                    changed.append("hosts")
            if port is not None and port != data["port"]:
                if not 1024 <= int(port) <= 65535 or int(port) == config.PORT:
                    raise RemoteError("Порт — от 1024 до 65535, не совпадающий с портом кабинета")
                data["port"] = int(port)
                changed.append("port")
            if enabled is True and not data["enabled"]:
                record = self._record(fresh=True)
                if record is None:
                    raise RemoteError("Сначала задайте PIN в разделе «Безопасность»: доступ с телефона открывается только по нему")
                wait = lock.Lock(self.core).retry_after()
                if wait:
                    raise RemoteError(f"Слишком много неверных попыток PIN. Подождите {wait} с", 429, wait)
                if not lock.check(str(pin or ""), record):
                    lock.Lock(self.core)._fail()
                    raise RemoteError("PIN указан неверно", 401)
                data["enabled"] = True
                data["failures"] = 0
                data["until"] = 0
                changed.append("enabled")
            elif enabled is False and data["enabled"]:
                data["enabled"] = False
                changed.append("disabled")
            self._save(data)
        if "port" in changed:
            self.stop()
        if self.enabled:
            self.start()
        else:
            self.stop()
        return changed

    def revoke(self, device_id: str) -> bool:
        with self._mutex:
            data = self._state()
            keep = [d for d in data["devices"] if d["id"] != device_id]
            if len(keep) == len(data["devices"]):
                return False
            data["devices"] = keep
            self._save(data)
        return True

    def revoke_all(self) -> int:
        with self._mutex:
            data = self._state()
            count = len(data["devices"])
            data["devices"] = []
            self._save(data)
        return count

    # --- вход
    def login(self, pin: str, ip: str, user_agent: str) -> tuple[str, dict]:
        with self._login:       # проверки идут по одной: параллельным запросом попытки не обогнать
            record = self._record(fresh=True)
            if not self.enabled or record is None:
                raise RemoteError("Доступ с телефона выключен", 403)
            wait = self.retry_after()
            if wait:
                raise RemoteError(f"Слишком много попыток. Подождите {wait} с", 429, wait)
            if not lock.check(str(pin or ""), record):
                data = self._state()
                data["failures"] = int(data["failures"]) + 1
                delay = delay_after(data["failures"])
                data["until"] = now() + delay if delay else 0
                hard = data["failures"] >= HARD_LIMIT
                if hard:
                    data["enabled"] = False
                self._save(data)
                audit.record("phone", "remote.login_failed", target="system", result="denied", detail=f"{ip}; попытка {data['failures']}")
                if hard:
                    audit.record("system", "remote.lockout", target="system", result="denied",
                                 detail=f"{HARD_LIMIT} неверных PIN подряд: доступ с телефона выключен")
                    threading.Thread(target=self.stop, daemon=True).start()
                    raise RemoteError("Слишком много неверных PIN: доступ с телефона выключен. Включите его заново на компьютере", 403)
                raise RemoteError("Неверный PIN" + (f". Следующая попытка через {delay} с" if delay else ""), 401, delay)
            token = secrets.token_urlsafe(32)
            at = now()
            with self._mutex:
                data = self._state()
                data["failures"] = 0
                data["until"] = 0
                generation = pin_generation(record)
                devices = [d for d in data["devices"] if float(d.get("expires", 0)) > at and d.get("gen") == generation]
                devices.sort(key=lambda d: d.get("seen", 0))
                devices = devices[-(MAX_DEVICES - 1):]       # вытесняется самое давнее
                days = data["days"] if data["days"] in DAYS else DEFAULT_DAYS
                device = {"id": secrets.token_hex(6), "name": device_name(user_agent), "hash": token_hash(token), "gen": generation,
                          "created": at, "seen": at, "ip": ip, "expires": at + days * 86400}
                data["devices"] = devices + [device]
                self._save(data)
        audit.record("phone", "remote.login", target="device", target_id=device["id"], detail=f"{ip}; {device['name']}")
        return token, device

    def authenticate(self, token: str | None, ip: str) -> dict | None:
        if not token or not self.enabled:
            return None
        digest = token_hash(token)
        at = now()
        generation = pin_generation(self._record())
        with self._mutex:
            data = self._state()
            for device in data["devices"]:
                if hmac_equal(device.get("hash", ""), digest):
                    if float(device.get("expires", 0)) <= at or device.get("gen") != generation:
                        return None
                    if at - float(device.get("seen", 0)) > SEEN_EVERY or device.get("ip") != ip:
                        device["seen"] = at
                        device["ip"] = ip
                        self._save(data)
                    return dict(device)
        return None

    def logout(self, device_id: str, ip: str) -> None:
        if self.revoke(device_id):
            audit.record("phone", "remote.logout", target="device", target_id=device_id, detail=ip)

    def viewed(self, device_id: str, meeting_id: str, ip: str) -> None:
        key, at = (device_id, meeting_id), time.monotonic()
        with self._mutex:
            if at - self._views.get(key, -1e9) < VIEW_GAP:
                return
            self._views[key] = at
            if len(self._views) > 500:
                self._views = {k: v for k, v in self._views.items() if at - v < VIEW_GAP}
        audit.record("phone", "remote.view", target="meeting", target_id=meeting_id, detail=f"{device_id}; {ip}")

    def denied(self, ip: str, reason: str) -> None:
        audit.record("phone", "remote.blocked", target="system", result="denied", detail=f"{ip}; {reason}")

    # --- второй сервер
    def start(self) -> bool:
        import uvicorn
        from .remote_gateway import build_gateway
        if self.running or not self.enabled:
            return self.running
        self.error = ""
        cert, key = cert_files()
        options = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)} if has_tls() else {}
        server = uvicorn.Server(uvicorn.Config(build_gateway(self), host=os.environ.get("MEMORY_DESK_REMOTE_BIND", "0.0.0.0"),
                                               port=self.port, log_level="warning", access_log=False, **options))
        def serve() -> None:
            try:
                server.run()
            except (SystemExit, OSError):      # порт занят: uvicorn завершает поток через sys.exit
                pass
        thread = threading.Thread(target=serve, name="memory-desk-remote", daemon=True)
        thread.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and thread.is_alive() and not server.started:
            time.sleep(0.05)
        if not server.started:
            self.error = f"Не удалось открыть порт {self.port}: возможно, он занят"
            server.should_exit = True
            return False
        self._server, self._thread = server, thread
        return True

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = self._thread = None
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=5)


def hmac_equal(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def origin_ok(origin: str, host: str) -> bool:
    """Запрос на вход должен идти со страницы этого же адреса."""
    if not origin:
        return True
    return urlsplit(origin).netloc.lower() == (host or "").lower()
