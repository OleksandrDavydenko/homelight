"""Робота на резерві — повідомлення лише власнику. Чиста логіка, без мережі й БД.

Модуль у щитку (TARGET_MAC) стоїть на лінії міста до перемикача «місто — резерв»,
тож показує, чи є світло від міста (його стан веде StateTracker). Розетка
(PLUG_MAC) стоїть у квартирі після перемикача: живлення в ній є і від міста,
і від резерву.

Перемикач ручний: кожне перемикання знеструмлює розетку, і вона
перезавантажується. Тому за моментом її увімкнення (boot_time) видно джерело:
- міста немає, а розетка під напругою й увімкнулася під час відключення — резерв;
- місто повернулося, а розетка увімкнулася раніше за нього — ще резерв;
- розетка перезавантажилася вже після повернення міста — перейшли на місто.

Резерв ніколи не починається, поки світло від міста є: так перезапуск бота
не дасть хибних повідомлень.

Коли місто повертається, а ви на резерв не переходили, модуль і розетка
вмикаються одночасно, але на зв'язок модуль може вийти пізніше. Щоб це не
виглядало як резерв, резерв зараховується, лише коли розетка під напругою вже
START_CONFIRM секунд, а модуль весь цей час не на зв'язку. Якщо модуль не
виходив на зв'язок ще довше, то коли він з'явиться, видно, що він увімкнувся
разом із розеткою, — тоді бот повідомляє, що резерву не було.
"""
import logging
from dataclasses import dataclass

from light_checker import OFFLINE_CONFIRM_SECONDS, Reading, format_duration, format_moment

logger = logging.getLogger(__name__)

# с; бот помічає зміни із затримкою (до інтервалу перевірки), тож момент
# відключення чи повернення міста відомий з такою точністю
BOOT_TOLERANCE = 120
SAME_BOOT_TOLERANCE = 60   # с; той самий запуск розетки (похибка годинників)
OFFLINE_CONFIRM = OFFLINE_CONFIRM_SECONDS  # с без живлення розетки, після яких резерв вимкнено
START_CONFIRM = OFFLINE_CONFIRM_SECONDS    # с під напругою без міста, після яких це резерв

STARTED = "started"                    # перейшли на резерв
CITY_BACK = "city_back"                # місто повернулося, а ви ще на резерві
SWITCHED_TO_CITY = "switched_to_city"  # перейшли з резерву на місто
STOPPED = "stopped"                    # резерв вимкнено (розетка без живлення)
RESUMED = "resumed"                    # резерв не вимикався — пропадав зв'язок
BOILER_ON = "boiler_on"                # бойлер увімкнувся на резерві
NOT_RESERVE = "not_reserve"            # резерву не було — місто повернулося, модуль довше виходив на зв'язок


@dataclass(frozen=True)
class ReserveEvent:
    kind: str
    since: int                          # коли увімкнено резерв
    at: int                             # момент події; для SWITCHED_TO_CITY/STOPPED — кінець резерву
    city_has_light: bool | None = None
    city_since: int | None = None       # з якого моменту діє стан світла від міста
    boiler_on: bool | None = None       # чи ввімкнене реле розетки (бойлер)

    @property
    def duration(self) -> int:
        return self.at - self.since


class ReserveTracker:
    def __init__(self, on: bool = False, since: int | None = None, plug_boot: int | None = None,
                 last_seen: int | None = None, city_back_noticed: bool = False,
                 boiler_warned: bool = False, last_session: dict | None = None,
                 start_boot: int | None = None):
        self.on = on
        self.since = since                    # коли увімкнено резерв
        self.plug_boot = plug_boot            # останній запуск розетки на резерві
        self.start_boot = start_boot          # запуск розетки, з якого почався резерв
        self.last_seen = last_seen            # коли розетку востаннє бачили під напругою
        self.city_back_noticed = city_back_noticed
        self.boiler_warned = boiler_warned
        self.last_session = last_session      # останній резерв, що вимкнувся через зникнення розетки
        self._offline_since: int | None = None
        self._powered_since: int | None = None  # коли розетку вперше побачили під напругою без міста
        self._no_reboot_logged = False

    @classmethod
    def from_dict(cls, data: dict | None) -> "ReserveTracker":
        if not data:
            return cls()
        session = data.get("last_session")
        return cls(on=bool(data.get("on")), since=data.get("since"),
                   plug_boot=data.get("plug_boot"), last_seen=data.get("last_seen"),
                   city_back_noticed=bool(data.get("city_back_noticed")),
                   boiler_warned=bool(data.get("boiler_warned")),
                   last_session=session if isinstance(session, dict) else None,
                   start_boot=data.get("start_boot"))

    def to_dict(self) -> dict:
        return {"on": self.on, "since": self.since, "plug_boot": self.plug_boot,
                "start_boot": self.start_boot, "last_seen": self.last_seen,
                "city_back_noticed": self.city_back_noticed,
                "boiler_warned": self.boiler_warned, "last_session": self.last_session}

    def observe(self, plug: Reading | None, city_has_light: bool | None,
                city_since: int | None, internet: bool | None, now: int,
                city_online: bool | None = None) -> list[ReserveEvent]:
        """plug — читання розетки; city_* — підтверджений стан світла від міста;
        internet=False — вдома немає інтернету (тоді зникнення розетки нічого не означає);
        city_online — чи модуль на лінії міста на зв'язку просто зараз (тоді місто є,
        навіть якщо бот ще не підтвердив його повернення).
        """
        if plug is None or city_has_light is None:
            return []
        if plug.online is True and plug.has_light is True:
            return self._powered(plug, city_has_light, city_since, now, city_online)
        self._powered_since = None
        if plug.online is False or plug.has_light is False:
            return self._unpowered(city_has_light, city_since, internet, now)
        return []  # онлайн, але без даних про напругу

    # ---------- Розетка під напругою ----------

    def _powered(self, plug: Reading, city_has_light: bool, city_since: int | None,
                 now: int, city_online: bool | None) -> list[ReserveEvent]:
        self._offline_since = None
        boot = plug.boot_time
        events: list[ReserveEvent] = []

        if not self.on:
            if self._resumable(boot):
                session = self.last_session
                self._begin(session["since"], boot, now)
                self.start_boot = session.get("start_boot", self.start_boot)
                events.append(ReserveEvent(RESUMED, self.since, at=now,
                                           city_has_light=city_has_light, city_since=city_since))
            else:
                self.last_session = None
                if city_has_light or city_online:
                    # Модуль на лінії міста на зв'язку — місто є, це не резерв
                    self._powered_since = None
                    return []
                if boot is not None and city_since is not None and boot < city_since - BOOT_TOLERANCE:
                    if not self._no_reboot_logged:
                        self._no_reboot_logged = True
                        logger.warning("Світла від міста немає, а розетка не перезавантажувалась — "
                                       "резерв не фіксую (перевірте, чи модуль у щитку на зв'язку)")
                    return []
                if self._powered_since is None:
                    self._powered_since = now
                since = boot if boot is not None and boot <= now else self._powered_since
                if now - since < START_CONFIRM:
                    # Можливо, місто щойно повернулося, а модуль ще виходить на зв'язок
                    return []
                self._begin(since, boot, now)
                self.boiler_warned = plug.output is True
                return [ReserveEvent(STARTED, since, at=now, city_has_light=False,
                                     city_since=city_since, boiler_on=plug.output)]

        self._no_reboot_logged = False
        self.last_seen = now
        if city_has_light:
            if boot is not None and city_since is not None and boot >= city_since - BOOT_TOLERANCE:
                if self._started_with_city(boot, city_since):
                    # Розетка не перезавантажувалась і ввімкнулася разом із модулем міста:
                    # місто повернулося, модуль просто довше виходив на зв'язок
                    events.append(ReserveEvent(NOT_RESERVE, self.since, at=now,
                                               city_has_light=True, city_since=city_since))
                else:
                    # Розетка перезавантажилась, коли місто вже було, — перейшли на місто
                    end = min(max(boot, self.since), now)
                    events.append(ReserveEvent(SWITCHED_TO_CITY, self.since, at=end,
                                               city_has_light=True, city_since=city_since))
                self._end()
                return events
            if not self.city_back_noticed:
                self.city_back_noticed = True
                events.append(ReserveEvent(CITY_BACK, self.since, at=now,
                                           city_has_light=True, city_since=city_since))
        elif boot is not None:
            # Резерв перезапускався (наприклад, генератор) — це той самий резерв
            self.plug_boot = boot

        if plug.output is True and not self.boiler_warned:
            self.boiler_warned = True
            events.append(ReserveEvent(BOILER_ON, self.since, at=now, city_has_light=city_has_light,
                                       city_since=city_since, boiler_on=True))
        return events

    # ---------- Розетка без живлення чи без зв'язку ----------

    def _unpowered(self, city_has_light: bool, city_since: int | None, internet: bool | None,
                   now: int) -> list[ReserveEvent]:
        if not self.on or internet is False:
            return []
        if self._offline_since is None:
            self._offline_since = now
        if now - self._offline_since < OFFLINE_CONFIRM:
            return []

        end = max(self.last_seen or self._offline_since, self.since)
        event = ReserveEvent(STOPPED, self.since, at=end, city_has_light=city_has_light,
                             city_since=city_since)
        session = {"since": self.since, "plug_boot": self.plug_boot,
                   "start_boot": self.start_boot, "ended": end}
        self._end()
        self.last_session = session
        return [event]

    # ---------- Допоміжне ----------

    def _resumable(self, boot: int | None) -> bool:
        """Розетка повернулася без перезавантаження після «вимкнення» резерву."""
        session = self.last_session
        return (session is not None and boot is not None and session.get("plug_boot") is not None
                and session.get("since") is not None
                and abs(boot - session["plug_boot"]) <= SAME_BOOT_TOLERANCE)

    def _started_with_city(self, boot: int, city_since: int) -> bool:
        """Розетка не перезавантажувалась від «початку резерву», і той запуск
        збігся з поверненням міста, — отже, резерву не було."""
        return (self.start_boot is not None
                and abs(boot - self.start_boot) <= SAME_BOOT_TOLERANCE
                and abs(self.start_boot - city_since) <= BOOT_TOLERANCE)

    def _begin(self, since: int, boot: int | None, now: int) -> None:
        self.on, self.since, self.plug_boot, self.last_seen = True, since, boot, now
        self.start_boot = boot
        self.city_back_noticed = self.boiler_warned = False
        self.last_session = None
        self._offline_since = self._powered_since = None

    def _end(self) -> None:
        self.on, self.since, self.plug_boot, self.last_seen = False, None, None, None
        self.start_boot = None
        self.city_back_noticed = self.boiler_warned = False
        self._offline_since = self._powered_since = None


# ---------- Тексти повідомлень ----------

def _period(event: ReserveEvent, now: int) -> str:
    return (f"Увімкнено: {format_moment(event.since, now)}\n"
            f"Вимкнено: {format_moment(event.at, now)}\n"
            f"На резерві: {format_duration(event.duration)}")


def format_reserve_event(event: ReserveEvent, now: int) -> str:
    """Текст повідомлення власнику. Без назв пристроїв."""
    if event.kind == STARTED:
        lines = ["🔋 Ви перейшли на резерв", "",
                 f"Резерв увімкнено: {format_moment(event.since, now)}"]
        if event.city_since is not None:
            lines.append(f"Світла від міста немає з {format_moment(event.city_since, now)}.")
        if event.boiler_on is False:
            lines.append("♨️ Бойлер вимкнено — на резерві він не вмикається.")
        elif event.boiler_on is True:
            lines.append("⚠️ Бойлер увімкнено! На резерві він має бути вимкнений — "
                         "вимкніть його в застосунку.")
        return "\n".join(lines)

    if event.kind == CITY_BACK:
        return ("🟢 Світло від міста повернулося, а ви ще на резерві — можна перемикатися.\n"
                f"На резерві вже {format_duration(now - event.since)} "
                f"(з {format_moment(event.since, now)}).")

    if event.kind == SWITCHED_TO_CITY:
        return "✅ Ви перейшли з резерву на світло від міста\n\n" + _period(event, now)

    if event.kind == STOPPED:
        city = "Світло від міста є." if event.city_has_light else "Світла від міста досі немає."
        return f"🪫 Резерв вимкнено\n\n{_period(event, now)}\n{city}"

    if event.kind == RESUMED:
        return ("ℹ️ Резерв не вимикався — пропадав зв'язок.\n"
                f"На резерві з {format_moment(event.since, now)} "
                f"({format_duration(now - event.since)}).")

    if event.kind == NOT_RESERVE:
        return ("ℹ️ Ви не переходили на резерв — світло від міста повернулося, "
                "просто датчик світла довше виходив на зв'язок.\n"
                "Попереднє повідомлення про резерв було помилковим.")

    if event.kind == BOILER_ON:
        return ("⚠️ Бойлер увімкнувся, хоча ви на резерві.\n"
                "Схоже, скрипт бойлера на розетці не працює — вимкніть бойлер у застосунку.")

    raise ValueError(f"Невідома подія резерву: {event.kind}")


def format_reserve_status(tracker: ReserveTracker, now: int) -> str | None:
    """Рядок для власника у відповіді «Перевірити»; None — не на резерві."""
    if not tracker.on or tracker.since is None:
        return None
    return (f"🔋 Ви на резерві з {format_moment(tracker.since, now)} "
            f"({format_duration(now - tracker.since)}).")
