"""
Транспорт — куди саме йдуть згенеровані події миші.

Уся людяність (траєкторія, тайминги, почерк) не залежить від того, як подія
потрапляє в систему. А от **чи видно, що вона згенерована**, залежить лише
від цього. Тому транспорт винесено в окремий шар, який можна замінити.

Три поверхи вводу, знизу вгору:

    1. Повідомлення вікну (`PostMessage(WM_LBUTTONDOWN)`) — курсор не рухається,
       подію бачить лише конкретне вікно. Тут не реалізовано: рівень надто
       високий, ігри на Raw Input такого просто не бачать.

    2. Системна черга (`SendInput`) — `SendInputTransport` нижче. Найнижче,
       куди можна дістати з користувацького режиму. Курсор рухається реально,
       подію бачать усі. Windows позначає такі події прапорцем
       `LLMHF_INJECTED`, і прибрати цю позначку звідси неможливо.

    3. Рівень драйвера — `InterceptionTransport`. Події вставляються нижче за
       шар, що ставить `LLMHF_INJECTED`. Потребує встановленого драйвера
       Interception і прав адміністратора.

    4. Рівень пристрою — `SerialHidTransport`. Події формує зовнішній
       мікроконтролер, який для системи є звичайною USB-мишею. Прапорця немає,
       бо система не має чого позначати: з її точки зору це залізо.

Зворотний зв'язок у `emitter.py` працює з будь-яким транспортом: позиція
курсора читається через `GetCursorPos` незалежно від того, хто його зрушив.
Тобто перехід на поверх 3 не потребує змін ні в траєкторії, ні в регуляторі.
"""
import ctypes
import struct
from typing import Optional, Protocol

from . import win_api

__all__ = [
    "Transport",
    "SendInputTransport",
    "SerialHidTransport",
    "InterceptionTransport",
    "RecordingTransport",
    "get_transport",
    "set_transport",
    "BUTTON_LEFT",
    "BUTTON_RIGHT",
    "BUTTON_MIDDLE",
]

# Бітова маска стану кнопок — як у HID-звіті справжньої миші.
BUTTON_LEFT = 0x01
BUTTON_RIGHT = 0x02
BUTTON_MIDDLE = 0x04

_FLAGS_DOWN = {
    BUTTON_LEFT: win_api.MOUSEEVENTF_LEFTDOWN,
    BUTTON_RIGHT: win_api.MOUSEEVENTF_RIGHTDOWN,
    BUTTON_MIDDLE: win_api.MOUSEEVENTF_MIDDLEDOWN,
}
_FLAGS_UP = {
    BUTTON_LEFT: win_api.MOUSEEVENTF_LEFTUP,
    BUTTON_RIGHT: win_api.MOUSEEVENTF_RIGHTUP,
    BUTTON_MIDDLE: win_api.MOUSEEVENTF_MIDDLEUP,
}


class Transport(Protocol):
    """Інтерфейс транспорту. Стан кнопок — маска, як у HID-звіті."""

    def move(self, dx: int, dy: int) -> None: ...
    def set_buttons(self, mask: int) -> None: ...
    def wheel(self, clicks: int, horizontal: bool = False) -> None: ...


class _ButtonState:
    """Спільна для транспортів логіка: маска -> події натискання/відпускання."""

    def __init__(self) -> None:
        self._mask = 0

    def diff(self, mask: int):
        """Повертає (натиснуті, відпущені) кнопки при переході до `mask`."""
        pressed = mask & ~self._mask
        released = self._mask & ~mask
        self._mask = mask
        return pressed, released


class SendInputTransport:
    """Поверх 2: події через `SendInput`. Типовий транспорт, нічого не потребує."""

    def __init__(self) -> None:
        self._state = _ButtonState()

    def move(self, dx: int, dy: int) -> None:
        win_api.send_mouse_move_relative(dx, dy)

    def set_buttons(self, mask: int) -> None:
        pressed, released = self._state.diff(mask)
        # Відпускання йдуть першими: якщо в одному такті одна кнопка
        # відпускається, а інша натискається, саме такий порядок дає
        # коректний стан і не створює миті, коли натиснуті обидві.
        for button, flag in _FLAGS_UP.items():
            if released & button:
                win_api.send_mouse_event(flag)
        for button, flag in _FLAGS_DOWN.items():
            if pressed & button:
                win_api.send_mouse_event(flag)

    def wheel(self, clicks: int, horizontal: bool = False) -> None:
        win_api.send_wheel(clicks, horizontal)


class SerialHidTransport:
    """Поверх 3: команди мікроконтролеру, який є для системи USB-мишею.

    Прошивка — `tools/firmware/hid_mouse/hid_mouse.ino`. Протокол навмисно
    двійковий і фіксованої довжини: текстовий на 1000 Гц не встигає, бо
    рядок у десяток символів на 115200 бод займає близько мілісекунди —
    рівно період такту.

    Кадр — 3 байти: команда і два байти корисних даних.

        b'M' dx dy   зміщення, по int8 на вісь (як у HID-звіті)
        b'B' mask 0  стан кнопок
        b'W' clicks 0  колесо
        b'H' clicks 0  горизонтальне колесо (AC Pan)

    Зміщення більші за 127 розбиваються на кілька кадрів — так само, як
    справжня миша розбиває швидкий рух на кілька звітів.

    Потребує `pyserial` (`pip install pyserial`).
    """

    def __init__(self, port: str, baudrate: int = 500000, timeout: float = 1.0) -> None:
        try:
            import serial  # noqa: PLC0415 — залежність опційна, тягнемо лише за потреби
        except ImportError as exc:
            raise RuntimeError(
                "SerialHidTransport потребує pyserial: pip install pyserial"
            ) from exc

        self._port = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        self._state = _ButtonState()

    def _send(self, command: bytes, a: int = 0, b: int = 0) -> None:
        self._port.write(command + struct.pack("bb", a, b))

    def move(self, dx: int, dy: int) -> None:
        # HID-звіт несе по одному знаковому байту на вісь; довгий стрибок
        # фізична миша теж віддає кількома звітами поспіль.
        while dx or dy:
            step_x = max(-127, min(127, dx))
            step_y = max(-127, min(127, dy))
            self._send(b"M", step_x, step_y)
            dx -= step_x
            dy -= step_y

    def set_buttons(self, mask: int) -> None:
        self._send(b"B", mask)

    def wheel(self, clicks: int, horizontal: bool = False) -> None:
        # Горизонтальне колесо — окрема вісь у HID-звіті (AC Pan), тому й
        # команда окрема.
        command = b"H" if horizontal else b"W"
        while clicks:
            step = max(-127, min(127, clicks))
            self._send(command, step)
            clicks -= step

    def close(self) -> None:
        self._port.close()


class InterceptionTransport:
    """Поверх 3: ін'єкція через драйвер-фільтр Interception.

    Драйвер стоїть у стеку пристроїв нижче за шар, який позначає події
    прапорцем `LLMHF_INJECTED`, тому для системи вони приходять як звичайний
    ввід.

    Потребує окремого встановлення (адміністратор, перезавантаження):
    драйвер ставиться командою `install-interception.exe /install` з релізу
    проєкту Interception, поруч має лежати `interception.dll` тієї ж
    розрядності, що й Python.

    Args:
        device: Номер пристрою миші (11..20 за угодою Interception).
            За замовчуванням береться перший, який драйвер вважає мишею.
        dll_path: Шлях до `interception.dll`, якщо її немає поруч із процесом.

    Note:
        Цей клас написаний за документованим API драйвера, але **не
        перевірений на живому залізі** — на машині, де він писався, драйвер
        не встановлений. Перший запуск варто зробити з увімкненим
        `mouse_capture.py`: колонка `injected` одразу покаже, чи справді
        події пішли нижче.
    """

    # Прапорці стану з interception.h.
    _STATE_DOWN = {BUTTON_LEFT: 0x001, BUTTON_RIGHT: 0x004, BUTTON_MIDDLE: 0x010}
    _STATE_UP = {BUTTON_LEFT: 0x002, BUTTON_RIGHT: 0x008, BUTTON_MIDDLE: 0x020}
    _STATE_WHEEL = 0x400
    _STATE_HWHEEL = 0x800
    _MOVE_RELATIVE = 0x000

    class _MouseStroke(ctypes.Structure):
        _fields_ = [
            ("state", ctypes.c_ushort),
            ("flags", ctypes.c_ushort),
            ("rolling", ctypes.c_short),
            ("x", ctypes.c_int),
            ("y", ctypes.c_int),
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

        # Типи оголошуємо явно: без цього ctypes обріже контекст-вказівник
        # до 32 біт на x64 і виклики мовчки нічого не робитимуть.
        self._dll.interception_create_context.restype = ctypes.c_void_p
        self._dll.interception_destroy_context.argtypes = (ctypes.c_void_p,)
        self._dll.interception_is_mouse.argtypes = (ctypes.c_int,)
        self._dll.interception_is_mouse.restype = ctypes.c_int
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

        self._device = device if device is not None else self._find_mouse()
        self._state = _ButtonState()

    def _find_mouse(self) -> int:
        """Перший пристрій, який драйвер вважає мишею (діапазон 11..20)."""
        for candidate in range(11, 21):
            if self._dll.interception_is_mouse(candidate):
                return candidate
        raise RuntimeError("Interception не бачить жодної миші в діапазоні пристроїв 11..20.")

    def _stroke(self, state: int, x: int = 0, y: int = 0, rolling: int = 0) -> None:
        stroke = self._MouseStroke(
            state=state, flags=self._MOVE_RELATIVE, rolling=rolling,
            x=x, y=y, information=0,
        )
        self._dll.interception_send(self._context, self._device, ctypes.byref(stroke), 1)

    def move(self, dx: int, dy: int) -> None:
        if dx or dy:
            self._stroke(0, dx, dy)

    def set_buttons(self, mask: int) -> None:
        pressed, released = self._state.diff(mask)
        state = 0
        # Відпускання і натискання різних кнопок можна віддати одним
        # пакетом: у драйвера це поле бітове, як і в HID-звіті.
        for button, flag in self._STATE_UP.items():
            if released & button:
                state |= flag
        for button, flag in self._STATE_DOWN.items():
            if pressed & button:
                state |= flag
        if state:
            self._stroke(state)

    def wheel(self, clicks: int, horizontal: bool = False) -> None:
        if clicks:
            state = self._STATE_HWHEEL if horizontal else self._STATE_WHEEL
            self._stroke(state, rolling=clicks * 120)

    def close(self) -> None:
        if getattr(self, "_context", None):
            self._dll.interception_destroy_context(self._context)
            self._context = None


class RecordingTransport:
    """Нічого не робить, лише записує виклики. Для тестів без заліза й без курсора."""

    def __init__(self) -> None:
        self.calls = []
        self._state = _ButtonState()

    def move(self, dx: int, dy: int) -> None:
        self.calls.append(("move", dx, dy))

    def set_buttons(self, mask: int) -> None:
        self.calls.append(("buttons", mask))

    def wheel(self, clicks: int, horizontal: bool = False) -> None:
        self.calls.append(("wheel", clicks, horizontal))


_transport: Optional[Transport] = None


def get_transport() -> Transport:
    """Поточний транспорт; за замовчуванням — `SendInput`."""
    global _transport
    if _transport is None:
        _transport = SendInputTransport()
    return _transport


def set_transport(transport: Optional[Transport]) -> None:
    """Замінює транспорт. `None` повертає типовий.

    Стан кнопок переноситься у новий транспорт. Без цього перемикання під
    час утримання лишало б кнопку затиснутою назавжди: новий транспорт
    вважає, що все відпущено, тому на команду «відпустити» він не бачить
    жодної зміни і не шле нічого. Найнеприємніше, що спрацьовувало це саме
    на аварійному відкаті `set_transport(None)` після збою — і саме `scroll`,
    який тримає праву кнопку.
    """
    global _transport

    previous = _transport
    _transport = transport

    held = getattr(previous, "_state", None)
    if held is not None and held._mask:
        get_transport().set_buttons(held._mask)
