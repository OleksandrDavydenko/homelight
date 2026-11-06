"""Отримання стану світла з Shelly Cloud і логіка підтвердження змін стану.

Ключова ідея: «пристрій офлайн» і «не вдалося отримати дані» — різні речі.
Shelly живиться від мережі, тому коли світла немає, він зникає з мережі —
це надійна ознака відключення. А помилка мережі чи Shelly Cloud означає лише,
що стан невідомий, і не повинна породжувати сповіщень.
"""
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from config import SHELLY_AUTH_KEY, SHELLY_BASE_URL, TARGET_MAC, TIMEZONE

logger = logging.getLogger(__name__)

MIN_VOLTAGE = 100          # В; нижче — вважаємо, що світла немає
INITIAL_RETRY_DELAY = 2    # с
MAX_RETRY_DELAY = 30       # с
CONFIRMATIONS = 2          # скільки читань поспіль потрібно, щоб підтвердити зміну стану
FUTURE_TOLERANCE = 120     # с; час пристрою «з майбутнього» вважаємо недостовірним


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
    error: str | None = None

    @property
    def known(self) -> bool:
        return self.has_light is not None


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


def _extract_switch_data(device: dict) -> tuple[float | None, float | None]:
    """Напруга й частота з першого компонента switch* пристрою."""
    for key, value in device.items():
        if str(key).startswith("switch") and isinstance(value, dict):
            return _to_float(value.get("voltage")), _to_float(value.get("freq"))
    return None, None


class LightChecker:
    def __init__(self, auth_key=SHELLY_AUTH_KEY, base_url=SHELLY_BASE_URL,
                 target_mac=TARGET_MAC, session=None):
        self.auth_key = auth_key
        self.base_url = base_url
        self.target_mac = target_mac
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._last: Reading | None = None

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

        voltage, frequency = _extract_switch_data(device)
        if voltage is None:
            logger.warning("Пристрій онлайн, але не передав напругу")
            return Reading(has_light=None, fetched_at=now, online=True, device_time=device_time,
                           error="Пристрій не передав напругу")

        has_light = voltage > MIN_VOLTAGE
        logger.info("Напруга %.1f В — світло %s", voltage, "Є" if has_light else "НЕМАЄ")
        return Reading(has_light=has_light, fetched_at=now, voltage=voltage,
                       frequency=frequency, online=True, device_time=device_time)

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
            except Exception as e:
                logger.error("Не вдалося отримати стан світла: %s", e)
                return Reading(has_light=None, fetched_at=int(time.time()), error=str(e))
            if reading.known:
                self._last = reading
            return reading


@dataclass(frozen=True)
class StateChange:
    has_light: bool
    since: int
    previous_duration: int | None = None  # скільки тривав попередній стан
    initial: bool = False                 # перша ініціалізація стану — без сповіщення


class StateTracker:
    """Підтверджений стан світла. Чиста логіка, без мережі й БД.

    Зміна стану підтверджується лише після `confirmations` читань поспіль,
    що відрізняються від поточного стану. Невідомі читання (None) ігноруються.
    """

    def __init__(self, has_light: bool | None = None, since: int | None = None,
                 confirmations: int = CONFIRMATIONS):
        if confirmations < 1:
            raise ValueError("confirmations must be >= 1")
        self.has_light = has_light
        self.since = since
        self.confirmations = confirmations
        self._pending_count = 0
        self._pending_since: int | None = None

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

    def observe(self, has_light: bool | None, now: int) -> StateChange | None:
        if has_light is None:
            return None

        if self.has_light is None:
            self.has_light, self.since = has_light, now
            self._reset_pending()
            return StateChange(has_light=has_light, since=now, initial=True)

        if has_light == self.has_light:
            self._reset_pending()
            return None

        if self._pending_count == 0:
            self._pending_since = now
        self._pending_count += 1
        if self._pending_count < self.confirmations:
            return None

        started = self._pending_since
        previous_duration = started - self.since if self.since is not None else None
        self.has_light, self.since = has_light, started
        self._reset_pending()
        return StateChange(has_light=has_light, since=started, previous_duration=previous_duration)


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
    if change.has_light:
        message = "🟢 СВІТЛО ПОВЕРНУЛОСЬ!"
        if change.previous_duration is not None:
            message += f"\n\n⏱️ Час відключення: {format_duration(change.previous_duration)}"
        return message
    return "🔴 СВІТЛО ПРОПАЛО!"
