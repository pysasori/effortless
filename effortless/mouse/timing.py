"""
Високоточні затримки та "людські" розподіли часу.

Навіщо окремий модуль: стандартний `time.sleep()` у Windows спирається на
системний планувальник, крок якого за замовчуванням ~15.6 мс. Тобто запит
`time.sleep(0.008)` реально спить ~15.6 мс, а `sleep(0.020)` — ~31.2 мс.
Наслідок для емуляції миші фатальний: скільки б випадковості не було в
аргументах, гістограма реальних інтервалів між подіями має піки, кратні
15.6 мс. Жива миша шле події рівномірним потоком (125 Гц = 8 мс, 1000 Гц =
1 мс) з мікроджитером у частки мілісекунди, і ця різниця видно на будь-якому
логері подій за пару секунд.

Тут це лікується двома речами:
  * `timeBeginPeriod(1)` — просимо в системи 1-мс роздільність таймера;
  * гібридний sleep — спимо лише "грубу" частину, а останні ~1.5 мс
    докручуємо активним очікуванням по `perf_counter`.
"""
import atexit
import ctypes
import random
import time
from typing import Optional

__all__ = [
    "enable_high_resolution_timer",
    "precise_sleep",
    "sleep_until",
    "lognormal_time",
    "gauss_clamped",
]

_winmm: Optional[ctypes.WinDLL]
try:
    _winmm = ctypes.WinDLL("winmm")
except (OSError, AttributeError):  # не Windows — деградуємо до звичайного sleep
    _winmm = None

_timer_period_active = False

# Наскільки рано виходимо зі `sleep` і добираємо решту спінами. 1.5 мс —
# компроміс: менше — ризик проспати, більше — марно гріємо ядро.
_SPIN_MARGIN = 0.0015


def enable_high_resolution_timer() -> bool:
    """Вмикає 1-мс роздільність системного таймера на весь час життя процесу.

    Ідемпотентна: повторні виклики нічого не роблять. Парний `timeEndPeriod`
    реєструється в `atexit` — без нього підвищена роздільність лишається
    глобальним станом системи і після завершення процесу.

    Returns:
        bool: True, якщо роздільність вдалося підвищити.
    """
    global _timer_period_active

    if _timer_period_active:
        return True
    if _winmm is None:
        return False

    if _winmm.timeBeginPeriod(1) != 0:  # TIMERR_NOERROR == 0
        return False

    _timer_period_active = True
    atexit.register(_disable_high_resolution_timer)
    return True


def _disable_high_resolution_timer() -> None:
    global _timer_period_active

    if _timer_period_active and _winmm is not None:
        _winmm.timeEndPeriod(1)
        _timer_period_active = False


def precise_sleep(seconds: float) -> None:
    """Спить `seconds` з похибкою в частки мілісекунди.

    Грубу частину віддаємо планувальнику (щоб не палити CPU), а хвіст
    докручуємо активним очікуванням.
    """
    if seconds <= 0:
        return
    sleep_until(time.perf_counter() + seconds)


def sleep_until(deadline: float) -> None:
    """Чекає до моменту `deadline` за шкалою `time.perf_counter()`.

    Саме ця форма потрібна для стабільної частоти подій: якщо рахувати
    затримки як `sleep(період)`, похибка кожного кроку накопичується і
    середня частота "пливе". Прив'язка до абсолютних міток тримає потік
    подій рівним, як у справжнього опитування пристрою.
    """
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        return

    if remaining > _SPIN_MARGIN:
        time.sleep(remaining - _SPIN_MARGIN)

    # Активне очікування хвоста: коротке, тому на завантаження CPU не впливає.
    while time.perf_counter() < deadline:
        pass


def lognormal_time(median: float, sigma: float, lo: float, hi: float) -> float:
    """Логнормальна тривалість у секундах, обрізана в [lo, hi].

    Людські інтервали (час утримання кнопки, реакція, паузи між діями)
    розподілені логнормально: щільний пік біля медіани і довгий хвіст
    управо — зрідка людина "залипає". `random.uniform`, який зазвичай
    ставлять у ботах, дає рівне плато замість піка, і це видно на гістограмі
    вже після кількох десятків подій.

    Args:
        median: Медіана розподілу в секундах (пік щільності зсунутий лівіше).
        sigma: Сигма логарифма; 0.2 — вузько, 0.5 — помітний хвіст.
        lo, hi: Межі обрізання.
    """
    return min(hi, max(lo, random.lognormvariate(0.0, sigma) * median))


def gauss_clamped(mu: float, sigma: float, lo: float, hi: float) -> float:
    """Нормальний розподіл, обрізаний у [lo, hi]."""
    return min(hi, max(lo, random.gauss(mu, sigma)))
