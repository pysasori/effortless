"""
Фізична геометрія клавіатури і переклад тексту в послідовність натискань.

Геометрія тут прив'язана до **скан-кодів**, а не до символів. Скан-код — це
номер фізичної клавіші; він не залежить від розкладки. Завдяки цьому
«клавіша під лівим вказівним пальцем» лишається тією самою і в англійській
розкладці (F), і в українській (А), і в будь-якій іншій — а разом з нею
лишаються правильними і сусідство клавіш (звідки беруться правдоподібні
одруки), і належність до руки (звідки береться ритм).

Якби геометрію задавали символами, як роблять майже всі бібліотеки, для
кожної розкладки довелося б писати окрему таблицю сусідів, а одруки в
українському тексті виглядали б як одруки англомовної людини.

Чому це взагалі важливо для ритму: інтервал між двома клавішами залежить не
від того, які це символи, а від того, якими пальцями їх б'ють. Дві клавіші
одним пальцем — найповільніший перехід (палець мусить фізично переїхати),
чергування рук — найшвидший (друга рука вже в дорозі, поки перша дотискає).
Різниця не косметична: між «одним пальцем» і «різними руками» це приблизно
вдвічі, і саме цей розподіл у ботів вироджується в один пік.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from . import win_api

__all__ = [
    "KeyPos",
    "Keystroke",
    "GEOMETRY",
    "NAMED_KEYS",
    "MOD_SHIFT",
    "MOD_CTRL",
    "MOD_ALT",
    "resolve_char",
    "resolve_key",
    "resolve_text",
    "unsupported_chars",
    "neighbors",
    "same_finger",
    "same_hand",
    "travel",
]

MOD_SHIFT = 0x01
MOD_CTRL = 0x02
MOD_ALT = 0x04

# Скан-коди модифікаторів (набір 1). Правий Shift навмисно окремою клавішею:
# людина тисне Shift протилежною рукою від літери, і бот, який завжди б'є
# лівий Shift, видає себе на кожній великій літері з лівої половини.
SC_LSHIFT = 0x2A
SC_RSHIFT = 0x36
SC_LCTRL = 0x1D
SC_LALT = 0x38
SC_RALT = 0xE038
SC_BACKSPACE = 0x0E
SC_SPACE = 0x39
SC_ENTER = 0x1C
SC_TAB = 0x0F
SC_CAPSLOCK = 0x3A

# Клавіші, на які палець «промахнутись» у моделі не може — див. `neighbors`.
_NOT_A_TYPO = frozenset((SC_LSHIFT, SC_RSHIFT, SC_CAPSLOCK, SC_TAB, SC_ENTER, SC_BACKSPACE))


@dataclass(frozen=True)
class KeyPos:
    """Позиція клавіші на клавіатурі.

    Attributes:
        row: Ряд, 0 — цифровий, 1 — верхній літерний, 2 — домашній, 3 — нижній.
        x: Горизонтальна позиція в ширинах клавіші, з урахуванням східчастого
            зсуву рядів (саме він робить відстань між Q і A не нульовою).
        finger: -4..-1 — мізинець..вказівний лівої руки, 1..4 — вказівний..мізинець
            правої. Великі пальці (пробіл) — 0.
    """

    row: int
    x: float
    finger: int

    @property
    def hand(self) -> int:
        """-1 ліва, 1 права, 0 великий палець."""
        return (self.finger > 0) - (self.finger < 0)


def _row(scancodes: Sequence[int], row: int, offset: float, fingers: Sequence[int]) -> Dict[int, KeyPos]:
    return {
        scan: KeyPos(row=row, x=offset + i, finger=fingers[min(i, len(fingers) - 1)])
        for i, scan in enumerate(scancodes)
    }


# Розкладка пальців — стандартна сліпого методу: мізинці тримають краї,
# вказівні відповідають за дві колонки кожен.
_F_DIGITS = (-4, -4, -3, -2, -1, -1, 1, 2, 3, 4, 4, 4, 4)
_F_LETTERS = (-4, -3, -2, -1, -1, 1, 1, 2, 3, 4, 4, 4)
_F_BOTTOM = (-4, -3, -2, -1, -1, 1, 1, 2, 3, 4)

# Східчастий зсув рядів на реальній клавіатурі: кожен наступний ряд зміщений
# управо приблизно на чверть клавіші відносно попереднього.
GEOMETRY: Dict[int, KeyPos] = {}
GEOMETRY.update(_row([0x29, *range(0x02, 0x0E)], 0, 0.00, _F_DIGITS))          # ` 1..0 - =
GEOMETRY.update(_row([0x0F, *range(0x10, 0x1C)], 1, 0.50, (-4, *_F_LETTERS)))  # Tab Q..P [ ] \
GEOMETRY.update(_row([0x3A, *range(0x1E, 0x29)], 2, 0.75, (-4, *_F_LETTERS)))  # Caps A..L ; '
GEOMETRY.update(_row([SC_LSHIFT, *range(0x2C, 0x36)], 3, 1.25, (-4, *_F_BOTTOM)))  # Shift Z..M , . /
GEOMETRY[SC_SPACE] = KeyPos(row=4, x=4.0, finger=0)
GEOMETRY[SC_ENTER] = KeyPos(row=2, x=13.0, finger=4)
GEOMETRY[SC_BACKSPACE] = KeyPos(row=0, x=13.0, finger=4)
GEOMETRY[SC_RSHIFT] = KeyPos(row=3, x=12.0, finger=4)

# Клавіші, які адресуються іменем, а не символом.
NAMED_KEYS: Dict[str, int] = {
    "backspace": SC_BACKSPACE,
    "tab": SC_TAB,
    "enter": SC_ENTER,
    "return": SC_ENTER,
    "shift": SC_LSHIFT,
    "ctrl": SC_LCTRL,
    "alt": SC_LALT,
    "altgr": SC_RALT,
    "capslock": SC_CAPSLOCK,
    "space": SC_SPACE,
    "esc": 0x01,
    "escape": 0x01,
    "delete": 0xE053,
    "insert": 0xE052,
    "home": 0xE047,
    "end": 0xE04F,
    "pageup": 0xE049,
    "pagedown": 0xE051,
    "up": 0xE048,
    "down": 0xE050,
    "left": 0xE04B,
    "right": 0xE04D,
    "win": 0xE05B,
    **{f"f{i}": scan for i, scan in enumerate(range(0x3B, 0x45), start=1)},
    **{"f11": 0x57, "f12": 0x58},
}

# Фізичні скан-коди (набір 1) клавіш у позиціях QWERTY. Не залежать від
# розкладки: клавіша в позиції «C» має скан-код 0x2E і на англійській, і на
# українській. Потрібні для комбінацій і команд, бо Ctrl+C адресує ФІЗИЧНУ
# клавішу, а не літеру «c» поточної розкладки. Через розкладку (`resolve_char`)
# латинська «c» на укр. розкладці дає скан-код 0 — і хоткей стає no-op.
QWERTY_SCAN: Dict[str, int] = {
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B, "-": 0x0C, "=": 0x0D,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15,
    "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19, "[": 0x1A, "]": 0x1B,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
    "j": 0x24, "k": 0x25, "l": 0x26, ";": 0x27, "'": 0x28, "`": 0x29,
    "\\": 0x2B, "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30,
    "n": 0x31, "m": 0x32, ",": 0x33, ".": 0x34, "/": 0x35,
}


@dataclass(frozen=True)
class Keystroke:
    """Одне натискання: яку клавішу бити і з якими модифікаторами.

    Attributes:
        scancode: Скан-код основної клавіші.
        mods: Маска MOD_* — які модифікатори мають бути затиснуті.
        char: Символ, який має з'явитись (None для службових клавіш).
        unicode_fallback: Символу немає в поточній розкладці, і надрукувати
            його можна лише через VK_PACKET.
    """

    scancode: int
    mods: int = 0
    char: Optional[str] = None
    unicode_fallback: bool = False

    @property
    def pos(self) -> Optional[KeyPos]:
        return GEOMETRY.get(self.scancode)


# ----------------------------------------------------------------------
# Символ -> натискання
# ----------------------------------------------------------------------

def resolve_char(ch: str, layout: Optional[int] = None) -> Keystroke:
    """Перекладає символ у натискання за поточною розкладкою.

    Розкладку не «вгадуємо» і не перемикаємо: беремо ту, що зараз активна у
    вікні-отримувачі. Перемикання розкладки з коду — окрема помітна дія
    (подія від невідомого джерела в чужому вікні), і робити її мовчки за
    користувача не варто.
    """
    layout = layout or win_api.active_layout()

    if ch == "\n":
        return Keystroke(SC_ENTER, char=ch)
    if ch == "\t":
        return Keystroke(SC_TAB, char=ch)

    resolved = win_api.vk_of_char(ch, layout)
    if resolved is None:
        return Keystroke(0, char=ch, unicode_fallback=True)

    vk, state = resolved
    scancode = win_api.scan_of_vk(vk, layout)
    if not scancode:
        return Keystroke(0, char=ch, unicode_fallback=True)

    mods = 0
    if state & 0x01:
        mods |= MOD_SHIFT
    if state & 0x02:
        mods |= MOD_CTRL
    if state & 0x04:
        mods |= MOD_ALT

    # CapsLock інвертує регістр літер: з увімкненим CapsLock велика літера
    # набирається БЕЗ Shift, а мала — З ним. Без цієї перевірки весь текст
    # виходив у протилежному регістрі, і жодна статистика цього не ловила.
    if ch.isalpha() and ch.lower() != ch.upper() and win_api.is_toggled(win_api.VK_CAPITAL):
        mods ^= MOD_SHIFT

    return Keystroke(scancode, mods=mods, char=ch)


def resolve_key(name: str, layout: Optional[int] = None) -> Keystroke:
    """Клавіша для натискання за іменем ('enter'), латинською позицією ('c')
    або символом розкладки ('ф').

    Порядок важливий і відрізняється від `resolve_char`:
      1. Іменована клавіша ('enter', 'f5', 'left') -> її скан-код.
      2. Одна латинська літера / цифра / знак -> ФІЗИЧНА позиція QWERTY.
         Саме це потрібно для команд і навігації: `press('w')` у грі й
         `hotkey('ctrl','c')` адресують фізичну клавішу, а не літеру поточної
         розкладки. Інакше під українською 'c' дало б скан-код 0 (no-op).
      3. Інший одиночний символ (кирилиця тощо) -> через розкладку, як текст.
    """
    scancode = NAMED_KEYS.get(name.lower())
    if scancode is not None:
        return Keystroke(scancode)
    if len(name) == 1:
        physical = QWERTY_SCAN.get(name.lower())
        if physical is not None:
            return Keystroke(physical, char=name)
        return resolve_char(name, layout)
    raise ValueError(f"невідома клавіша: {name!r}")


def resolve_text(text: str, layout: Optional[int] = None) -> List[Keystroke]:
    """Перекладає рядок у послідовність натискань. Розкладка читається один раз."""
    layout = layout or win_api.active_layout()
    return [resolve_char(ch, layout) for ch in text]


def unsupported_chars(text: str, layout: Optional[int] = None) -> List[str]:
    """Символи тексту, яких немає в активній розкладці.

    Перевірити варто **до** набору: латиниця в українській розкладці (і
    навпаки) сюди потрапляє цілком, і кожен такий символ поїде через
    VK_PACKET — тобто з очевидною позначкою в потоці подій. Правильна
    реакція на непорожній результат — не «набрати як вийде», а перемкнути
    розкладку у вікні-отримувачі.
    """
    layout = layout or win_api.active_layout()
    seen, missing = set(), []
    for ch in text:
        if ch in seen or ch in "\n\t":
            continue
        seen.add(ch)
        if resolve_char(ch, layout).unicode_fallback:
            missing.append(ch)
    return missing


# ----------------------------------------------------------------------
# Відносини між клавішами — основа ритму й одруків
# ----------------------------------------------------------------------

def travel(a: int, b: int) -> float:
    """Відстань між клавішами в ширинах клавіші.

    Використовується для тривалості переходу: чим далі летить палець, тим
    довший інтервал. Це та сама ідея, що закон Фітца для миші, тільки для
    руки на клавіатурі.
    """
    pos_a, pos_b = GEOMETRY.get(a), GEOMETRY.get(b)
    if pos_a is None or pos_b is None:
        return 1.0
    return ((pos_a.x - pos_b.x) ** 2 + (pos_a.row - pos_b.row) ** 2) ** 0.5


def same_finger(a: int, b: int) -> bool:
    """Чи б'ються обидві клавіші одним пальцем — найповільніший перехід."""
    pos_a, pos_b = GEOMETRY.get(a), GEOMETRY.get(b)
    if pos_a is None or pos_b is None or pos_a.finger == 0:
        return False
    return pos_a.finger == pos_b.finger and a != b


def same_hand(a: int, b: int) -> bool:
    """Чи б'ються обидві клавіші однією рукою."""
    pos_a, pos_b = GEOMETRY.get(a), GEOMETRY.get(b)
    if pos_a is None or pos_b is None:
        return False
    return pos_a.hand == pos_b.hand and pos_a.hand != 0


def neighbors(scancode: int, radius: float = 1.15) -> List[int]:
    """Фізично сусідні клавіші — кандидати на одрук «промазав по клавіші».

    Радіус за замовчуванням охоплює сусідів по ряду і по діагоналі, але не
    через одну: людина промахується на сусідню клавішу, а не на дві.
    """
    pos = GEOMETRY.get(scancode)
    if pos is None or pos.row == 4:
        return []

    found = []
    for other, other_pos in GEOMETRY.items():
        # Службові клавіші на краях — не кандидати на одрук: промах по Tab
        # переносить фокус в інше поле, по Enter — відправляє форму, і тоді
        # виправлення Backspace-ом іде вже не туди. Це не «схоже на бота», це
        # зламаний сценарій.
        if other == scancode or other_pos.row == 4 or other in _NOT_A_TYPO:
            continue
        if abs(other_pos.row - pos.row) <= 1 and travel(scancode, other) <= radius:
            found.append(other)
    return found
