"""
Клавіатурний ввід з людяним ритмом і низькорівневими подіями через WinAPI.

Приклад:
    import effortless.keyboard as keyboard

    keyboard.type_text("Привіт, світе!")
    keyboard.press("enter")
    keyboard.hotkey("ctrl", "s")

Модель складається з тих самих шарів, що й миша, і з тих самих міркувань:

    profile.py     — почерк друкарки на сесію (швидкість, одруки, втома)
    layout.py      — фізична геометрія клавіш і переклад тексту в натискання
    mistakes.py    — одруки та їх виправлення
    rhythm.py      — розклад подій у часі (інтервали, утримання, накладання)
    emitter.py     — укладання розкладу на сітку опитування клавіатури
    transport.py   — куди йдуть події: SendInput чи рівень пристрою
    win_api.py     — скан-коди й розкладка

Що саме закриває кожен шар — у докстрінгах цих модулів. Найкоротше:
`win_api` прибирає ознаки в самих подіях (скан-коди замість VK_PACKET),
`rhythm` — ознаки в їхньому часі (накладання клавіш, залежність інтервалу
від пари клавіш), `emitter` — ознаки в часових мітках (сітка опитування),
`transport` — прапорець ін'єкції, і тільки він.

Розкладку модуль не перемикає: текст перекладається в скан-коди за тією
розкладкою, яка активна у вікні-отримувачі. Тобто перед набором кирилиці
розкладка має бути українська — так само, як для живої людини.
"""
import random
from typing import List, Optional, Sequence

from ..mouse.timing import lognormal_time, precise_sleep
from . import win_api
from .calibration import capture_summary, profile_from_capture
from .emitter import get_clock, play, pressed_keys, release_all, reset_clock
from .layout import (MOD_ALT, MOD_CTRL, MOD_SHIFT, NAMED_KEYS, Keystroke, resolve_char,
                     resolve_key, resolve_text, unsupported_chars)
from .mistakes import plan_strokes
from .profile import TypistProfile, get_profile, new_profile, set_profile
from .rhythm import KeyEvent, Stroke, build_schedule, digraph_interval
from .transport import (InterceptionTransport, RecordingTransport, SendInputTransport,
                        SerialHidTransport, get_transport, set_transport)

__all__ = [
    "type_text",
    "press",
    "hotkey",
    "hold",
    "key_down",
    "key_up",
    "release_all",
    # Налаштування почерку
    "TypistProfile",
    "get_profile",
    "new_profile",
    "set_profile",
    "profile_from_capture",
    "capture_summary",
    # Транспорт — куди йдуть події
    "get_transport",
    "set_transport",
    "SendInputTransport",
    "InterceptionTransport",
    "SerialHidTransport",
    "RecordingTransport",
    # Нижчий рівень — для аналізу й тестів
    "resolve_text",
    "unsupported_chars",
    "plan_strokes",
    "build_schedule",
    "digraph_interval",
    "Stroke",
    "KeyEvent",
]


def _reaction_delay(profile: TypistProfile) -> float:
    """Пауза «побачив поле -> почав друкувати».

    Логнормальна з тих самих міркувань, що й у миші: у людини є типове
    значення з довгим хвостом управо, а не рівне плато.
    """
    return lognormal_time(0.22 * (1.0 + 0.25 * profile.fatigue), 0.45, 0.06, 2.0)


def _segments(keystrokes: Sequence[Keystroke]) -> List[List[Keystroke]]:
    """Ріже послідовність на шматки, розділені символами без скан-кода.

    Символи, яких немає в поточній розкладці (емодзі, рідкі знаки), ввести
    натисканням неможливо — лише через `VK_PACKET`. Такий символ виноситься
    в окремий шматок довжиною один, щоб решта тексту йшла нормальним шляхом.
    """
    segments: List[List[Keystroke]] = [[]]
    for keystroke in keystrokes:
        if keystroke.unicode_fallback:
            segments.append([keystroke])
            segments.append([])
        else:
            segments[-1].append(keystroke)
    return [segment for segment in segments if segment]


def type_text(text: str, mistakes: bool = True, react: bool = True) -> None:
    """Набирає текст із людяним ритмом.

    Args:
        text: Що набрати. Переклад у клавіші робиться за розкладкою активного
            вікна; символи, яких у ній немає, ідуть через Unicode-підстановку.
        mistakes: Чи розігрувати одруки з виправленнями. Кінцевий текст від
            цього не змінюється — змінюється лише шлях до нього. Вимикати варто
            хіба що там, де поле вводу не терпить Backspace (маски, автодоповнення).
        react: Пауза перед початком набору. Вимикається, коли пауза вже
            зроблена явно — наприклад, одразу після кліку по полю.
    """
    if not text:
        return

    profile = get_profile()
    if react:
        precise_sleep(_reaction_delay(profile))

    for segment in _segments(resolve_text(text)):
        if segment[0].unicode_fallback:
            # Через транспорт, а не напряму в win_api: інакше цей шлях минав
            # драйвер і залізо, а RecordingTransport його не бачив узагалі.
            get_transport().send_text(segment[0].char)
            precise_sleep(lognormal_time(profile.current_interval, 0.3, 0.02, 0.6))
            continue

        strokes = plan_strokes(segment, profile) if mistakes else [Stroke(k) for k in segment]
        play(build_schedule(strokes, profile), profile)


def press(key: str, times: int = 1) -> None:
    """Натискає клавішу за іменем ('enter', 'f5', 'left') або символом.

    Args:
        key: Ім'я з `layout.NAMED_KEYS` або один символ.
        times: Скільки разів. Повтори йдуть з різними інтервалами й різним
            часом утримання — рівна серія однакових натискань не буває живою.
    """
    profile = get_profile()
    keystroke = resolve_key(key)
    play(build_schedule([Stroke(keystroke)] * max(1, times), profile), profile)


def hold(key: str, duration: float) -> None:
    """Тримає клавішу задану кількість секунд (рух у грі, прокрутка).

    Утримання не рівно `duration`: людина відпускає клавішу з розкидом у
    десятки мілісекунд, і саме рівні значення виглядають машинними.
    """
    profile = get_profile()
    keystroke = resolve_key(key)
    actual = max(0.02, random.gauss(duration, min(0.06, duration * 0.06)))
    play([KeyEvent(0.0, keystroke.scancode, True),
          KeyEvent(actual, keystroke.scancode, False)], profile)


def hotkey(*keys: str, hold_time: Optional[float] = None) -> None:
    """Натискає комбінацію: `hotkey("ctrl", "shift", "s")`.

    Модифікатори натискаються по черзі знизу вгору й відпускаються у
    зворотному порядку — так рука і робить. Одночасне натискання всіх клавіш
    в одну мілісекунду фізично неможливе й видно одразу.
    """
    if not keys:
        return

    profile = get_profile()
    keystrokes = [resolve_key(key) for key in keys]

    events: List[KeyEvent] = []
    t = 0.0
    for keystroke in keystrokes[:-1]:
        events.append(KeyEvent(t, keystroke.scancode, True))
        t += lognormal_time(0.075, 0.35, 0.015, 0.4)

    last = keystrokes[-1]
    dwell = hold_time if hold_time is not None else lognormal_time(
        profile.dwell_median, profile.dwell_sigma, 0.02, 0.4
    )
    events.append(KeyEvent(t, last.scancode, True))
    t += dwell
    events.append(KeyEvent(t, last.scancode, False))

    for keystroke in reversed(keystrokes[:-1]):
        t += lognormal_time(0.055, 0.35, 0.010, 0.3)
        events.append(KeyEvent(t, keystroke.scancode, False))

    play(events, profile)


def key_down(key: str) -> None:
    """Затискає клавішу й лишає її затиснутою.

    Парний `key_up` — на тобі. Якщо є ризик винятку між ними, обгортай у
    `try/finally` з `release_all()`: затиснутий Shift, що дістався
    користувачеві, помітніший за будь-яку статистику.
    """
    keystroke = resolve_key(key)
    play([KeyEvent(0.0, keystroke.scancode, True)], persist=True)


def key_up(key: str) -> None:
    """Відпускає клавішу, затиснуту через `key_down`."""
    keystroke = resolve_key(key)
    play([KeyEvent(0.0, keystroke.scancode, False)], persist=True)
