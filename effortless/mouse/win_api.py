"""
Низькорівнева обгортка над WinAPI (user32) для симуляції руху миші та кліків через SendInput.

Ключове рішення модуля — рух **відносними** дельтами (`MOUSEEVENTF_MOVE` без
`MOUSEEVENTF_ABSOLUTE`). Фізична миша не знає, де знаходиться курсор: вона шле
кількість "каунтів" зміщення, а Windows сама застосовує до них криву
прискорення (pointer ballistics) і рухає курсор. Абсолютне позиціонування —
режим, у якому працюють дигітайзери й тачскріни, і в Raw Input воно позначене
окремим прапорцем `MOUSE_MOVE_ABSOLUTE`. Тобто рух абсолютними координатами
не просто "виглядає неприродно" — він структурно інший, і відрізнити його від
миші можна одним полем, без жодного аналізу траєкторії.

Наслідок відносного режиму: сказати "стань рівно в (x, y)" більше не можна,
бо скільки пікселів пройде курсор на N каунтів — залежить від швидкості
вказівника й того, чи ввімкнена "підвищена точність". Тому позиціонування
робиться зі зворотним зв'язком — див. `emitter.py`.
"""
import ctypes
from ctypes import wintypes
from typing import Tuple

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetTickCount.restype = wintypes.DWORD
kernel32.GetTickCount.argtypes = ()

INPUT_MOUSE = 0

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

WHEEL_DELTA = 120

SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

SPI_GETMOUSE = 0x0003
SPI_GETMOUSESPEED = 0x0070

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def _send(mi: _MOUSEINPUT) -> None:
    # Мітка часу події. З `time=0` низькорівневий хук бачить нуль у КОЖНІЙ
    # події — а справжня подія (і ретрансльована через Parsec теж) несе
    # реальний GetTickCount. Нуль у полі часу — однорядкова ознака SendInput,
    # видніша за будь-яку статистику. Ставимо реальний тік у момент надсилання:
    # ми шлемо в реальному часі, тому значення природно зростає від події до
    # події, як у фізичного пристрою.
    mi.time = kernel32.GetTickCount()
    inp = _INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=mi))

    # Результат перевіряємо обов'язково. SendInput повертає нуль, коли ввід
    # заблокував UIPI — тобто коли активне вікно належить процесу з вищими
    # правами (запущений «від адміністратора» застосунок) або піднято
    # захищений робочий стіл. Без перевірки бот у такому разі просто нічого
    # не робить, годинами, і виглядає це як «працює, але не клікає».
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp)) == 0:
        error = ctypes.get_last_error()
        raise OSError(
            f"SendInput відхилено системою (код {error}). Найчастіша причина — "
            "активне вікно належить процесу з вищими правами: запусти скрипт "
            "від адміністратора або перемкнись на звичайне вікно."
        )


def get_cursor_pos() -> Tuple[int, int]:
    """Повертає поточні координати курсора миші."""
    pt = _POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def get_screen_size() -> Tuple[int, int]:
    """Розмір основного монітора в пікселях."""
    return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)


def get_virtual_screen() -> Tuple[int, int, int, int]:
    """Межі віртуального робочого столу (left, top, width, height).

    На кількох моніторах координати можуть бути від'ємними — саме тому
    абсолютне позиціонування по `GetSystemMetrics(0/1)`, як це роблять
    більшість прикладів у мережі, ламається на другому екрані.
    """
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def get_pointer_speed() -> int:
    """Повертає системну швидкість вказівника (повзунок 1..20, типово 10)."""
    speed = ctypes.c_int()
    if not user32.SystemParametersInfoW(SPI_GETMOUSESPEED, 0, ctypes.byref(speed), 0):
        return 10
    return speed.value or 10


def is_pointer_acceleration_enabled() -> bool:
    """Чи ввімкнена "Підвищена точність встановлення вказівника"."""
    params = (ctypes.c_int * 3)()
    if not user32.SystemParametersInfoW(SPI_GETMOUSE, 0, ctypes.byref(params), 0):
        return False
    return bool(params[2])


def send_mouse_move_relative(dx: int, dy: int) -> None:
    """Зміщує курсор на (dx, dy) "каунтів" — так, як це робить фізична миша.

    Скільки це вийде пікселів, вирішує сама Windows (швидкість вказівника +
    крива прискорення), тому викликати це наосліп для точного позиціонування
    не можна — потрібен зворотний зв'язок по `get_cursor_pos()`.
    """
    if dx == 0 and dy == 0:
        return
    # Поле time тут не задаємо — його виставляє `_send` реальним GetTickCount.
    _send(_MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=MOUSEEVENTF_MOVE, dwExtraInfo=0))


def send_mouse_move(x: int, y: int) -> None:
    """Ставить курсор в абсолютні координати (x, y).

    Лишено як запасний шлях (телепортація в потрібну точку, відновлення після
    збою зворотного зв'язку). Для звичайного руху використовуй відносний
    режим: абсолютні події видно в Raw Input за прапорцем `MOUSE_MOVE_ABSOLUTE`.
    """
    vx, vy, vw, vh = get_virtual_screen()
    abs_x = int(round((x - vx) * 65535 / max(vw - 1, 1)))
    abs_y = int(round((y - vy) * 65535 / max(vh - 1, 1)))

    _send(
        _MOUSEINPUT(
            dx=abs_x,
            dy=abs_y,
            mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
            dwExtraInfo=0,  # time виставляє _send реальним GetTickCount
        )
    )


def send_mouse_event(flag: int, data: int = 0) -> None:
    """Надсилає low-level подію миші (down/up тощо) через SendInput."""
    # time виставляє _send реальним GetTickCount.
    _send(_MOUSEINPUT(dx=0, dy=0, mouseData=data, dwFlags=flag, dwExtraInfo=0))


def send_wheel(clicks: int, horizontal: bool = False) -> None:
    """Крутить колесо на `clicks` "клацань" (одне клацання = WHEEL_DELTA)."""
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    send_mouse_event(flag, ctypes.c_ulong(clicks * WHEEL_DELTA).value)
