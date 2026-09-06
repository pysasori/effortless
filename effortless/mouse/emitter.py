"""
Відтворення траєкторії реальними подіями миші.

Фізична миша шле не координати, а "каунти" зміщення, до яких Windows додає
власну криву прискорення. Скільки пікселів вийде з N каунтів — залежить від
повзунка швидкості вказівника, від того, чи ввімкнена "підвищена точність",
і (якщо ввімкнена) від миттєвої швидкості руху. Передбачити це наперед
неможливо, тому позиціонування тут зроблене як стежачий регулятор:

  * **упереджувальна складова** — крок, який ми хочемо пройти за цей такт;
  * **зворотний зв'язок** — частина нев'язки між бажаною і фактичною
    позицією, зчитаною через `GetCursorPos`;
  * **адаптивний коефіцієнт** — поточна оцінка "пікселів на каунт",
    що уточнюється по фактично пройденій відстані.

Побічний ефект приємний: така схема сама себе виправляє при будь-яких
налаштуваннях системи, тобто нічого не треба читати з реєстру й моделювати
`SmoothMouseXCurve`.
"""
import math
import random
import time
from typing import Callable, Iterable, Optional, Tuple

from . import win_api
from .clock import get_clock
from .profile import OperatorProfile, get_profile
from .timing import enable_high_resolution_timer, sleep_until
from .transport import get_transport
from .trajectory import Sample

__all__ = ["emit_path", "reset_gain"]

# Множник швидкості вказівника для повзунка SPI_GETMOUSESPEED (1..20).
# Використовується лише як стартова оцінка — далі коефіцієнт уточнюється по
# факту, тому неточність таблиці нічого не ламає.
_SPEED_MULTIPLIER = {
    1: 1 / 32, 2: 1 / 16, 3: 1 / 8, 4: 2 / 8, 5: 3 / 8,
    6: 4 / 8, 7: 5 / 8, 8: 6 / 8, 9: 7 / 8, 10: 1.0,
    11: 1.25, 12: 1.5, 13: 1.75, 14: 2.0, 15: 2.25,
    16: 2.5, 17: 2.75, 18: 3.0, 19: 3.25, 20: 3.5,
}

# Частка нев'язки, що гаситься за один такт.
#
# Ефективний коефіцієнт петлі — це значення, помножене на відношення
# справжніх пікселів на каунт до оціненых. Поріг коливань для такої
# схеми — одиниця. З увімкненою «підвищеною точністю» (типове
# налаштування Windows) реальне відношення гуляє приблизно втричі
# залежно від миттєвої швидкості, тож 0.65 виводило перший рух свіжого
# процесу майже на межу дзвону. 0.45 лишає запас; ціною є трохи довший
# хвіст гасіння, який усе одно доїдає довідний цикл.
_FEEDBACK_GAIN = 0.45
_FEEDBACK_SPREAD = 0.10

# Нев'язка, меншу за яку регулятор не чіпає, px.
_DEAD_BAND_PX = 1.5

# Наскільки швидко переоцінюємо "пікселі на каунт".
_GAIN_SMOOTHING = 0.10
_GAIN_LIMITS = (0.05, 8.0)

# Мінімальний запас стелі швидкості на один такт: навіть найповільніший
# профіль має право зрушити курсор хоча б на кілька пікселів за подію,
# інакше довгий рух ніколи не дійде до цілі.
_MIN_STEP_PX = 4.0

# У скільки разів обмежувач зсуву щедріший за стелю швидкості профілю.
#
# Ці два пороги роблять різне, і плутати їх не можна. Стеля в `trajectory`
# формує рух: вона задає, наскільки швидко курсор ходить ЗАЗВИЧАЙ. Обмежувач
# тут — аварійний, він ловить лише промахи регулятора (у записах це були
# поодинокі події зі зсувом у сотні пікселів). Якщо поставити його впритул
# до стелі, він почне різати нормальний рух, і розподіл зсувів отримає
# рівну стінку на місці природного хвоста — а рівна стінка сама по собі
# ознака. Для порівняння: у записі живої руки 99-й перцентиль зсуву 51 px,
# але максимум 113 px, тобто хвіст удвічі довший за перцентиль.
_STEP_LIMIT_MARGIN = 2.0

_pixels_per_count: Optional[float] = None


def _initial_gain() -> float:
    """Стартова оцінка коефіцієнта за системними налаштуваннями."""
    try:
        return _SPEED_MULTIPLIER.get(win_api.get_pointer_speed(), 1.0)
    except OSError:
        return 1.0


def reset_gain() -> None:
    """Скидає оцінку коефіцієнта — після зміни налаштувань миші в системі."""
    global _pixels_per_count, _rejections
    _pixels_per_count = None
    _rejections = 0


# Наскільки фактичний зсув може перевищити очікуваний, перш ніж ми
# вважатимемо, що курсор рухали не ми. Оцінка «пікселів на каунт» рахується
# по МОДУЛЮ зсуву, тож стороннє втручання (людина взялася за мишу) завжди
# читається як «пройшли багато» і може лише завищити коефіцієнт — а завищений
# коефіцієнт означає недобір, тобто рухи почнуть тихо недоїжджати до цілі.
_INTERFERENCE_RATIO = 3.0


# Скільки поспіль «завеликих» спостережень має прийти, перш ніж ми визнаємо,
# що завелика насправді ОЦІНКА, а не зсув.
#
# Без цього лічильника захист від втручання перетворюється на пастку: якщо
# справжній масштаб утричі більший за стартову оцінку (транспорт не SendInput,
# драйвер миші зі своїм множником, змінений повзунок швидкості), то кожне
# спостереження виглядає «завеликим», кожне відкидається, оцінка не рухається
# ніколи — і всі рухи до кінця сесії промахуються на сотні пікселів.
_INTERFERENCE_PATIENCE = 4

_rejections = 0


def _update_gain(moved_px: float, sent_counts: float) -> None:
    """Уточнює оцінку "пікселів на каунт" по фактично пройденій відстані."""
    global _pixels_per_count, _rejections

    # Дрібні кроки не інформативні: там усе з'їдає округлення до пікселя.
    if sent_counts < 2.0 or moved_px <= 0.0:
        return

    # Зсув, набагато більший за очікуваний, — найімовірніше не наша подія:
    # хтось узявся за фізичну мишу. Але якщо таке повторюється поспіль, то
    # помиляється оцінка, і тримати її стає гірше, ніж прийняти дані.
    if moved_px > sent_counts * _pixels_per_count * _INTERFERENCE_RATIO:
        _rejections += 1
        if _rejections < _INTERFERENCE_PATIENCE:
            return

    _rejections = 0

    observed = moved_px / sent_counts
    if not math.isfinite(observed):
        return

    observed = min(_GAIN_LIMITS[1], max(_GAIN_LIMITS[0], observed))
    _pixels_per_count += _GAIN_SMOOTHING * (observed - _pixels_per_count)


def _clamp_step(dx: float, dy: float, limit: float) -> Tuple[float, float]:
    """Обрізає зсув за такт до `limit` пікселів, зберігаючи напрямок."""
    magnitude = math.hypot(dx, dy)
    if magnitude <= limit or magnitude == 0.0:
        return dx, dy
    scale = limit / magnitude
    return dx * scale, dy * scale


def emit_path(
    samples: Iterable[Sample],
    profile: Optional[OperatorProfile] = None,
    settle: bool = True,
    on_tick: Optional[Callable[[int, int, Sample], None]] = None,
) -> Tuple[int, int]:
    """Програє траєкторію подіями відносного руху в реальному часі.

    Args:
        samples: Семпли з `trajectory.generate_path`.
        profile: Профіль оператора; за замовчуванням — профіль сесії.
        settle: Чи дотягувати курсор у кінцеву точку. Вимикається для
            холостого тремору: там кінцева точка не ціль, а випадкова
            фаза шуму, і "виправляти" її нема сенсу.

    Returns:
        Tuple[int, int]: Фактичні координати курсора після руху.
    """
    global _pixels_per_count

    profile = profile or get_profile()
    samples = list(samples)
    if not samples:
        return win_api.get_cursor_pos()

    # Без 1-мс таймера всі затримки злипнуться в кратні ~15.6 мс, і рівний
    # потік подій, заради якого все й робиться, розсиплеться.
    enable_high_resolution_timer()

    if _pixels_per_count is None:
        _pixels_per_count = _initial_gain()

    clock = get_clock(profile)
    transport = get_transport()

    started = time.perf_counter()
    # Кожен семпл лягає на такт спільного годинника, а не на власний дедлайн:
    # у фізичної миші рух і кнопки їдуть одним і тим самим HID-звітом, тож
    # і таймстемпи в них мають бути з одного генератора.
    tick = clock.index_at(started)

    prev_pos = win_api.get_cursor_pos()
    prev_counts = 0.0
    prev_target = (float(prev_pos[0]), float(prev_pos[1]))

    # Залишок субпікселя: без нього дрібні кроки округлялися б до нуля і
    # повільний рух просто зупинявся б.
    residual_x = residual_y = 0.0

    # Стеля зсуву на одну подію. Регулятор при збитій оцінці коефіцієнта
    # (різка зміна кривої прискорення, втручання фізичної миші) може
    # спробувати погасити всю нев'язку одним стрибком — у записах це давало
    # окремі події зі зсувом у сотні пікселів, тоді як у живої руки максимум
    # близько тридцяти. Надлишок не викидається, а переноситься на наступні
    # такти: курсор дійде куди треба, просто не за одну подію.
    step_limit = max(
        _MIN_STEP_PX,
        profile.max_velocity * profile.polling_interval * _STEP_LIMIT_MARGIN,
    )

    # Коефіцієнт петлі розігрується на кожен рух. Сталий коефіцієнт означає
    # сталий полюс: будь-яке збурення гасне як 0.45^n у КОЖНОМУ русі кожної
    # сесії, і підгонка AR(1) до залишку траєкторії повертала одне й те саме
    # число. У руки єдиного полюса немає.
    feedback = min(0.70, max(0.25, random.gauss(_FEEDBACK_GAIN, _FEEDBACK_SPREAD)))

    total = len(samples)
    for index, sample in enumerate(samples):
        # Не менше одного такту на подію: два звіти в одну мить фізична
        # миша видати не може.
        tick = max(tick + 1, clock.index_at(started + sample.t))

        # Якщо ми відстали від розкладу (пауза складальника сміття, чужий
        # процес забрав ядро), то без цієї гілки sleep_until миттєво
        # повертався б для кожного простроченого такту й вистрілював чергою
        # подій майже без проміжків. Замість цього перескакуємо на поточний
        # момент: краще втратити кілька семплів, ніж видати пачку.
        deadline = clock.time_of(tick)
        now = time.perf_counter()
        if deadline < now - clock.interval:
            # max(), а не присвоєння: номер такту мусить лишатись зростаючим,
            # інакше годинник поверне стару мітку й дві події вилетять
            # з проміжком у мікросекунди.
            tick = max(tick, clock.index_at(now))
            deadline = clock.time_of(tick)
        sleep_until(deadline)

        pos = win_api.get_cursor_pos()

        # Оцінку коефіцієнта уточнюємо по попередньому такту: подія вводу
        # обробляється асинхронно, тож читати позицію одразу після SendInput
        # немає сенсу — до цього моменту вона вже точно застосована.
        if prev_counts:
            _update_gain(math.hypot(pos[0] - prev_pos[0], pos[1] - prev_pos[1]), prev_counts)

        # Упередження (куди хочемо зрушити за такт) + гасіння нев'язки.
        feed_x = sample.x - prev_target[0]
        feed_y = sample.y - prev_target[1]
        error_x = sample.x - pos[0]
        error_y = sample.y - pos[1]

        # Мертва зона: нев'язку менше ніж на піксель-півтора не виправляємо.
        # Без неї регулятор після кожного перельоту на 1 px одразу давав
        # -1 px наступним тактом — мікрозсуви йшли парами з протилежним
        # знаком, і знакова автокореляція зсувів була різко від'ємною.
        if math.hypot(error_x, error_y) < _DEAD_BAND_PX:
            error_x = error_y = 0.0

        want_x = feed_x + feedback * error_x
        want_y = feed_y + feedback * error_y

        want_x, want_y = _clamp_step(want_x, want_y, step_limit)

        counts_fx = want_x / _pixels_per_count + residual_x
        counts_fy = want_y / _pixels_per_count + residual_y
        counts_x = int(round(counts_fx))
        counts_y = int(round(counts_fy))
        residual_x = counts_fx - counts_x
        residual_y = counts_fy - counts_y

        transport.move(counts_x, counts_y)

        if on_tick is not None:
            on_tick(index, total, sample)

        prev_pos = pos
        prev_counts = math.hypot(counts_x, counts_y)
        prev_target = (sample.x, sample.y)

    # Дотягування: якщо після траєкторії лишилась нев'язка (різко змінилась
    # крива прискорення, втрутилась фізична миша), гасимо її кількома
    # короткими кроками — так само, як людина довідним рухом.
    final_x, final_y = samples[-1].x, samples[-1].y
    for _ in range(4 if settle else 0):
        pos = win_api.get_cursor_pos()
        error_x, error_y = final_x - pos[0], final_y - pos[1]
        if math.hypot(error_x, error_y) < 1.0:
            break
        error_x, error_y = _clamp_step(error_x, error_y, step_limit)
        transport.move(
            int(round(error_x / _pixels_per_count)),
            int(round(error_y / _pixels_per_count)),
        )
        clock.wait_after(profile.polling_interval * random.uniform(1.5, 3.0))

    return win_api.get_cursor_pos()
