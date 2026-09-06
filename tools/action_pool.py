"""
Відома послідовність дій для перевірки транспорту наскрізь — зокрема через Parsec.

Навіщо саме «відома»: щоб порівнювати не з теорією, а з фактом. Скрипт друкує
маніфест того, що надіслав (скільки символів, кліків, рухів), а `*_capture.py`
на машині-отримувачі ловить те, що долетіло. Різниця між надісланим і спійманим
і є відповідь на питання «що робить з подіями Parsec».

Порядок перевірки:

    Тест 1 — локально, на одній машині (базова лінія):
        Вікно 1:  python tools/keyboard_capture.py local_kb.jsonl --seconds 40
        Вікно 2:  python tools/action_pool.py --delay 6
        Очікування: injected = 100% (це SendInput, прапорець стоїть).

    Тест 2 — через Parsec (те, що підказав викладач):
        На МАШИНІ З ДЕТЕКТОРОМ (хост) запусти capture:
            python tools/keyboard_capture.py parsec_kb.jsonl --seconds 40
            python tools/mouse_capture.py    parsec_mouse.jsonl --seconds 40
        На СВОЄМУ ноуті (клієнт), у фокусі вікна Parsec:
            python tools/action_pool.py --delay 6
        Очікування (перевіримо разом): injected -> 0, VK_PACKET = 0,
        але, можливо, зіпсований час і absolute-миша.

Дії навмисно безпечні: текст іде в активне поле, кліки — по поточній позиції
курсора (нічого не тицяємо наосліп), рухи — дрібними відносними зсувами.
Перед запуском постав курсор у порожнє текстове поле (Блокнот).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import effortless.keyboard as keyboard   # noqa: E402
import effortless.mouse as mouse          # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SENTENCE = "the quick brown fox jumps over the lazy dog"


def run(text: str) -> dict:
    """Виконує послідовність і повертає маніфест того, що надіслано."""
    manifest = {"typed": text, "keys": 0, "clicks": 0, "moves": 0, "wheels": 0}

    # 1. Набір тексту з ритмом і одруками.
    keyboard.type_text(text)
    keyboard.press("enter")
    manifest["keys"] += len(text) + 1

    # 2. Комбінація + повторний набір: перевіряємо модифікатори через Parsec.
    keyboard.hotkey("ctrl", "a")
    keyboard.type_text(text.upper())
    manifest["keys"] += len(text)

    # 3. Рухи миші відносними зсувами (без прив'язки до координат екрана).
    for dx, dy in ((60, 25), (-40, 35), (30, -50), (-25, -20)):
        mouse.move_from_point(x=dx, y=dy)
        manifest["moves"] += 1

    # 4. Кліки по поточній позиції — нічого не тицяємо наосліп.
    for _ in range(3):
        mouse.click()
        manifest["clicks"] += 1

    # 5. Колесо.
    mouse.wheel(clicks=-3)
    mouse.wheel(clicks=2)
    manifest["wheels"] += 2

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Відома послідовність дій для перевірки транспорту")
    parser.add_argument("--delay", type=float, default=6.0,
                        help="Секунд на те, щоб перевести фокус у потрібне вікно / Parsec")
    parser.add_argument("--text", default=SENTENCE, help="Що друкувати")
    parser.add_argument("--interception", action="store_true",
                        help="Слати через драйвер Interception замість SendInput")
    parser.add_argument("--seed", type=int, default=None,
                        help="Відтворюваний почерк (для порівняння прогонів)")
    args = parser.parse_args()

    if args.seed is not None:
        keyboard.set_profile(keyboard.new_profile(args.seed))
        mouse.set_profile(mouse.new_profile(args.seed))

    if args.interception:
        keyboard.set_transport(keyboard.InterceptionTransport())
        mouse.set_transport(mouse.InterceptionTransport())
        print("Транспорт: Interception (драйвер)")
    else:
        print("Транспорт: SendInput (типовий)")

    print(f"Постав курсор у порожнє текстове поле. Старт через {args.delay:.0f} с...")
    for remaining in range(int(args.delay), 0, -1):
        print(f"  {remaining}", end="\r", flush=True)
        time.sleep(1)

    print("\nПоїхали.")
    manifest = run(args.text)

    print("\nНадіслано:")
    print(f"  символів тексту (без одруків): {manifest['keys']}")
    print(f"  кліків: {manifest['clicks']}")
    print(f"  рухів миші: {manifest['moves']}")
    print(f"  прокруток: {manifest['wheels']}")
    print("\nТепер зупини capture (Esc) і глянь колонку injected. Файл .jsonl — мені на аналіз.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
