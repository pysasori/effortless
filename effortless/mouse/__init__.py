"""
Керування мишею з людяними траєкторіями руху + низькорівневі кліки через WinAPI.

Приклад:
    import effortless.mouse as mouse

    mouse.move(x=100, y=200)
    mouse.click(x=150, y=250, target_radius=12)

Модель складається з чотирьох шарів, кожен у своєму модулі:

    profile.py     — почерк оператора на сесію (швидкість, зсув прицілу, втома)
    trajectory.py  — геометрія і час руху (субрухи, мінімум ривка, тремор)
    emitter.py     — відтворення траєкторії відносними подіями зі зворотним зв'язком
    timing.py      — 1-мс таймер і логнормальні паузи

Детальніше про те, які саме ознаки автоматики закриває кожен шар — у
докстрінгах цих модулів.
"""
import atexit
import functools
import math
import random
import time
from typing import Optional, Tuple

from . import win_api
from .calibration import capture_summary, profile_from_capture
from .clock import get_clock
from .emitter import emit_path
from .profile import OperatorProfile, get_profile, new_profile, set_profile
from .timing import enable_high_resolution_timer, lognormal_time, precise_sleep
from .transport import (BUTTON_LEFT, BUTTON_MIDDLE, BUTTON_RIGHT, InterceptionTransport,
                        RecordingTransport, SendInputTransport, SerialHidTransport,
                        get_transport, set_transport)
from .trajectory import (Sample, click_hold_duration, generate_idle_path,
                         generate_path, reaction_delay)
from .wind_mouse import wind_mouse

__all__ = [
    "move",
    "move_from_point",
    "move_and_click",
    "drag",
    "click",
    "long_click",
    "scroll",
    "wheel",
    "release_all_buttons",
    # Налаштування почерку
    "OperatorProfile",
    "get_profile",
    "new_profile",
    "set_profile",
    "profile_from_capture",
    "capture_summary",
    # Транспорт — куди йдуть події
    "get_transport",
    "set_transport",
    "SendInputTransport",
    "SerialHidTransport",
    "InterceptionTransport",
    "RecordingTransport",
    # Нижчий рівень — для аналізу й тестів
    "generate_path",
    "emit_path",
    "Sample",
    "wind_mouse",
]

_BUTTONS = {"left": BUTTON_LEFT, "right": BUTTON_RIGHT, "middle": BUTTON_MIDDLE}

# Поточний стан кнопок — маска, як у HID-звіті справжньої миші. Транспорт
# приймає саме стан, а не окремі "натиснув"/"відпустив", тож розсинхронізації
# між тим, що ми думаємо, і тим, що бачить система, бути не може.
#
# Але сама по собі маска НЕ рятує від затиснутої кнопки: між натисканням і
# відпусканням лежить рух, і виняток у цей момент лишив би кнопку внизу.
# Від цього рятує `release_all_buttons` у `finally` кожної публічної дії.
_buttons = 0

enable_high_resolution_timer()

# Медіана часу підльоту, що лишається після натискання «на льоту», с.
_EARLY_PRESS_LEAD = 0.028

# Час кінця траєкторії, яку зараз відтворює _goto (для on_tick у click).
_path_end_time: Optional[float] = None

# Скільки додаткових довідних наведень робити, якщо курсор не дійшов.
_ARRIVAL_ATTEMPTS = 2

# Радіус, з яким звіряємо прибуття, коли викликач розміру цілі не назвав.
_DEFAULT_RADIUS = 8.0


def _rest(duration: float, hand_on: Optional[bool] = None) -> None:
    """Пауза, під час якої курсор не завмирає намертво.

    Рука, що лежить на миші, тремтить, тому в реальному потоці подій
    "простій" — це не порожнеча, а рідкі зсуви на піксель. Абсолютно
    нерухомий курсор буває лише тоді, коли руку з миші зняли, тож саме це
    тут і розігрується: чим довша пауза, тим імовірніше, що руку прибрали.

    Args:
        duration: Тривалість паузи в секундах.
        hand_on: Примусово задати, чи лежить рука на миші. Потрібно там, де
            це відомо напевно — під час утримання кнопки рука на миші за
            означенням.
    """
    if duration <= 0:
        return

    profile = get_profile()
    if hand_on is None:
        # «Рука на миші» — СТАН, а не монетка на кожну паузу. Раніше стан
        # розігрувався щоразу заново і міг мигати: 0.3 с на миші, 0.3 с
        # знята, знову на. Людина тримає руку на миші або знімає її на
        # десятки секунд поспіль. Тут стан живе між паузами і перемикається
        # з імовірністю, пропорційною тривалості паузи: середнє перебування в
        # кожному стані — близько `hand_state_dwell` секунд чистого простою.
        switch = min(0.5, duration / profile.hand_state_dwell)
        if profile.hand_resting and random.random() < switch * profile.hand_off_chance * 2.0:
            profile.hand_resting = False
        elif not profile.hand_resting and random.random() < switch:
            profile.hand_resting = True
        hand_on = profile.hand_resting

    samples = generate_idle_path(win_api.get_cursor_pos(), duration, profile) if hand_on else []
    if not samples:
        precise_sleep(duration)
        return

    started = time.perf_counter()
    # settle=False: кінцева точка тут не ціль, а випадкова фаза тремору —
    # дотягувати курсор у неї безглуздо.
    emit_path(samples, profile, settle=False)

    # Шум укладається в ціле число тактів, тому добираємо залишок паузи.
    precise_sleep(duration - (time.perf_counter() - started))


def _pause_between_actions() -> None:
    """Коротка пауза після завершеної дії.

    Логнормальна, а не рівномірна: у людини є типовий інтервал з невеликим
    розкидом і рідкими довгими затримками, а не рівне плато від 50 до 200 мс.
    """
    profile = get_profile()
    pause = lognormal_time(0.11 * (1.0 + 0.3 * profile.fatigue), 0.42, 0.03, 1.6)

    # Довгий хвіст. Без нього паузи бібліотеки мали жорстку стелю ~2.8 с
    # (пауза + реакція), і за годину роботи не траплялось жодної довшої —
    # тоді як людина регулярно зависає на секунди: прочитала, відволіклась.
    # Частка мала, бо між діями бот і так чекає на розпізнавання — це теж
    # паузи, і вони вже довгі.
    if random.random() < profile.long_pause_chance:
        pause += lognormal_time(2.5, 0.9, 0.4, 20.0)

    _rest(pause)


def _clamp_to_screen(x: float, y: float) -> Tuple[float, float]:
    """Заганяє ціль у межі віртуального робочого столу.

    Курсор за межі екрана не виходить: якби ціль лишилась зовні, регулятор
    у `emitter` весь рух намагався б погасити нев'язку, яку погасити
    неможливо, і траєкторія вироджувалась би в упирання в край.
    """
    left, top, width, height = win_api.get_virtual_screen()
    return (
        min(max(x, left), left + width - 1),
        min(max(y, top), top + height - 1),
    )


def _goto(
    x: float,
    y: float,
    target_radius: Optional[float],
    react: bool = True,
    on_tick=None,
) -> Tuple[int, int]:
    """Веде курсор до цілі: пауза реакції -> траєкторія -> відтворення.

    Args:
        react: Чи робити паузу реакції перед стартом. Вимикається там, де
            пауза вже зроблена явно (наприклад, після натискання кнопки в
            `drag`), щоб не отримати подвійне зволікання.
    """
    profile = get_profile()
    x, y = _clamp_to_screen(x, y)
    radius = target_radius if target_radius is not None else _DEFAULT_RADIUS

    global _path_end_time

    if react:
        precise_sleep(reaction_delay(profile))

    path = generate_path(win_api.get_cursor_pos(), (x, y), target_radius, profile)
    _path_end_time = path[-1].t if path else None
    try:
        final = emit_path(path, profile, on_tick=on_tick)
    finally:
        _path_end_time = None

    # Перевірка прибуття. Регулятор працює зі зворотним зв'язком і зазвичай
    # приводить курсор куди треба, але він залежить від оцінки масштабу,
    # від того, чи не втрутилась фізична миша, і від того, чи встигла система
    # застосувати попередні події. Зрідка (заміряно приблизно один раз на
    # дюжину рухів) щось із цього збивається, і курсор лишається далеко від
    # цілі — а мовчазний промах гірший за будь-яку ознаку автоматики.
    #
    # Довідний рух тут не «милиця»: людина, яка промахнулась повз кнопку,
    # робить рівно те саме. Тому це не окремий шлях у коді, а ще одне
    # звичайне наведення.
    for _ in range(_ARRIVAL_ATTEMPTS):
        if math.hypot(final[0] - x, final[1] - y) <= radius:
            break
        precise_sleep(lognormal_time(0.13, 0.4, 0.04, 0.7))
        final = emit_path(
            generate_path(win_api.get_cursor_pos(), (x, y), target_radius, profile),
            profile,
        )

    profile.note_action()
    return final


def _button_bit(button: str) -> int:
    """Бітова маска кнопки за іменем.

    Помилку в імені краще побачити одразу: мовчазне падіння назад на ліву
    кнопку означає, що `drag(..., button="midle")` тисне не те, що написано,
    і зрозуміти це з поведінки майже неможливо.
    """
    try:
        return _BUTTONS[button]
    except KeyError:
        raise ValueError(
            f"Невідома кнопка {button!r}. Доступні: {', '.join(sorted(_BUTTONS))}."
        ) from None


def _apply_button(button: str, down: bool) -> None:
    """Змінює стан кнопки негайно, без чекання такту.

    Потрібно там, де такт уже настав і в ньому щойно поїхало зміщення:
    справжня миша шле рух і стан кнопок одним HID-звітом, тож натискання
    «на льоту» має лягти в той самий такт, а не між тактами.
    """
    global _buttons

    bit = _button_bit(button)
    _buttons = (_buttons | bit) if down else (_buttons & ~bit)
    get_transport().set_buttons(_buttons)


def release_all_buttons() -> None:
    """Відпускає всі кнопки миші, які бібліотека вважає натиснутими.

    Потрібна тому, що натискання й відпускання розділені в часі рухом:
    `drag` і `scroll` тримають кнопку весь час переміщення, а `click` з
    натисканням «на льоту» — частину траєкторії. Будь-який виняток у цьому
    проміжку (Ctrl+C під час показу, помилка транспорту, збій у ctypes)
    лишав би кнопку затиснутою на рівні системи — а `scroll` тримає ПРАВУ,
    після чого машина фактично некерована, поки не клікнеш рукою.

    Викликається автоматично з `finally` у кожній публічній дії і на виході
    з процесу, але доступна і напряму — якщо щось пішло не так у чужому коді.
    """
    global _buttons

    if not _buttons:
        return

    _buttons = 0
    try:
        get_transport().set_buttons(0)
    except Exception:
        # Аварійне звільнення не має права кинути виняток поверх того, який
        # його спричинив: інакше справжня причина збою загубиться.
        pass


atexit.register(release_all_buttons)


def _release_on_failure(action):
    """Відпускає кнопки, якщо дія обірвалась винятком.

    Ловиться саме `BaseException`, а не `Exception`: найімовірніший спосіб
    обірвати дію на показі — це Ctrl+C, тобто `KeyboardInterrupt`, який від
    `Exception` не успадковується. Виняток після звільнення перекидається
    далі незмінним.
    """
    @functools.wraps(action)
    def wrapper(*args, **kwargs):
        try:
            return action(*args, **kwargs)
        except BaseException:
            release_all_buttons()
            raise

    return wrapper


def _set_button(button: str, down: bool) -> None:
    """Змінює стан кнопки рівно на такті годинника опитування.

    Це і є вирівнювання, якого раніше не було: рух ішов по сітці частоти
    опитування, а натискання — тоді, коли доспала логіка програми. Кожен
    потік окремо виглядав нормально, але таймстемпи кнопок і руху були з
    різних годинників, чого у справжньої миші не буває: там усе їде одним
    HID-звітом.
    """
    get_clock().wait_next()
    _apply_button(button, down)


def _press(button: str, hold: float) -> None:
    """Натискання з утриманням `hold`, обидві події — на тактах годинника."""
    _set_button(button, True)
    # Утримання бімодальне, бо таким воно є в реальних кліків. Заміряно на
    # живій руці: у ~2/3 кліків курсор між down і up стоїть АБСОЛЮТНО
    # нерухомо (0 подій руху), і лише в решті рука ледь тремтить. Модель, що
    # завжди тремтить під час утримання, дає рух у 100% кліків — а це якраз
    # той тел, за яким клік бота видно найпершим. Тож більшість утримань —
    # повний спокій, і тільки решта йде через idle-тремор.
    if random.random() < get_profile().press_still_chance:
        precise_sleep(hold)
    else:
        _rest(hold, hand_on=True)
    _set_button(button, False)


@_release_on_failure
def move(
    x: Optional[int] = None,
    y: Optional[int] = None,
    target_radius: Optional[float] = None,
) -> None:
    """Переміщує курсор в абсолютні координати (x, y).

    Args:
        x, y: Координати цілі. Пропущену координату лишає без змін.
        target_radius: Радіус цілі в пікселях. Впливає і на розкид влучання,
            і на тривалість руху (закон Фітца): у дрібну ціль курсор їде
            довше й з більшою кількістю довідних рухів.
    """
    current_x, current_y = win_api.get_cursor_pos()
    _goto(
        x if x is not None else current_x,
        y if y is not None else current_y,
        target_radius,
    )
    _pause_between_actions()


@_release_on_failure
def move_from_point(
    x: Optional[int] = None,
    y: Optional[int] = None,
    target_radius: Optional[float] = None,
) -> None:
    """Переміщує курсор відносно поточної позиції на (x, y)."""
    current_x, current_y = win_api.get_cursor_pos()
    _goto(current_x + (x or 0), current_y + (y or 0), target_radius)
    _pause_between_actions()


@_release_on_failure
def click(
    x: Optional[int] = None,
    y: Optional[int] = None,
    jitter: int = 3,
    button: str = "left",
    target_radius: Optional[float] = None,
) -> None:
    """Клікає кнопкою миші, за бажанням попередньо переміщуючись у (x, y).

    Args:
        x, y: Координати цілі. Курсор навмисно зупиняється не рівно в них, а
            в межах радіуса цілі — влучання в один і той самий піксель двічі
            поспіль людина не повторює.
        jitter: Сумісний зі старим API радіус розкиду. Використовується як
            радіус цілі, якщо `target_radius` не заданий.
        button: "left", "right" або "middle".
        target_radius: Радіус цілі в пікселях; має пріоритет над `jitter`.
    """
    hold = click_hold_duration()
    pressed_at = None

    if x is not None or y is not None:
        current_x, current_y = win_api.get_cursor_pos()
        radius = target_radius if target_radius is not None else max(1.0, float(jitter))
        goal_x = x if x is not None else current_x
        goal_y = y if y is not None else current_y

        # Частина кліків іде «на льоту»: кнопка тисне ще під час останнього
        # довідного руху. Курсор, що обов'язково завмирає перед кожним
        # натисканням, — поведінка механізму, і в потоці подій вона видніша
        # за будь-яку деталь самого кліку.
        early = random.random() < get_profile().press_on_arrival_chance
        # Скільки часу підльоту лишиться після натискання — НЕПЕРЕРВНА
        # величина. Раніше вибирався номер такту з 1..5, тобто час до кінця
        # руху був рівно 8, 16, 24, 32 або 40 мс і ніяким іншим — п'ять
        # значень зі стінкою на 40. Логнормальна в часі, з хвостом.
        lead = lognormal_time(_EARLY_PRESS_LEAD, 0.6, 0.004, 0.16)
        state = {"end_t": None}

        def on_tick(index: int, total: int, sample: Sample) -> None:
            nonlocal pressed_at
            if pressed_at is not None:
                return
            if state["end_t"] is None:
                # Час кінця руху відомий лише з останнього семпла; сюди він
                # приходить не одразу, тому запам'ятовуємо перший і рахуємо
                # від тривалості, яку emit_path передає разом із семплом.
                state["end_t"] = _path_end_time
            if state["end_t"] is None or state["end_t"] - sample.t > lead:
                return

            # Натискати «на льоту» можна лише тоді, коли курсор УЖЕ всередині
            # цілі. Без цієї перевірки рання кнопка була не стилістикою, а
            # промахом: заміряно 3.5% втрачених кліків по кнопці радіусом
            # 8 px і 13% по трипіксельній, бо клік фіксує ціль у момент
            # натискання, а курсор у цей момент був ще за 12-17 пікселів.
            cursor_x, cursor_y = win_api.get_cursor_pos()
            if math.hypot(cursor_x - goal_x, cursor_y - goal_y) > radius * 0.8:
                return

            pressed_at = time.perf_counter()
            _apply_button(button, True)

        _goto(goal_x, goal_y, radius, on_tick=on_tick if early else None)

    if pressed_at is None:
        # Пауза між зупинкою курсора і натисканням: рука встигає "заспокоїтись".
        _rest(lognormal_time(0.075, 0.45, 0.02, 0.6), hand_on=True)
        _press(button, hold)
    else:
        # Кнопка вже натиснута — лишилось дотримати решту часу утримання.
        _rest(max(0.0, hold - (time.perf_counter() - pressed_at)), hand_on=True)
        _set_button(button, False)

    _pause_between_actions()


@_release_on_failure
def move_and_click(
    x: Optional[int] = None,
    y: Optional[int] = None,
    jitter: int = 3,
    target_radius: Optional[float] = None,
) -> None:
    """Переміщує курсор та клікає лівою кнопкою."""
    click(x, y, jitter=jitter, target_radius=target_radius)


@_release_on_failure
def drag(
    x: int,
    y: int,
    button: str = "left",
    target_radius: Optional[float] = None,
) -> None:
    """Затискає кнопку, переміщує курсор в (x, y) та відпускає.

    Паузи після натискання і перед відпусканням не косметичні: людина
    ніколи не починає тягнути в той самий момент, коли натиснула, і не
    відпускає кнопку рівно в піксель прибуття.
    """
    _set_button(button, True)
    _rest(lognormal_time(0.09, 0.4, 0.03, 0.6), hand_on=True)

    # Під час перетягування рука напруженіша — рух трохи повільніший і
    # прямолінійніший, тому ціль вважаємо більшою (менше довідних рухів).
    _goto(x, y, target_radius if target_radius is not None else 14.0, react=False)

    _rest(lognormal_time(0.11, 0.4, 0.03, 0.7), hand_on=True)
    _set_button(button, False)
    _pause_between_actions()


@_release_on_failure
def long_click(t: float = 0.2, button: str = "left") -> None:
    """Затискає кнопку миші приблизно на t секунд.

    Точна тривалість трохи "гуляє" навколо t: людина не витримує інтервал
    з точністю до мілісекунди, а рівно однакове утримання в серії кліків —
    помітна ознака.
    """
    _press(button, max(0.02, random.gauss(t, t * 0.09)))
    _pause_between_actions()


@_release_on_failure
def wheel(clicks: int, horizontal: bool = False) -> None:
    """Крутить колесо на `clicks` клацань (від'ємне — до себе).

    Людина не крутить колесо рівним потоком: виходять пачки по 2-5 клацань
    із коротким інтервалом і помітними паузами між пачками, поки око
    оцінює результат.
    """
    if clicks == 0:
        return

    step = 1 if clicks > 0 else -1
    remaining = abs(int(clicks))

    while remaining > 0:
        burst = min(remaining, random.randint(2, 5))
        for _ in range(burst):
            get_clock().wait_next()
            get_transport().wheel(step, horizontal)
            precise_sleep(lognormal_time(0.055, 0.3, 0.02, 0.3))
        remaining -= burst

        if remaining:
            precise_sleep(lognormal_time(0.22, 0.45, 0.06, 1.2))

    _pause_between_actions()


@_release_on_failure
def scroll(px: int) -> None:
    """Прокрутка на px пікселів утримуванням правої кнопки (як у грі).

    Поведінка та сама, що й раніше, але переміщення йде людяною
    траєкторією замість лінійної інтерполяції: рівномірний рух за рівний
    час — найпростіша для розпізнавання форма з усіх можливих.
    """
    start_x, start_y = win_api.get_cursor_pos()

    _set_button("right", True)
    _rest(lognormal_time(0.09, 0.4, 0.03, 0.6), hand_on=True)

    _goto(start_x, start_y - px, 12.0, react=False)

    _rest(lognormal_time(0.12, 0.4, 0.04, 0.7), hand_on=True)
    _set_button("right", False)

    _goto(start_x, start_y, 12.0, react=False)
    _pause_between_actions()
