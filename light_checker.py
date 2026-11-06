"""Отримання стану світла з Shelly Cloud і логіка підтвердження змін стану.

Три різні ситуації:
- не вдалося отримати дані (немає інтернету на сервері, збій Shelly Cloud) —
  стан невідомий, сповіщень немає;
- пристрій онлайн — стан визначає напруга;
- пристрій офлайн — пропало світло АБО інтернет у будинку. Якщо задано
  ROUTER_HOST, бот перевіряє домашній роутер (він на ДБЖ): відповідає —
  інтернет є, отже світла немає точно; не відповідає — збій провайдера,
  стан невідомий. Без ROUTER_HOST бот чекає OFFLINE_CONFIRM_SECONDS і
  повідомляє про відключення, а коли пристрій повертається, за його uptime
  видно, чи він перезавантажувався: якщо ні — живлення не зникало, і бот
  повідомляє, що пропадав лише інтернет.
"""
import logging
import socket
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from config import ROUTER_HOST, ROUTER_PORT, SHELLY_AUTH_KEY, SHELLY_BASE_URL, TARGET_MAC, TIMEZONE

logger = logging.getLogger(__name__)

MIN_VOLTAGE = 100          # В; нижче — вважаємо, що світла немає
INITIAL_RETRY_DELAY = 2    # с
MAX_RETRY_DELAY = 30       # с
CONFIRMATIONS = 2          # скільки читань поспіль потрібно, щоб підтвердити зміну стану
OFFLINE_CONFIRM_SECONDS = 180  # с; скільки пристрій має бути офлайн, щоб повідомити про відключення
BOOT_TOLERANCE = 60        # с; допуск розбіжності годинників пристрою й сервера
LATE_RETURN_NOTE = 300     # с; якщо пристрій вийшов на зв'язок настільки пізніше за увімкнення
FUTURE_TOLERANCE = 120     # с; час пристрою «з майбутнього» вважаємо недостовірним
MIN_UNIXTIME = 1_600_000_000  # нижче — годинник пристрою ще не синхронізований

INITIAL = "initial"    # перша ініціалізація стану — без сповіщення
LOST = "lost"          # світло пропало
RESTORED = "restored"  # світло повернулось
INTERNET = "internet"  # світло не зникало, пропадав інтернет у будинку


class ShellyApiError(RuntimeError):
    """Не вдалося отримати дані з Shelly Cloud."""


@dataclass(frozen=True)
class Reading:
    """Одне читання стану з Shelly Cloud."""
    has_light: bool | None          # None — стан невідомий (помилка API)
    fetched_at: int                 # коли отримано дані (unix time)
    voltage: float | None = None
    frequency: float | None = None
    online: bool | None = None
    device_time: int | None = None  # коли пристрій востаннє оновлював дані, якщо відомо
    boot_time: int | None = None    # коли пристрій востаннє ввімкнувся (за його uptime)
    internet: bool | None = None    # чи відповідає домашній роутер; None — не перевірялося
    error: str | None = None

    @property
    def known(self) -> bool:
        return self.has_light is not None


ROUTER_OPEN = "open"        # з'єднання встановлено — роутер точно на зв'язку
ROUTER_REFUSED = "refused"  # відмова (закритий порт) — найімовірніше, відповів роутер
ROUTER_SILENT = "silent"    # відповіді немає — інтернету в будинку немає


def probe_router(host: str, port: int, timeout: float = 5, attempts: int = 2) -> str:
    """Перевіряє, чи відповідає домашній роутер з інтернету (ROUTER_OPEN/REFUSED/SILENT)."""
    for _ in range(attempts):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return ROUTER_OPEN
        except ConnectionRefusedError:
            return ROUTER_REFUSED
        except OSError:
            continue
    return ROUTER_SILENT


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_updated(value, now: int) -> int | None:
    """Розбирає поле `_updated` Shelly Cloud ("YYYY-MM-DD HH:MM:SS", UTC)."""
    if not value:
        return None
    try:
        dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
    return ts if ts <= now + FUTURE_TOLERANCE else None


def _boot_time(device: dict, device_time: int | None, now: int) -> int | None:
    """Момент увімкнення пристрою: час знімка мінус uptime (Gen2 — у sys, Gen1 — у корені)."""
    sys_status = device.get("sys") if isinstance(device.get("sys"), dict) else {}
    uptime = _to_float(sys_status.get("uptime", device.get("uptime")))
    if uptime is None or uptime < 0:
        return None
    unixtime = _to_float(sys_status.get("unixtime", device.get("unixtime")))
    if unixtime is not None and not MIN_UNIXTIME < unixtime <= now + FUTURE_TOLERANCE:
        unixtime = None
    reference = unixtime or device_time or now
    return int(reference - uptime)


def _extract_switch_data(device: dict) -> tuple[float | None, float | None]:
    """Напруга й частота з першого компонента switch* пристрою."""
    for key, value in device.items():
        if str(key).startswith("switch") and isinstance(value, dict):
            return _to_float(value.get("voltage")), _to_float(value.get("freq"))
    return None, None


class LightChecker:
    def __init__(self, auth_key=SHELLY_AUTH_KEY, base_url=SHELLY_BASE_URL,
                 target_mac=TARGET_MAC, session=None,
                 router_host=ROUTER_HOST, router_port=ROUTER_PORT):
        self.auth_key = auth_key
        self.base_url = base_url
        self.target_mac = target_mac
        self.router_host = router_host
        self.router_port = router_port
        self._router_open_seen = False
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._last: Reading | None = None
        self._uptime_logged = False

    def fetch_all_status(self, attempts: int = 3, timeout: float = 10) -> dict:
        """Запит /device/all_status з повторами. Кидає ShellyApiError."""
        url = f"{self.base_url}/device/all_status"
        delay = INITIAL_RETRY_DELAY
        last_error = "невідома помилка"

        for attempt in range(1, attempts + 1):
            try:
                response = self._session.post(
                    url, data={"auth_key": self.auth_key}, timeout=timeout
                )
                if response.status_code == 429:
                    last_error = "HTTP 429 (ліміт запитів)"
                else:
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("isok"):
                        return payload.get("data") or {}
                    errors = payload.get("errors") or payload.get("error") or "Unknown API error"
                    if "max_req" not in str(errors):
                        raise ShellyApiError(f"Shelly API помилка: {errors}")
                    last_error = f"ліміт запитів Shelly: {errors}"
            except requests.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"

            logger.warning("Shelly: спроба %d/%d невдала: %s", attempt, attempts, last_error)
            if attempt < attempts:
                time.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY)

        raise ShellyApiError(f"Не вдалося отримати дані з Shelly: {last_error}")

    def _find_device(self, devices: dict) -> dict | None:
        if self.target_mac:
            wanted = self.target_mac.lower()
            for mac, status in devices.items():
                if str(mac).lower() == wanted and isinstance(status, dict):
                    return status
            return None
        return next((s for s in devices.values() if isinstance(s, dict)), None)

    def parse_reading(self, data: dict, now: int) -> Reading:
        """Перетворює відповідь Shelly Cloud на Reading (без мережевих запитів)."""
        devices = data.get("devices_status") or {}
        device = self._find_device(devices) if isinstance(devices, dict) else None

        if device is None:
            logger.info("Пристрою %s немає у відповіді Shelly Cloud — вважаю його офлайн",
                        self.target_mac or "(перший)")
            return Reading(has_light=False, fetched_at=now, online=False)

        device_time = _parse_updated(device.get("_updated"), now)
        dev_info = device.get("_dev_info")
        if isinstance(dev_info, dict) and dev_info.get("online") is False:
            logger.info("Shelly Cloud повідомляє, що пристрій офлайн")
            return Reading(has_light=False, fetched_at=now, online=False, device_time=device_time)

        boot_time = _boot_time(device, device_time, now)
        if not self._uptime_logged:
            self._uptime_logged = True
            if boot_time is None:
                logger.warning("Shelly Cloud не передає uptime пристрою: зникнення інтернету "
                               "в будинку не вдасться відрізнити від відключення світла")
            else:
                logger.info("Shelly Cloud передає uptime пристрою (увімкнувся о %s UTC)",
                            datetime.fromtimestamp(boot_time, timezone.utc).strftime("%Y-%m-%d %H:%M"))

        voltage, frequency = _extract_switch_data(device)
        if voltage is None:
            logger.warning("Пристрій онлайн, але не передав напругу")
            return Reading(has_light=None, fetched_at=now, online=True, device_time=device_time,
                           boot_time=boot_time, error="Пристрій не передав напругу")

        has_light = voltage > MIN_VOLTAGE
        logger.info("Напруга %.1f В — світло %s", voltage, "Є" if has_light else "НЕМАЄ")
        return Reading(has_light=has_light, fetched_at=now, voltage=voltage, frequency=frequency,
                       online=True, device_time=device_time, boot_time=boot_time)

    def get_reading(self, max_age: float = 0, attempts: int = 3, timeout: float = 10) -> Reading:
        """Поточне читання. Блокуючий виклик — з async-коду через asyncio.to_thread.

        max_age > 0 дозволяє повернути збережене читання, якщо воно не старше
        за max_age секунд. Lock гарантує, що одночасно йде лише один запит до
        Shelly Cloud (у нього жорсткий ліміт запитів).
        Ніколи не кидає винятків: при будь-якій помилці повертає has_light=None.
        """
        with self._lock:
            cached = self._last
            if max_age > 0 and cached is not None and time.time() - cached.fetched_at <= max_age:
                return cached
            try:
                data = self.fetch_all_status(attempts=attempts, timeout=timeout)
                reading = self.parse_reading(data, int(time.time()))
                if reading.online is False and self.router_host:
                    reading = self._check_router(reading)
            except Exception as e:
                logger.error("Не вдалося отримати стан світла: %s", e)
                return Reading(has_light=None, fetched_at=int(time.time()), error=str(e))
            if reading.known:
                self._last = reading
            return reading

    def check_router(self) -> tuple[str, bool]:
        """Перевіряє роутер. Повертає (результат probe_router, чи є інтернет у будинку).

        Відмову (RST) може надіслати й обладнання провайдера, коли до роутера
        зв'язку вже немає. Тому щойно бот хоч раз побачив відкритий порт,
        інтернет вважається наявним лише за встановленого з'єднання.
        """
        result = probe_router(self.router_host, self.router_port)
        if result == ROUTER_OPEN:
            self._router_open_seen = True
        online = result == ROUTER_OPEN or (result == ROUTER_REFUSED and not self._router_open_seen)
        return result, online

    def _check_router(self, reading: Reading) -> Reading:
        """Shelly офлайн: розрізнити відключення світла й зникнення інтернету в будинку."""
        result, online = self.check_router()
        if online:
            logger.info("Shelly офлайн, а роутер відповідає (%s) — світла немає", result)
            return replace(reading, internet=True)
        logger.warning("Shelly офлайн і роутер не відповідає (%s) — немає інтернету в будинку", result)
        return replace(reading, has_light=None, internet=False, error="Немає інтернету в будинку")


@dataclass(frozen=True)
class StateChange:
    kind: str                             # INITIAL | LOST | RESTORED | INTERNET
    has_light: bool
    since: int                            # з якого моменту діє новий стан
    noticed_at: int                       # коли зміну підтверджено
    previous_duration: int | None = None  # скільки тривав попередній стан
    returned_at: int | None = None        # коли повернулося світло за uptime пристрою

    @property
    def initial(self) -> bool:
        return self.kind == INITIAL


class StateTracker:
    """Підтверджений стан світла. Чиста логіка, без мережі й БД.

    - Невідомі читання (has_light=None) ігноруються.
    - Зміна за напругою підтверджується `confirmations` читаннями поспіль.
    - Зникнення пристрою зі зв'язку (online=False) додатково має тривати
      щонайменше `offline_confirm_seconds`. Таке відключення позначається
      offline=True: причина невідома (світло або інтернет у будинку).
    - Коли пристрій повертається після offline-відключення, його boot_time
      показує, чи зникало живлення: якщо пристрій не перезавантажувався від
      моменту зникнення, пропадав лише інтернет (kind=INTERNET).
    """

    def __init__(self, has_light: bool | None = None, since: int | None = None,
                 offline: bool = False, light_since: int | None = None,
                 confirmations: int = CONFIRMATIONS,
                 offline_confirm_seconds: int = OFFLINE_CONFIRM_SECONDS):
        if confirmations < 1:
            raise ValueError("confirmations must be >= 1")
        self.has_light = has_light
        self.since = since
        self.offline = offline          # відключення визначене лише за зникненням пристрою
        self.light_since = light_since  # з якого часу було світло перед таким відключенням
        self.confirmations = confirmations
        self.offline_confirm_seconds = offline_confirm_seconds
        self._pending_count = 0
        self._pending_since: int | None = None

    @classmethod
    def from_dict(cls, data: dict | None, **kwargs) -> "StateTracker":
        if not data:
            return cls(**kwargs)
        return cls(has_light=data["has_light"], since=data["since"],
                   offline=bool(data.get("offline")), light_since=data.get("light_since"),
                   **kwargs)

    def to_dict(self) -> dict:
        return {"has_light": self.has_light, "since": self.since,
                "offline": self.offline, "light_since": self.light_since}

    @property
    def known(self) -> bool:
        return self.has_light is not None

    @property
    def pending(self) -> bool:
        """Є непідтверджена зміна стану."""
        return self._pending_count > 0

    def _reset_pending(self) -> None:
        self._pending_count = 0
        self._pending_since = None

    @staticmethod
    def _cause_unknown(reading: Reading) -> bool:
        """Пристрій офлайн, і невідомо чому: світло чи інтернет у будинку.

        Якщо роутер відповідає (internet=True), причина відома — світла немає.
        """
        return reading.has_light is False and reading.online is False and reading.internet is not True

    def observe(self, reading: Reading, now: int) -> StateChange | None:
        value = reading.has_light
        if value is None:
            return None

        if self.has_light is None:
            self.has_light, self.since = value, now
            self.offline = self._cause_unknown(reading)
            self.light_since = None
            self._reset_pending()
            return StateChange(INITIAL, value, since=now, noticed_at=now)

        if value == self.has_light:
            self._reset_pending()
            if value is False and not self._cause_unknown(reading):
                # Напруги немає на онлайн-пристрої або роутер підтвердив інтернет —
                # відключення справжнє
                self.offline = False
            return None

        if self._pending_count == 0:
            self._pending_since = now
        self._pending_count += 1
        if self._pending_count < self.confirmations:
            return None
        if (self._cause_unknown(reading)
                and now - self._pending_since < self.offline_confirm_seconds):
            return None

        started = self._pending_since
        self._reset_pending()
        if value is False:
            return self._lost(reading, started, now)
        return self._restored(reading, started, now)

    def _lost(self, reading: Reading, started: int, now: int) -> StateChange:
        offline = self._cause_unknown(reading)
        change = StateChange(
            LOST, False, since=started, noticed_at=now,
            previous_duration=started - self.since if self.since is not None else None,
        )
        self.light_since = self.since if offline else None
        self.offline = offline
        self.has_light, self.since = False, started
        return change

    def _restored(self, reading: Reading, started: int, now: int) -> StateChange:
        outage_start = self.since
        boot = reading.boot_time
        offline_outage = self.offline and boot is not None and outage_start is not None

        if offline_outage and boot <= outage_start - BOOT_TOLERANCE:
            # Пристрій не перезавантажувався: живлення не зникало, пропадав інтернет
            since = self.light_since if self.light_since is not None else outage_start
            change = StateChange(INTERNET, True, since=since, noticed_at=now,
                                 previous_duration=started - outage_start)
        else:
            # Пристрій увімкнувся після початку відключення — отже, саме тоді
            # повернулося живлення (навіть якщо інтернет з'явився пізніше)
            returned_at = (boot if boot is not None and outage_start is not None
                           and outage_start < boot <= now else None)
            since = returned_at if returned_at is not None else started
            change = StateChange(
                RESTORED, True, since=since, noticed_at=now,
                previous_duration=since - outage_start if outage_start is not None else None,
                returned_at=returned_at,
            )

        self.has_light, self.since = True, since
        self.offline, self.light_since = False, None
        return change


# ---------- Форматування повідомлень ----------

def _tz():
    try:
        return ZoneInfo(TIMEZONE)
    except Exception:
        logger.warning("Невідома таймзона %s, використовую UTC", TIMEZONE)
        return timezone.utc


def format_duration(seconds: int) -> str:
    """Тривалість у зрозумілому форматі."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} сек"
    if seconds < 3600:
        return f"{seconds // 60} хв {seconds % 60} сек"
    if seconds < 86400:
        return f"{seconds // 3600} год {(seconds % 3600) // 60} хв"
    return f"{seconds // 86400} дн {(seconds % 86400) // 3600} год"


def format_moment(timestamp: int, now: int) -> str:
    """«сьогодні о 14:05», «вчора о 23:10» або «05.11 о 08:00»."""
    tz = _tz()
    dt = datetime.fromtimestamp(timestamp, tz)
    today = datetime.fromtimestamp(now, tz).date()
    if dt.date() == today:
        return dt.strftime("сьогодні о %H:%M")
    if dt.date() == today - timedelta(days=1):
        return dt.strftime("вчора о %H:%M")
    return dt.strftime("%d.%m о %H:%M")


def format_ago(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "щойно"
    if seconds < 3600:
        return f"{seconds // 60} хв тому"
    if seconds < 86400:
        return f"{seconds // 3600} год {(seconds % 3600) // 60} хв тому"
    return f"{seconds // 86400} дн тому"


def format_time(timestamp: int, now: int) -> str:
    return f"{format_moment(timestamp, now)} ({format_ago(now - timestamp)})"


def format_status_message(reading: Reading, tracker: StateTracker, now: int) -> str:
    """Відповідь на кнопку «Перевірити». Стан не змінює."""
    if not reading.known:
        if reading.internet is False:
            lines = ["⚠️ У будинку зараз немає інтернету, тому стан світла невідомий."]
        else:
            lines = ["⚠️ Не вдалося отримати дані від Shelly. Спробуйте пізніше."]
        if tracker.known and tracker.since is not None:
            state = "світло є" if tracker.has_light else "світла немає"
            lines.append(f"\nОстанній відомий стан: {state} з {format_moment(tracker.since, now)}.")
        return "\n".join(lines)

    confirmed = (tracker.known and tracker.since is not None
                 and tracker.has_light == reading.has_light)
    voltage = f"{reading.voltage:.1f} В" if reading.voltage is not None else "–"
    frequency = f"{reading.frequency:.1f} Гц" if reading.frequency is not None else "–"

    if reading.has_light:
        lines = ["✅ СВІТЛО Є", "", f"🔌 Напруга: {voltage}", f"〰️  Частота: {frequency}"]
        if confirmed:
            lines.append(f"⏱️ Світло є вже: {format_duration(now - tracker.since)}")
    elif reading.online is False and reading.internet:
        lines = ["❌ СВІТЛА НЕМАЄ", "",
                 "📡 Пристрій не на зв'язку, а інтернет у будинку працює — отже світла немає."]
        if confirmed:
            lines.append(f"⏱️ Час відключення: {format_duration(now - tracker.since)}")
    elif reading.online is False:
        lines = ["❌ СВІТЛА НЕМАЄ (ймовірно)", "",
                 "📡 Пристрій не на зв'язку — так буває при відключенні світла "
                 "або інтернету в будинку."]
        if confirmed:
            lines.append(f"⏱️ Не на зв'язку: {format_duration(now - tracker.since)}")
    else:
        lines = ["❌ СВІТЛА НЕМАЄ", ""]
        if confirmed:
            lines.append(f"⏱️ Час відключення: {format_duration(now - tracker.since)}")
        if reading.voltage is not None:
            lines.append(f"🔌 Напруга: {voltage}")

    lines.append(f"🕒 Дані: {format_time(reading.device_time or reading.fetched_at, now)}")
    return "\n".join(lines)


def format_alert(change: StateChange) -> str:
    """Текст сповіщення для підписників."""
    if change.kind == LOST:
        return "🔴 СВІТЛО ПРОПАЛО!"

    if change.kind == INTERNET:
        message = "ℹ️ Світло не зникало — у будинку пропадав інтернет"
        if change.previous_duration is not None:
            message += f" ({format_duration(change.previous_duration)})"
        return message + ".\nПопереднє сповіщення про відключення було помилковим."

    message = "🟢 СВІТЛО ПОВЕРНУЛОСЬ!"
    if change.previous_duration is not None:
        message += f"\n\n⏱️ Час відключення: {format_duration(change.previous_duration)}"
    if change.returned_at is not None and change.noticed_at - change.returned_at > LATE_RETURN_NOTE:
        message += (f"\n💡 Світло з'явилося {format_moment(change.returned_at, change.noticed_at)}, "
                    "інтернет у будинку — пізніше.")
    return message
