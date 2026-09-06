"""
Низькорівнева обгортка над user32 для клавіатурного вводу через SendInput.

Ключове рішення модуля — події **скан-кодами** (`KEYEVENTF_SCANCODE`), а не
віртуальними клавішами і тим більше не `KEYEVENTF_UNICODE`.

Три способи сказати «натиснута клавіша A», і вони структурно різні:

    1. `KEYEVENTF_UNICODE` — так працюють `pyautogui.write`, `pynput` для
       довільного тексту і більшість прикладів у мережі. Windows підставляє
       віртуальну клавішу `VK_PACKET` (0xE7) і скан-код 0. У низькорівневому
       хуці це видно однією умовою `vkCode == 0xE7`: жодна фізична клавіатура
       такого не породжує ніколи. Аналог `MOUSE_MOVE_ABSOLUTE` для миші —
       ознака не «підозріла», а однозначна.

    2. Віртуальна клавіша без скан-коду — Windows домальовує скан-код сама,
       але для розширених клавіш (стрілки, правий Alt) робить це не завжди
       правильно, і в потоці подій з'являються комбінації vk/scan, яких на
       залізі не буває.

    3. Скан-код (`KEYEVENTF_SCANCODE`, `wVk = 0`) — те, що реально приїжджає
       від клавіатури: драйвер отримує позицію клавіші, а віртуальну клавішу
       обчислює сам за поточною розкладкою. Саме цей режим тут.

Прапорець `LLKHF_INJECTED` виставляється системою в усіх трьох випадках і
з користувацького режиму не знімається — так само, як для миші. Знімає його
лише перехід на рівень пристрою (`transport.SerialHidTransport`).

Друге рішення — `send_batch`: усі події, що мають статися «одночасно»
(Shift + літера), їдуть одним викликом `SendInput` з масивом. Фізична
клавіатура шле HID-звіт зі станом усіх клавіш одразу, тому одночасні зміни
приходять з однією часовою міткою; окремі виклики дали б різницю в кілька
десятків мікросекунд там, де на залізі вона рівно нульова.
"""
import ctypes
from ctypes import wintypes
from typing import Iterable, Optional, Sequence, Tuple

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetTickCount.restype = wintypes.DWORD
kernel32.GetTickCount.argtypes = ()

INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MAPVK_VK_TO_VSC_EX = 4
MAPVK_VSC_TO_VK_EX = 3

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_CAPITAL = 0x14

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
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

user32.VkKeyScanExW.argtypes = (ctypes.c_wchar, wintypes.HKL)
user32.VkKeyScanExW.restype = ctypes.c_short

user32.MapVirtualKeyExW.argtypes = (wintypes.UINT, wintypes.UINT, wintypes.HKL)
user32.MapVirtualKeyExW.restype = wintypes.UINT

user32.GetKeyState.argtypes = (ctypes.c_int,)
user32.GetKeyState.restype = ctypes.c_short

# Без явних restype ctypes вважає, що функція повертає C int. Для HKL і HWND
# на 64 бітах це означає обрізання до 32 біт зі знаком: `GetKeyboardLayout`
# повертає від'ємне число, `VkKeyScanEx` з таким «дескриптором» відповідає -1
# на кожен символ, і весь текст мовчки їде через Unicode-підстановку.
user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype = wintypes.HWND

user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
user32.GetKeyboardLayout.restype = wintypes.HKL


# ----------------------------------------------------------------------
# Розкладка
# ----------------------------------------------------------------------

def active_layout() -> int:
    """HKL розкладки **вікна на передньому плані**, а не власного потоку.

    Береться саме чуже вікно: розкладка в Windows своя для кожного потоку, і
    якщо питати `GetKeyboardLayout(0)`, то в момент, коли активний Word з
    українською, ми порахуємо скан-коди по англійській розкладці власного
    процесу — і надрукуємо не те.
    """
    hwnd = user32.GetForegroundWindow()
    thread_id = user32.GetWindowThreadProcessId(hwnd, None) if hwnd else 0
    # HKL приходить як вказівник; 0 буває, якщо вікно зникло між викликами —
    # тоді питаємо розкладку власного потоку.
    return int(user32.GetKeyboardLayout(thread_id) or user32.GetKeyboardLayout(0) or 0)


def scan_of_vk(vk: int, layout: Optional[int] = None) -> int:
    """Скан-код віртуальної клавіші. Для розширених клавіш повертає 0xE0XX."""
    return user32.MapVirtualKeyExW(vk, MAPVK_VK_TO_VSC_EX, layout or active_layout())


def vk_of_char(ch: str, layout: Optional[int] = None) -> Optional[Tuple[int, int]]:
    """(віртуальна клавіша, стан модифікаторів) для символу в заданій розкладці.

    Стан модифікаторів — бітова маска зі старшого байта `VkKeyScanEx`:
    1 = Shift, 2 = Ctrl, 4 = Alt (Ctrl+Alt разом означає AltGr).

    Returns:
        None, якщо символу немає в розкладці — тоді єдиний спосіб його ввести
        це `send_unicode`, з усіма наслідками (див. докстрінг модуля).
    """
    result = user32.VkKeyScanExW(ch, layout or active_layout())
    if result == -1:
        return None
    return result & 0xFF, (result >> 8) & 0xFF


def is_toggled(vk: int) -> bool:
    """Чи ввімкнений перемикач (CapsLock, NumLock) — молодший біт GetKeyState."""
    return bool(user32.GetKeyState(vk) & 0x0001)


# ----------------------------------------------------------------------
# Відправка подій
# ----------------------------------------------------------------------

def _make_input(scancode: int, key_up: bool) -> _INPUT:
    """Одна подія клавіші у форматі SendInput.

    Скан-коди розширених клавіш приходять від `MapVirtualKeyEx` як 0xE0XX;
    у `wScan` кладеться лише молодший байт, а префікс 0xE0 виражається
    прапорцем `KEYEVENTF_EXTENDEDKEY` — рівно так, як його передає драйвер.
    """
    flags = KEYEVENTF_SCANCODE
    if scancode & 0xE000 == 0xE000:
        flags |= KEYEVENTF_EXTENDEDKEY
    if key_up:
        flags |= KEYEVENTF_KEYUP

    # Реальна мітка часу замість нуля: з `time=0` низькорівневий хук бачить
    # нуль у кожній події, тоді як справжня подія (і ретрансльована через
    # Parsec теж) несе GetTickCount. Нуль у полі часу — однорядкова ознака
    # SendInput. Події одного HID-звіту на залізі мають спільний тік, тому
    # спільна мітка на весь пакет — це правильно, а не проблема.
    ki = _KEYBDINPUT(wVk=0, wScan=scancode & 0xFF, dwFlags=flags,
                     time=kernel32.GetTickCount(), dwExtraInfo=0)
    return _INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=ki))


def send_key(scancode: int, key_up: bool) -> None:
    """Надсилає одну подію клавіші скан-кодом."""
    send_batch(((scancode, key_up),))


def send_batch(events: Sequence[Tuple[int, bool]]) -> int:
    """Надсилає кілька подій одним викликом — з однією часовою міткою.

    Args:
        events: Послідовність (скан-код, key_up). Порядок зберігається:
            система обробляє масив як одну транзакцію, тому Shift у першому
            елементі гарантовано «раніший» за літеру в другому, хоча мітка
            часу в них спільна.

    Returns:
        Скільки подій система прийняла. Менше за довжину — вводом заволоділа
        інша програма (типово: активне вікно UAC або гра з блокуванням вводу).
    """
    if not events:
        return 0

    array = (_INPUT * len(events))(*(_make_input(scan, up) for scan, up in events))
    accepted = user32.SendInput(len(events), array, ctypes.sizeof(_INPUT))

    # Неповний прийом — не «дрібниця»: транспорт уже записав новий стан, і
    # далі він рахуватиме різниці від клавіш, яких система не бачила. Типова
    # причина — вікно з вищими правами на передньому плані (UIPI).
    if accepted != len(events):
        error = ctypes.get_last_error()
        raise OSError(
            f"SendInput прийняв {accepted} з {len(events)} подій (код {error}). "
            "Найчастіша причина — активне вікно належить процесу з вищими правами."
        )
    return accepted


def send_unicode(ch: str) -> None:
    """Запасний шлях для символів, яких немає в розкладці (емодзі, рідкі знаки).

    Свідомо помітний: породжує `VK_PACKET`, який у потоці подій видно одразу.
    Використовується лише там, де альтернативи немає взагалі — для звичайного
    тексту шлях інший (див. `layout.resolve_text`).
    """
    code = ord(ch)
    events = []
    for unit in (
        [code] if code <= 0xFFFF
        else [0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
    ):
        for key_up in (False, True):
            flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
            ki = _KEYBDINPUT(wVk=0, wScan=unit, dwFlags=flags,
                             time=kernel32.GetTickCount(), dwExtraInfo=0)
            events.append(_INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=ki)))

    array = (_INPUT * len(events))(*events)
    user32.SendInput(len(events), array, ctypes.sizeof(_INPUT))


def release_all(scancodes: Iterable[int]) -> None:
    """Аварійно відпускає перелічені клавіші. Для `finally` на випадку винятку."""
    events = [(scan, True) for scan in scancodes]
    if events:
        send_batch(events)
