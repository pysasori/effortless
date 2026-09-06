"""
Записувач подій клавіатури через низькорівневий хук WH_KEYBOARD_LL.

Це той самий механізм, яким антибот-перевірки дивляться на клавіатуру, тому
запис показує рівно те, що бачить перевіряльник:

    * точні часові мітки натискань і відпускань (звідки видно і сітку
      опитування, і накладання клавіш, і піки на кратних 15.6 мс);
    * скан-код і віртуальну клавішу окремо — саме тут видно `VK_PACKET`
      (0xE7), яким друкує `pyautogui.write` і будь-що на KEYEVENTF_UNICODE;
    * **прапорець LLKHF_INJECTED** — виставляється системою для всього, що
      прийшло через `SendInput`.

Про останній варто сказати окремо: обійти його з користувацького режиму не
можна в принципі. Якщо перевірка дивиться саме на нього, жодна якість ритму
не допоможе — тому перше, що має сенс зробити, це записати свого бота цим
скриптом і подивитись колонку `injected`. Якщо там одиниці, задача не в ритмі.

Скрипт **не** записує, які саме символи набрано: у файл ідуть скан-коди
без прив'язки до тексту, а сам запис ведеться лише поки він запущений. Це
інструмент для вимірювання власного вводу, а не для збору чужого.

Використання:

    python tools/keyboard_capture.py human.jsonl --seconds 60
    python tools/keyboard_capture.py bot.jsonl --seconds 60

Зупинка — Esc або вичерпання часу.
"""
import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLKHF_ALTDOWN = 0x20
LLKHF_UP = 0x80

VK_ESCAPE = 0x1B
VK_PACKET = 0xE7
PM_REMOVE = 0x0001

_DOWN_MESSAGES = (WM_KEYDOWN, WM_SYSKEYDOWN)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


HOOKPROC = ctypes.CFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT)
)

# Типи оголошуємо явно: за замовчуванням ctypes вважає результат int (32 біти)
# і на x64 мовчки обрізає хендли, після чого SetWindowsHookExW падає з
# ERROR_MOD_NOT_FOUND (126) на цілком коректному виклику.
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HMODULE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK

user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.CallNextHookEx.argtypes = (
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT)
)
user32.CallNextHookEx.restype = ctypes.c_long


def capture(path: str, seconds: float) -> int:
    """Пише події клавіатури у JSONL-файл. Повертає кількість записаних подій."""
    events = []
    started = time.perf_counter()

    def on_event(code, message, data):
        # Обробник хука має відпрацьовувати швидко: якщо він думає довше за
        # LowLevelHooksTimeout, система мовчки знімає хук. Тому тут лише
        # складання в список, без форматування чи запису на диск.
        if code >= 0:
            info = data.contents
            events.append(
                (
                    time.perf_counter() - started,
                    int(message),
                    int(info.vkCode),
                    int(info.scanCode),
                    int(info.flags),
                    int(info.time),
                )
            )
        return user32.CallNextHookEx(None, code, message, data)

    callback = HOOKPROC(on_event)  # посилання тримаємо живим, інакше GC зніме хук
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        raise OSError(f"SetWindowsHookExW не вдався: {ctypes.get_last_error()}")

    print(f"Запис {seconds:.0f} с. Друкуй звичайний текст. Esc — зупинити.")
    msg = wintypes.MSG()
    try:
        while time.perf_counter() - started < seconds:
            # Низькорівневий хук працює лише в потоці з чергою повідомлень.
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                break
            time.sleep(0.001)
    finally:
        user32.UnhookWindowsHookEx(hook)

    injected = packets = 0
    with open(path, "w", encoding="utf-8") as fh:
        for t, message, vk, scan, flags, sys_time in events:
            is_injected = bool(flags & LLKHF_INJECTED)
            injected += is_injected
            packets += vk == VK_PACKET
            fh.write(
                json.dumps(
                    {
                        "t": round(t, 6),
                        "down": int(message in _DOWN_MESSAGES),
                        "vk": vk,
                        "scan": scan,
                        "extended": int(bool(flags & LLKHF_EXTENDED)),
                        "injected": int(is_injected),
                        "sys_time": sys_time,
                    }
                )
                + "\n"
            )

    print(f"Записано {len(events)} подій -> {path}")
    if events:
        share = injected / len(events) * 100
        print(f"З них позначені як injected: {injected} ({share:.1f}%)")
        if packets:
            print(
                f"\n  УВАГА: {packets} подій з VK_PACKET (0xE7).\n"
                "  Так виглядає ввід через KEYEVENTF_UNICODE. Фізична клавіатура\n"
                "  такого не породжує ніколи — це видно однією умовою, без будь-якої\n"
                "  статистики."
            )
        if share > 50:
            print(
                "\n  УВАГА: більшість подій має прапорець injected.\n"
                "  Це означає, що джерелом був SendInput. Прибрати цей прапорець\n"
                "  з користувацького режиму неможливо — якщо перевірка дивиться\n"
                "  на нього, працювати треба не над ритмом."
            )
    return len(events)


def main() -> int:
    parser = argparse.ArgumentParser(description="Запис подій клавіатури через WH_KEYBOARD_LL")
    parser.add_argument("output", help="Куди писати (JSONL)")
    parser.add_argument("--seconds", type=float, default=60.0, help="Тривалість запису")
    args = parser.parse_args()

    if not sys.platform.startswith("win"):
        print("Потрібна Windows.", file=sys.stderr)
        return 1

    capture(args.output, args.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
