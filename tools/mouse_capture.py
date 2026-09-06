"""
Записувач подій миші через низькорівневий хук WH_MOUSE_LL.

Це той самий механізм, яким антибот-перевірки дивляться на мишу, тому запис
показує рівно те, що бачить перевіряльник:

    * точні часові мітки подій (звідки видно частоту опитування і те, чи
      злипаються інтервали в кратні 15.6 мс);
    * координати (звідки рахується траєкторія і профіль швидкості);
    * **прапорець LLMHF_INJECTED** — виставляється системою для всього, що
      прийшло через `SendInput`.

Про останній варто сказати окремо: обійти його з користувацького режиму
не можна в принципі. Якщо перевірка дивиться саме на нього, жодна якість
траєкторії не допоможе — тому перше, що має сенс зробити, це записати
свого бота цим скриптом і подивитись колонку `injected`. Якщо там одиниці,
задача не в траєкторії.

Використання:

    python tools/mouse_capture.py human.jsonl --seconds 30
    python tools/mouse_capture.py bot.jsonl --seconds 30

Зупинка — Esc або вичерпання часу.
"""
import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MOUSEWHEEL = 0x020A

LLMHF_INJECTED = 0x00000001
LLMHF_LOWER_IL_INJECTED = 0x00000002

VK_ESCAPE = 0x1B
PM_REMOVE = 0x0001

_MESSAGE_NAMES = {
    WM_MOUSEMOVE: "move",
    WM_LBUTTONDOWN: "left_down",
    WM_LBUTTONUP: "left_up",
    WM_RBUTTONDOWN: "right_down",
    WM_RBUTTONUP: "right_up",
    WM_MOUSEWHEEL: "wheel",
}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


HOOKPROC = ctypes.CFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(MSLLHOOKSTRUCT)
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

user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(MSLLHOOKSTRUCT))
user32.CallNextHookEx.restype = ctypes.c_long


def capture(path: str, seconds: float) -> int:
    """Пише події миші у JSONL-файл. Повертає кількість записаних подій."""
    events = []
    started = time.perf_counter()

    def on_event(code, message, data):
        # Обробник хука має відпрацьовувати швидко: якщо він думає довше за
        # LowLevelHooksTimeout, система мовчки знімає хук. Тому тут лише
        # складання в список, без будь-якого форматування чи запису на диск.
        if code >= 0:
            info = data.contents
            events.append(
                (
                    time.perf_counter() - started,
                    int(message),
                    info.pt.x,
                    info.pt.y,
                    info.flags,
                    info.time,
                    int(info.mouseData),
                )
            )
        return user32.CallNextHookEx(None, code, message, data)

    callback = HOOKPROC(on_event)  # посилання тримаємо живим, інакше GC зніме хук
    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        raise OSError(f"SetWindowsHookExW не вдався: {ctypes.get_last_error()}")

    print(f"Запис {seconds:.0f} с. Рухай мишею. Esc — зупинити.")
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

    injected = 0
    with open(path, "w", encoding="utf-8") as fh:
        for t, message, x, y, flags, sys_time, mouse_data in events:
            is_injected = bool(flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED))
            injected += is_injected
            fh.write(
                json.dumps(
                    {
                        "t": round(t, 6),
                        "type": _MESSAGE_NAMES.get(message, str(message)),
                        "x": x,
                        "y": y,
                        "injected": int(is_injected),
                        "sys_time": sys_time,
                        "wheel": ctypes.c_short(mouse_data >> 16).value if message == WM_MOUSEWHEEL else 0,
                    }
                )
                + "\n"
            )

    print(f"Записано {len(events)} подій -> {path}")
    if events:
        share = injected / len(events) * 100
        print(f"З них позначені як injected: {injected} ({share:.1f}%)")
        if share > 50:
            print(
                "\n  УВАГА: більшість подій має прапорець injected.\n"
                "  Це означає, що джерелом був SendInput. Прибрати цей прапорець\n"
                "  з користувацького режиму неможливо — якщо перевірка дивиться\n"
                "  на нього, працювати треба не над траєкторією."
            )
    return len(events)


def main() -> int:
    parser = argparse.ArgumentParser(description="Запис подій миші через WH_MOUSE_LL")
    parser.add_argument("output", help="Куди писати (JSONL)")
    parser.add_argument("--seconds", type=float, default=30.0, help="Тривалість запису")
    args = parser.parse_args()

    if not sys.platform.startswith("win"):
        print("Потрібна Windows.", file=sys.stderr)
        return 1

    capture(args.output, args.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
