"""
Годинник опитування — спільна часова сітка для всіх подій миші.

Навіщо окремий модуль: у фізичній миші є **один** генератор. Мікросхема
опитує сенсор і кнопки з однаковою періодичністю і пакує все — зміщення,
стан кнопок, колесо — в один HID-звіт. Тому в реальному потоці подій
натискання ніколи не «висить» між тактами руху: воно лягає на ту саму
сітку, бо приїхало тим самим звітом.

Якщо ж рух вирівняний по 125 Гц, а `down`/`up` шлються тоді, коли
доспала логіка програми, то таймстемпи кнопок і таймстемпи руху виявляються
з різних годинників. Кожен потік окремо виглядає нормально — а от разом
вони не сходяться, і саме це помітно тому, хто дивиться на них разом.

Годинник вільно біжить від створення профілю і не перезапускається між
діями: пауза між рухом і кліком не зсуває фазу, як не зсуває її і пауза
в роботі живої людини.
"""
import math
import random
import time
from typing import Optional

from .profile import OperatorProfile, get_profile
from .timing import sleep_until

# Понад стільки тактів поспіль сітку поштучно не розкручуємо.
_MAX_CATCHUP_TICKS = 4000

__all__ = ["PollingClock", "get_clock", "reset_clock"]


class PollingClock:
    """Вільно біжуча сітка тактів із частотою опитування миші."""

    # Мінімальний проміжок між сусідніми тактами, у частках періоду.
    #
    # Джитер накладається на кожен такт НЕЗАЛЕЖНО, тож за великого розкиду
    # сусідні мітки можуть зійтись упритул або й помінятись місцями. Розкид
    # 0.17, який дало калібрування по запису через віддалений доступ, дає
    # приблизно одну таку пару на тисячу; дозволена калібруванням стеля 0.5
    # дає майже кожну восьму. Тоді дві події вилітають одна за одною через
    # мікросекунди — рівно той артефакт «двох звітів в одну мить», заради
    # усунення якого цей модуль і існує.
    _MIN_GAP_RATIO = 0.45

    # Яка частка ДИСПЕРСІЇ інтервалу припадає на повільне блукання періоду,
    # а не на незалежний шум окремого звіту.
    #
    # Підібрано так, щоб автокореляція інтервалів на лагу 1 вийшла близько
    # +0.2 — стільки заміряно в записі живої руки через віддалений доступ.
    _WALK_SHARE = 0.25

    # Час загасання блукання періоду. Має бути в кілька десятків тактів:
    # надто коротке вироджується в незалежний шум, надто довге робить період
    # практично сталим.
    _WALK_TAU = 0.25

    def __init__(self, interval: float, jitter_ratio: float) -> None:
        self.interval = interval
        # Джитер у частках періоду: кварц не ідеальний, але й не «гуляє»
        # на десятки відсотків.
        self.jitter = jitter_ratio * interval
        self.epoch = time.perf_counter()

        # Розлад кварца миші відносно годинника комп'ютера. У реальних
        # пристроїв це десятки ppm, і за годину роботи виміряний період
        # помітно відрізняється від номінального. Без цього члена період
        # лишався б однаковим до дев'ятого знака всю сесію.
        self._skew = random.gauss(0.0, 4e-5)

        # Стаціонарний розкид блукання періоду і сила поштовху, що його
        # підтримує, разом дають задану частку дисперсії.
        walk_sd = self.jitter * math.sqrt(self._WALK_SHARE)
        self._WALK_DECAY = math.exp(-self.interval / self._WALK_TAU)
        self._walk_kick = walk_sd * math.sqrt(max(1e-12, 1.0 - self._WALK_DECAY ** 2))
        self._report_sigma = self.jitter * math.sqrt(1.0 - self._WALK_SHARE)

        self._period_dev = 0.0
        self._last_index: Optional[int] = None
        self._last_time = 0.0

    def index_at(self, moment: float) -> int:
        """Номер першого такту, не раніший за `moment`.

        Відлік ведеться від ОСТАННЬОГО виданого такту, а не від ідеальної
        сітки `epoch + index * interval`. Так і має бути: відколи мітки
        накопичуються тік за тіком, їхня шкала повільно відходить від
        ідеальної — за двадцять тисяч тактів набігає близько третини
        секунди. Поки `index_at` рахував по ідеальній сітці, дві функції
        одного годинника відповідали на різних шкалах: `time_of` повертав
        мітки в минулому, очікування миттєво завершувалось, і вся
        траєкторія вилітала пачкою подій, після чого регулятор працював по
        застарілій позиції курсора й промахувався на сотні пікселів.
        """
        if self._last_index is None:
            return math.ceil((moment - self.epoch) / self.interval - 1e-9)
        return self._last_index + math.ceil(
            (moment - self._last_time) / self.interval - 1e-9
        )

    def time_of(self, index: int) -> float:
        """Час такту з номером `index`, зі своїм джитером.

        Мітка запам'ятовується: повторний запит того самого такту має дати
        той самий час, інакше «такт» перестає бути точкою на осі часу.
        Сусідній такт не може підійти до попереднього ближче ніж на
        `_MIN_GAP_RATIO` періоду.
        """
        if index == self._last_index:
            return self._last_time

        if self._last_index is None:
            self._last_index = index
            self._last_time = self.epoch + index * self.interval
            return self._last_time

        # Мітки накопичуються тік за тіком, а не рахуються від сітки. Це і є
        # суть виправлення: коли шум додається до ГОТОВОЇ мітки, інтервал
        # дорівнює period + e_i - e_{i-1}, і його автокореляція на лагу 1
        # виходить рівно -0.5. Коли ж блукає сам ПЕРІОД, сусідні інтервали
        # схожі один на одного, і кореляція стає додатною — як у живої руки
        # через мережевий шлях, де заміряно +0.23.
        steps = index - self._last_index
        if steps <= 0:
            return self._last_time

        moment = self._last_time
        base = self.interval * (1.0 + self._skew)
        floor_gap = self.interval * self._MIN_GAP_RATIO

        for _ in range(min(steps, _MAX_CATCHUP_TICKS)):
            self._period_dev = self._period_dev * self._WALK_DECAY + random.gauss(
                0.0, self._walk_kick
            )
            gap = base + self._period_dev + random.gauss(0.0, self._report_sigma)
            moment += max(floor_gap, gap)

        # Дуже великий стрибок індексу (довга пауза між діями) не має сенсу
        # розкручувати поштучно.
        if steps > _MAX_CATCHUP_TICKS:
            moment += (steps - _MAX_CATCHUP_TICKS) * base

        self._last_index = index
        self._last_time = moment
        return moment

    def tick_at(self, moment: float) -> float:
        """Найближчий такт, не раніший за `moment`, зі своїм джитером."""
        return self.time_of(self.index_at(moment))

    def next_tick(self, not_before: Optional[float] = None) -> float:
        """Наступний такт від поточного моменту (або від `not_before`)."""
        return self.tick_at(not_before if not_before is not None else time.perf_counter())

    def wait_next(self, not_before: Optional[float] = None) -> float:
        """Чекає до наступного такту й повертає його час."""
        moment = self.next_tick(not_before)
        sleep_until(moment)
        return moment

    def wait_after(self, delay: float) -> float:
        """Чекає такт, найближчий до «зараз + delay».

        Саме так лягає відпускання кнопки: людина тримає її стільки,
        скільки тримає, але звіт про відпускання поїде найближчим тактом.
        """
        return self.wait_next(time.perf_counter() + delay)


_clock: Optional[PollingClock] = None
_clock_profile: Optional[OperatorProfile] = None


def get_clock(profile: Optional[OperatorProfile] = None) -> PollingClock:
    """Годинник поточної сесії. Пересоздається при зміні профілю."""
    global _clock, _clock_profile

    profile = profile or get_profile()
    if _clock is None or _clock_profile is not profile:
        _clock = PollingClock(profile.polling_interval, profile.polling_jitter)
        _clock_profile = profile
    return _clock


def reset_clock() -> None:
    """Скидає годинник — знадобиться в тестах."""
    global _clock, _clock_profile
    _clock = _clock_profile = None
