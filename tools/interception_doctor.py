"""
Діагностика драйвера Interception: що є, що ні, і що саме не так.

Драйвер Interception ламається тихо. Найчастіші випадки — і всі вони
виглядають однаково («нічого не відбувається»):

  * взято DLL не тієї розрядності, що Python. Найпоширеніша помилка: у
    релізі лежать і x86, і x64, а ctypes на невідповідній просто впаде або,
    гірше, завантажиться і мовчатиме;
  * драйвер поставлено, але систему не перезавантажено — контекст
    створюється, а події нікуди не йдуть;
  * драйвер не поставлено взагалі, лише скопійовано DLL;
  * драйвер бачить пристрої, але не той, у який ти шлеш.

Скрипт нічого не встановлює і не змінює — лише читає й повідомляє.

    python tools/interception_doctor.py
"""
import ctypes
import platform
import struct
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Значення поля Machine у заголовку PE.
_MACHINE = {0x014C: "x86 (32 біти)", 0x8664: "x64 (64 біти)", 0xAA64: "ARM64"}

# Interception ставить свої драйвери під цими іменами.
_DRIVER_FILES = ("keyboard.sys", "mouse.sys")


def pe_architecture(path: Path) -> str:
    """Розрядність PE-файлу, прочитана із заголовка."""
    try:
        with open(path, "rb") as fh:
            if fh.read(2) != b"MZ":
                return "не PE-файл"
            fh.seek(0x3C)
            pe_offset = struct.unpack("<I", fh.read(4))[0]
            fh.seek(pe_offset)
            if fh.read(4) != b"PE\0\0":
                return "пошкоджений PE-заголовок"
            machine = struct.unpack("<H", fh.read(2))[0]
        return _MACHINE.get(machine, f"невідома (0x{machine:04X})")
    except OSError as exc:
        return f"не вдалося прочитати: {exc}"


def check_python() -> str:
    bits = 64 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32
    print(f"Python:  {platform.python_version()}, {bits} біти")
    return f"x{'64' if bits == 64 else '86'}"


def check_dll(path: Path) -> bool:
    print(f"\nDLL:     {path}")
    if not path.exists():
        print("         НЕ ЗНАЙДЕНО")
        print("         Поклади interception.dll поруч із цим скриптом або в корінь проєкту.")
        return False

    arch = pe_architecture(path)
    print(f"         розрядність: {arch}")

    want_64 = ctypes.sizeof(ctypes.c_void_p) == 8
    matches = ("x64" in arch) if want_64 else ("x86" in arch)
    if not matches:
        print("         РОЗРЯДНІСТЬ НЕ ЗБІГАЄТЬСЯ з Python — візьми іншу DLL з релізу.")
        return False
    return True


def check_driver_files() -> None:
    system32 = Path(r"C:\Windows\System32\drivers")
    print("\nФайли драйвера:")
    found = 0
    for name in _DRIVER_FILES:
        target = system32 / name
        if target.exists():
            print(f"         {target} — є")
            found += 1
        else:
            print(f"         {target} — немає")
    if not found:
        print("         Драйвер не встановлено. Копіювання DLL цього не замінює:")
        print("         потрібен install-interception.exe /install від адміністратора")
        print("         і ПЕРЕЗАВАНТАЖЕННЯ.")


def check_context(path: Path) -> None:
    print("\nДрайвер у роботі:")
    try:
        dll = ctypes.WinDLL(str(path))
    except OSError as exc:
        print(f"         DLL не завантажилась: {exc}")
        return

    dll.interception_create_context.restype = ctypes.c_void_p
    dll.interception_destroy_context.argtypes = (ctypes.c_void_p,)
    dll.interception_is_mouse.argtypes = (ctypes.c_int,)
    dll.interception_is_mouse.restype = ctypes.c_int
    dll.interception_is_keyboard.argtypes = (ctypes.c_int,)
    dll.interception_is_keyboard.restype = ctypes.c_int

    context = dll.interception_create_context()
    if not context:
        print("         interception_create_context повернув NULL.")
        print("         Драйвер не встановлений, або система не перезавантажувалась,")
        print("         або процесу бракує прав.")
        return

    try:
        mice = [d for d in range(11, 21) if dll.interception_is_mouse(d)]
        keyboards = [d for d in range(1, 11) if dll.interception_is_keyboard(d)]
        print(f"         контекст створено")
        print(f"         миші: {mice if mice else 'жодної'}")
        print(f"         клавіатури: {keyboards if keyboards else 'жодної'}")

        if mice:
            print(f"\n         Готово. Використання:")
            print(f"           mouse.set_transport(mouse.InterceptionTransport(device={mice[0]}))")
            print(f"         Перевір результат через:")
            print(f"           python tools/mouse_capture.py bot.jsonl --seconds 15")
            print(f"         У колонці injected мають бути нулі.")
        else:
            print("\n         Драйвер працює, але жодної миші не бачить.")
            print("         Зазвичай це означає, що після встановлення не було")
            print("         перезавантаження, або миша підключилась пізніше за драйвер.")
    finally:
        dll.interception_destroy_context(context)


def main() -> int:
    if not sys.platform.startswith("win"):
        print("Потрібна Windows.", file=sys.stderr)
        return 1

    print("=" * 62)
    print("Діагностика Interception")
    print("=" * 62)

    check_python()

    here = Path(__file__).resolve().parent
    candidates = [here / "interception.dll", here.parent / "interception.dll",
                  Path.cwd() / "interception.dll"]
    dll_path = next((c for c in candidates if c.exists()), candidates[0])

    ok = check_dll(dll_path)
    check_driver_files()
    if ok:
        check_context(dll_path)

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
