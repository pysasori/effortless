"""
Транспорт — куди саме йдуть згенеровані події клавіатури.

Так само, як для миші: ритм, одруки й почерк не залежать від того, як подія
потрапляє в систему, а от **чи видно, що вона згенерована** — залежить лише
від цього.

Поверхи вводу, знизу вгору:

    1. `PostMessage(WM_KEYDOWN)` / `WM_CHAR` — подію бачить одне вікно, фокус
       не потрібен. Тут не реалізовано: рівень надто високий, ігри й будь-що
       на Raw Input такого не бачать, а `WM_CHAR` ще й не має скан-кода.

    2. Системна черга (`SendInput`) — `SendInputTransport`. Найнижче, куди
       можна дістати з користувацького режиму. Windows позначає такі події
       прапорцем `LLKHF_INJECTED`, і прибрати позначку звідси неможливо.

    3. Рівень драйвера — `InterceptionTransport`. Готовий підписаний
       фільтр-драйвер класу клавіатури (kbdclass upper filter) вставляє подію
       нижче за те місце, де ставиться прапорець ін'єкції, тому хук бачить її
       як натиснуту рукою. Потребує встановленого драйвера й прав адміна, але
       свого драйвера писати й підписувати не треба.

    4. Рівень пристрою — `SerialHidTransport`. Звіти формує мікроконтролер,
       який для системи є звичайною USB-клавіатурою. Прапорця немає, бо
       система не має чого позначати: з її точки зору це залізо.

Чому не «свій драйвер». Написати власний kbdclass-фільтр можна, але на x64
його не завантажить без EV-підпису або без `testsigning on`, а testsigning
лишає водяний знак і змінений стан системи — слід помітніший за саму
ін'єкцію. Тому поверх 3 — це готовий підписаний драйвер Interception, а не
свій. Порівняно з поверхом 4 у нього є власний слід (драйвер видно в
системі), тож якщо перевірка дивиться ще й на список драйверів — потрібне
залізо; якщо лише на прапорець ін'єкції — досить Interception.
"""
import ctypes
import struct
from typing import Dict, FrozenSet, Optional, Protocol, Set

from . import win_api
from .layout import SC_LALT, SC_LCTRL, SC_LSHIFT, SC_RALT, SC_RSHIFT

__all__ = [
    "Transport",
    "SendInputTransport",
    "InterceptionTransport",
    "SerialHidTransport",
    "RecordingTransport",
    "get_transport",
    "set_transport",
]


class Transport(Protocol):
    """Інтерфейс транспорту.

    Приймає **стан** (які клавіші зараз натиснуті), а не окремі «натиснув» /
    «відпустив»: так неможливо розсинхронізуватись і лишити клавішу
    затиснутою після винятку. Це та сама модель, що в HID-звіті клавіатури,
    де кожен звіт несе повний список натиснутого.
    """

    def set_keys(self, pressed: FrozenSet[int]) -> None: ...

    def send_text(self, ch: str) -> None:
        """Символ, якого немає в розкладці. Запасний шлях — див. `win_api.send_unicode`."""
        ...


_MODIFIER_SCANCODES = frozenset((SC_LSHIFT, SC_RSHIFT, SC_LCTRL, SC_LALT, SC_RALT,
                                 0xE01D, 0xE05B, 0xE05C))


def _is_modifier(scancode: int) -> bool:
    return scancode in _MODIFIER_SCANCODES


class _KeyState:
    """Спільна логіка: стан -> події натискання/відпускання."""

    def __init__(self) -> None:
        self._pressed: Set[int] = set()

    def diff(self, pressed: FrozenSet[int]):
        down = pressed - self._pressed
        up = self._pressed - pressed
        self._pressed = set(pressed)
        return down, up


class SendInputTransport:
    """Поверх 2: події через `SendInput`. Типовий транспорт, нічого не потребує."""

    def __init__(self) -> None:
        self._state = _KeyState()

    def set_keys(self, pressed: FrozenSet[int]) -> None:
        down, up = self._state.diff(pressed)
        if not down and not up:
            return

        # Відпускання першими: якщо в одному такті одна клавіша відпускається,
        # а інша натискається, саме такий порядок дає коректний стан.
        # Усе одним викликом — фізична клавіатура теж віддає це одним звітом.
        #
        # Порядок ВСЕРЕДИНІ групи теж не байдужий, бо `SendInput` — це
        # послідовність, а не стан: серед натискань модифікатори мають іти
        # першими (Shift раніше за свою літеру), серед відпускань — останніми.
        # Ітерація по set давала порядок хешу, і в половині випадків Shift
        # вилітав після власної літери або перед чужою.
        events = (
            [(scan, True) for scan in sorted(up, key=_is_modifier)]
            + [(scan, False) for scan in sorted(down, key=lambda s: not _is_modifier(s))]
        )
        win_api.send_batch(events)

    def send_text(self, ch: str) -> None:
        win_api.send_unicode(ch)


class InterceptionTransport:
    """Поверх 3: ін'єкція через драйвер-фільтр Interception.

    Драйвер стоїть у стеку пристроїв нижче за шар, який позначає події
    прапорцем `LLKHF_INJECTED`, тому для системи вони приходять як звичайний
    ввід. На відміну від `SendInputTransport`, хук бачить їх без прапорця.

    Потребує окремого встановлення (адміністратор, перезавантаження): драйвер
    ставиться командою `install-interception.exe /install` з релізу проєкту
    Interception, поруч має лежати `interception.dll` тієї ж розрядності, що й
    Python.

    Args:
        device: Номер пристрою клавіатури (1..10 за угодою Interception). За
            замовчуванням перший слот — на нього драйвер прив'язує основну
            клавіатуру. Якщо на машині це не так, вкажи точний номер.
        dll_path: Шлях до `interception.dll`, якщо її немає поруч із процесом.

    Note:
        Клас написаний за документованим API драйвера, але **не перевірений на
        живому залізі** — на машині, де він писався, драйвер не встановлений.
        Перший запуск варто зробити з увімкненим `keyboard_capture.py`:
        колонка `injected` одразу покаже, чи справді події пішли нижче.
    """

    # Прапорці стану клавіші з interception.h.
    _KEY_DOWN = 0x00
    _KEY_UP = 0x01
    _KEY_E0 = 0x02   # префікс розширеної клавіші; аналог KEYEVENTF_EXTENDEDKEY

    class _KeyStroke(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("state", ctypes.c_ushort),
            ("information", ctypes.c_uint),
        ]

    def __init__(self, device: Optional[int] = None, dll_path: str = "interception.dll") -> None:
        try:
            self._dll = ctypes.WinDLL(dll_path)
        except OSError as exc:
            raise RuntimeError(
                f"Не знайдено {dll_path}. Потрібен драйвер Interception: "
                "встанови його (адміністратор + перезавантаження) і поклади "
                "interception.dll тієї ж розрядності, що й Python, поруч із процесом."
            ) from exc

        # Типи оголошуємо явно: без цього ctypes обріже контекст-вказівник до
        # 32 біт на x64 і виклики мовчки нічого не робитимуть.
        self._dll.interception_create_context.restype = ctypes.c_void_p
        self._dll.interception_destroy_context.argtypes = (ctypes.c_void_p,)
        self._dll.interception_is_keyboard.argtypes = (ctypes.c_int,)
        self._dll.interception_is_keyboard.restype = ctypes.c_int
        self._dll.interception_send.argtypes = (
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
        )
        self._dll.interception_send.restype = ctypes.c_int

        self._context = self._dll.interception_create_context()
        if not self._context:
            raise RuntimeError(
                "interception_create_context повернув NULL — драйвер не встановлений "
                "або система не перезавантажувалась після встановлення."
            )

        self._device = device if device is not None else self._find_keyboard()
        self._state = _KeyState()

    def _find_keyboard(self) -> int:
        """Перший пристрій, який драйвер вважає клавіатурою (діапазон 1..10)."""
        for candidate in range(1, 11):
            if self._dll.interception_is_keyboard(candidate):
                return candidate
        raise RuntimeError("Interception не бачить жодної клавіатури в діапазоні 1..10.")

    def _send(self, scancode: int, key_up: bool) -> None:
        # Розширені клавіші (0xE0XX): низький байт у code, префікс — прапорцем
        # стану, рівно як це кодує win_api для SendInput.
        state = self._KEY_UP if key_up else self._KEY_DOWN
        if scancode & 0xE000 == 0xE000:
            state |= self._KEY_E0
        stroke = self._KeyStroke(code=scancode & 0xFF, state=state, information=0)
        self._dll.interception_send(self._context, self._device, ctypes.byref(stroke), 1)

    def set_keys(self, pressed: FrozenSet[int]) -> None:
        down, up = self._state.diff(pressed)
        # Відпускання першими; модифікатори — першими серед натискань і
        # останніми серед відпускань. Той самий порядок, що і в SendInput.
        for scan in sorted(up, key=_is_modifier):
            self._send(scan, key_up=True)
        for scan in sorted(down, key=lambda s: not _is_modifier(s)):
            self._send(scan, key_up=False)

    def send_text(self, ch: str) -> None:
        # Драйверу дати Unicode нема як — він шле скан-коди. Мовчки впасти
        # назад на SendInput не можна: це саме той шлях з прапорцем injected,
        # заради уникнення якого драйвер і ставили.
        raise ValueError(
            f"Символ {ch!r} відсутній у активній розкладці, а транспорт рівня "
            "драйвера не має Unicode-шляху. Перемкни розкладку у вікні-отримувачі."
        )

    def close(self) -> None:
        if getattr(self, "_context", None):
            self._dll.interception_destroy_context(self._context)
            self._context = None


# Скан-код (набір 1) -> HID Usage ID (сторінка Keyboard/Keypad, 0x07).
# Потрібно лише транспорту рівня пристрою: мікроконтролер шле HID-звіт, а не
# скан-коди PS/2.
_HID_USAGE: Dict[int, int] = {
    # HID нумерує літери за абеткою (a = 0x04), тому таблиця — це ряд клавіш
    # у порядку скан-кодів, перекладений у цю нумерацію.
    **{0x1E + i: usage for i, usage in enumerate(
        (0x04, 0x16, 0x07, 0x09, 0x0A, 0x0B, 0x0D, 0x0E, 0x0F))},                # A S D F G H J K L
    **{0x10 + i: usage for i, usage in enumerate(
        (0x14, 0x1A, 0x08, 0x15, 0x17, 0x1C, 0x18, 0x0C, 0x12, 0x13))},          # Q W E R T Y U I O P
    **{0x2C + i: usage for i, usage in enumerate(
        (0x1D, 0x1B, 0x06, 0x19, 0x05, 0x11, 0x10))},                            # Z X C V B N M
    **{0x02 + i: 0x1E + i for i in range(9)},  # 1..9
    0x0B: 0x27,   # 0
    0x0C: 0x2D, 0x0D: 0x2E,           # - =
    0x1A: 0x2F, 0x1B: 0x30, 0x2B: 0x31,  # [ ] \
    0x27: 0x33, 0x28: 0x34, 0x29: 0x35,  # ; ' `
    0x33: 0x36, 0x34: 0x37, 0x35: 0x38,  # , . /
    0x1C: 0x28,   # Enter
    0x01: 0x29,   # Esc
    0x0E: 0x2A,   # Backspace
    0x0F: 0x2B,   # Tab
    0x39: 0x2C,   # Space
    0x3A: 0x39,   # CapsLock
    **{0x3B + i: 0x3A + i for i in range(10)},  # F1..F10
    0x57: 0x44, 0x58: 0x45,                     # F11 F12
    0xE052: 0x49, 0xE047: 0x4A, 0xE049: 0x4B,   # Insert Home PageUp
    0xE053: 0x4C, 0xE04F: 0x4D, 0xE051: 0x4E,   # Delete End PageDown
    0xE04D: 0x4F, 0xE04B: 0x50, 0xE050: 0x51, 0xE048: 0x52,  # стрілки
}

# Модифікатори в HID-звіті живуть не в списку клавіш, а в окремому байті-масці.
# Win/GUI і правий Ctrl теж сюди: без них клавіша Win (0xE05B) не має ані
# запису в _HID_USAGE, ані біта модифікатора, і на серійному транспорті
# `Win+D` тихо зникав би. SendInput і Interception їх обробляють інакше.
SC_RCTRL = 0xE01D
SC_LWIN = 0xE05B
SC_RWIN = 0xE05C
_HID_MODIFIER: Dict[int, int] = {
    SC_LCTRL: 0x01,
    SC_LSHIFT: 0x02,
    SC_LALT: 0x04,
    SC_LWIN: 0x08,
    SC_RCTRL: 0x10,
    SC_RSHIFT: 0x20,
    SC_RALT: 0x40,
    SC_RWIN: 0x80,
}


class SerialHidTransport:
    """Поверх 3: команди мікроконтролеру, який є для системи USB-клавіатурою.

    Прошивка — `tools/firmware/hid_keyboard/hid_keyboard.ino`.

    Кадр — 8 байтів, рівно HID-звіт boot-протоколу клавіатури:

        b'K' mods 0 k1 k2 k3 k4 k5 k6

    Шість клавіш — обмеження boot-протоколу (6KRO). Для набору тексту цього з
    величезним запасом: одночасно натиснутими в накладанні бувають дві-три.
    Сьома й далі мовчки відкидаються — так само, як їх відкидає більшість
    реальних мембранних клавіатур.

    Потребує `pyserial` (`pip install pyserial`).
    """

    MAX_KEYS = 6

    def __init__(self, port: str, baudrate: int = 500000, timeout: float = 1.0) -> None:
        try:
            import serial  # noqa: PLC0415 — залежність опційна
        except ImportError as exc:
            raise RuntimeError(
                "SerialHidTransport потребує pyserial: pip install pyserial"
            ) from exc

        self._port = serial.Serial(port, baudrate=baudrate, timeout=timeout)

    def set_keys(self, pressed: FrozenSet[int]) -> None:
        mods = 0
        usages = []
        for scan in sorted(pressed):
            modifier = _HID_MODIFIER.get(scan)
            if modifier is not None:
                mods |= modifier
                continue
            usage = _HID_USAGE.get(scan)
            if usage is not None and len(usages) < self.MAX_KEYS:
                usages.append(usage)

        usages += [0] * (self.MAX_KEYS - len(usages))
        self._port.write(b"K" + struct.pack("BB6B", mods, 0, *usages))

    def send_text(self, ch: str) -> None:
        raise ValueError(
            f"Символ {ch!r} відсутній у активній розкладці, а USB-клавіатура шле "
            "лише коди клавіш. Перемкни розкладку у вікні-отримувачі."
        )

    def close(self) -> None:
        self._port.close()


class RecordingTransport:
    """Нічого не робить, лише записує стани. Для тестів без заліза й без фокуса.

    `send_text` теж лише записується: до цієї правки запасний Unicode-шлях
    ішов у `SendInput` напряму, минаючи транспорт, і «офлайн» тест з
    українською розкладкою друкував латиницю в реальне вікно.
    """

    def __init__(self) -> None:
        self.calls = []
        self.texts = []

    def set_keys(self, pressed: FrozenSet[int]) -> None:
        self.calls.append(frozenset(pressed))

    def send_text(self, ch: str) -> None:
        self.texts.append(ch)


_transport: Optional[Transport] = None


def get_transport() -> Transport:
    """Поточний транспорт; за замовчуванням — `SendInput`."""
    global _transport
    if _transport is None:
        _transport = SendInputTransport()
    return _transport


def set_transport(transport: Optional[Transport]) -> None:
    """Замінює транспорт. `None` повертає типовий."""
    global _transport
    _transport = transport
