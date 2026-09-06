"""
Вимірювання "людяності" клавіатурного вводу.

Скрипт рахує ті самі ознаки, за якими автоматику розпізнають на практиці, і
дозволяє порівняти три речі між собою: наївну емуляцію, цю реалізацію і
**власні руки**. Останнє важливе: еталон тут не літературні числа, а твій
власний запис — його не оскаржиш і його видно на захисті.

Режими:

    python tools/keyboard_stats.py sim
        Порівнює наївну емуляцію (down/up + time.sleep) з реалізацією
        effortless.keyboard. Нічого не набирає — обидва розклади будуються
        офлайн.

    python tools/keyboard_stats.py analyze human.jsonl
        Метрики одного запису з tools/keyboard_capture.py, з висновком по
        кожній ознаці.

    python tools/keyboard_stats.py compare human.jsonl bot.jsonl
        Записи поруч: рука проти бота.

Що означають метрики — у докстрінгах відповідних функцій нижче.
"""
import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from effortless.keyboard.layout import Keystroke, same_finger, same_hand  # noqa: E402
from effortless.keyboard.mistakes import plan_strokes                     # noqa: E402
from effortless.keyboard.profile import new_profile                       # noqa: E402
from effortless.keyboard.rhythm import build_schedule                     # noqa: E402

if hasattr(sys.stdout, "reconfigure"):  # консоль Windows часто не UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Крок системного планувальника Windows без timeBeginPeriod(1).
TIMER_QUANTUM = 0.015625

SC_BACKSPACE = 0x0E
VK_PACKET = 0xE7

# Віртуальні клавіші модифікаторів: Shift, Ctrl, Alt (обидва боки), Win.
_MODIFIER_VKS = frozenset((16, 17, 18, 160, 161, 162, 163, 164, 165, 91, 92))

# Скан-коди QWERTY — лише для режиму `sim`, щоб порівняння не залежало від
# того, яка розкладка активна на машині, де його запускають.
_QWERTY_CHARS = "qwertyuiop[]asdfghjkl;'zxcvbnm,./ "
_QWERTY = dict(zip(
    _QWERTY_CHARS,
    [*range(0x10, 0x1C), *range(0x1E, 0x29), *range(0x2C, 0x36), 0x39],
))

Event = Tuple[float, int, bool]  # (t, scancode, down)


# ----------------------------------------------------------------------
# Метрики
# ----------------------------------------------------------------------

def _downs(events: Sequence[Event]) -> List[Event]:
    return [event for event in events if event[2]]


def quantization_share(events: Sequence[Event], tolerance: float = 0.0012) -> float:
    """Частка інтервалів, що лягають на кратні 15.6 мс.

    Це слід `time.sleep` без `timeBeginPeriod(1)`: скільки б випадковості не
    було в аргументах, реальні паузи округляються вгору до кроку планувальника.
    У живому вводі таких збігів приблизно стільки, скільки дає випадковість.
    """
    downs = _downs(events)
    gaps = [b[0] - a[0] for a, b in zip(downs, downs[1:]) if b[0] > a[0]]
    if not gaps:
        return 0.0

    hits = sum(
        1 for gap in gaps
        if min(gap % TIMER_QUANTUM, TIMER_QUANTUM - gap % TIMER_QUANTUM) < tolerance
    )
    return hits / len(gaps)


def rollover_share(events: Sequence[Event]) -> float:
    """Частка натискань, що сталися до відпускання попередньої клавіші.

    Головна ознака живого набору. У людини від ~40 слів/хв таких переходів
    десятки відсотків; у циклу `down; up; sleep` їх рівно нуль — не «мало»,
    а нуль, бо стан «дві клавіші натиснуті» в такому циклі не існує.
    """
    pressed, overlaps, total = set(), 0, 0
    for _, scancode, down in events:
        if down:
            total += 1
            if pressed:
                overlaps += 1
            pressed.add(scancode)
        else:
            pressed.discard(scancode)
    return overlaps / total if total else 0.0


def dwell_times(events: Sequence[Event]) -> List[float]:
    """Тривалості утримання клавіш (dwell time)."""
    opened: Dict[int, float] = {}
    result = []
    for t, scancode, down in events:
        if down:
            opened[scancode] = t
        elif scancode in opened:
            result.append(t - opened.pop(scancode))
    return result


def digraph_split(events: Sequence[Event]) -> Tuple[List[float], List[float]]:
    """Інтервали down-to-down окремо для «одним пальцем» і «різними руками».

    Головна перевірка на те, що ритм має фізичну природу. У руки перший
    набір помітно повільніший за другий — палець мусить переїхати. У бота,
    який спить випадковий час незалежно від клавіш, обидва розподіли
    однакові, і це видно вже на кількох рядках тексту.
    """
    downs = _downs(events)
    finger, hands = [], []
    for a, b in zip(downs, downs[1:]):
        gap = b[0] - a[0]
        if gap > 0.6:  # пауза в наборі, а не перехід між клавішами
            continue
        if same_finger(a[1], b[1]):
            finger.append(gap)
        elif not same_hand(a[1], b[1]):
            hands.append(gap)
    return finger, hands


def tail_ratio(values: Sequence[float]) -> float:
    """Відношення середнього до медіани — груба міра «хвоста» розподілу.

    У логнормального (людського) розподілу воно помітно більше за одиницю:
    є щільний пік і рідкі довгі затримки. У рівномірного `uniform` — рівно
    одиниця з точністю до шуму, бо плато симетричне.
    """
    if len(values) < 3:
        return 1.0
    median = statistics.median(values)
    return statistics.fmean(values) / median if median else 1.0


def backspace_share(events: Sequence[Event]) -> float:
    """Частка Backspace серед натискань — слід одруків та їх виправлення."""
    downs = _downs(events)
    if not downs:
        return 0.0
    return sum(1 for _, scancode, _ in downs if scancode == SC_BACKSPACE) / len(downs)


def polling_grid_share(events: Sequence[Event], period: float,
                       tolerance: float = 0.0008, phases: int = 32) -> float:
    """Частка подій, що лягають на сітку опитування із заданим періодом.

    Фаза сітки підбирається (перебір `phases` зсувів), а не береться з першої
    події: та версія давала від 5% до 38% на одному й тому самому записі
    залежно від того, який Enter запустив скрипт. Нульовий рівень для
    випадкових міток — 2 * tolerance / period (≈20% на 8 мс), і значущою є
    лише частка, помітно вища за нього.
    """
    if not events:
        return 0.0
    best = 0.0
    for k in range(phases):
        origin = events[0][0] + period * k / phases
        hits = sum(
            1 for t, _, _ in events
            if min((t - origin) % period, period - (t - origin) % period) < tolerance
        )
        best = max(best, hits / len(events))
    return best


def metrics(events: Sequence[Event], period: float = 0.008) -> Dict[str, float]:
    """Усі метрики одного потоку подій."""
    dwells = dwell_times(events)
    finger, hands = digraph_split(events)
    downs = _downs(events)
    gaps = [b[0] - a[0] for a, b in zip(downs, downs[1:]) if 0 < b[0] - a[0] < 0.6]

    return {
        "подій": len(events),
        "натискань": len(downs),
        "квантування 15.6 мс": quantization_share(events),
        "сітка опитування": polling_grid_share(events, period),
        "накладання клавіш": rollover_share(events),
        "утримання, медіана мс": statistics.median(dwells) * 1000 if dwells else 0.0,
        "утримання, CV": (statistics.pstdev(dwells) / statistics.fmean(dwells))
                         if len(dwells) > 2 else 0.0,
        "інтервал, медіана мс": statistics.median(gaps) * 1000 if gaps else 0.0,
        "хвіст (сер/медіана)": tail_ratio(gaps),
        "одним пальцем, мс": statistics.median(finger) * 1000 if finger else 0.0,
        "одним пальцем, n": float(len(finger)),
        "різними руками, мс": statistics.median(hands) * 1000 if hands else 0.0,
        "різними руками, n": float(len(hands)),
        "відношення пал/рук": (statistics.median(finger) / statistics.median(hands))
                              if finger and hands else 0.0,
        "частка Backspace": backspace_share(events),
    }


# ----------------------------------------------------------------------
# Висновки — те, що показують на захисті
# ----------------------------------------------------------------------

def verdict(values: Dict[str, float], injected: Optional[float] = None,
            packets: int = 0, zero_time: Optional[float] = None) -> List[Tuple[bool, str]]:
    """Перелік перевірок з висновком по кожній.

    Пороги — не істина в останній інстанції, а орієнтири: остаточна відповідь
    завжди з порівняння з власним записом (`compare`).
    """
    checks: List[Tuple[bool, str]] = []

    if packets:
        checks.append((False, f"VK_PACKET: {packets} подій — ввід через KEYEVENTF_UNICODE, "
                              "фізична клавіатура такого не породжує"))
    else:
        checks.append((True, "VK_PACKET не зустрічається — події йдуть скан-кодами"))

    # Мітка часу події. `time == 0` у всіх подіях — це SendInput з незаданим
    # полем часу; справжня подія (і ретрансльована через Parsec) несе реальний
    # GetTickCount. Через Parsec це важливіше за injected: там injected стоїть
    # і в живого користувача, а от нульовий час лишається тільки в бота.
    if zero_time is not None:
        ok = zero_time < 0.5
        checks.append((ok, f"нульова мітка часу (time=0): {zero_time:.0%} подій"
                           + ("" if ok else " — SendInput з time=0; постав GetTickCount у win_api")))

    if injected is not None:
        # Через Parsec injected стоїть у всіх, тому сам по собі він нічого не
        # каже — інформативний лише в парі з часом і локально.
        ok = injected < 0.5
        checks.append((ok, f"прапорець injected: {injected:.0%} подій"
                           + ("" if ok else " — джерелом SendInput; через Parsec стоїть і в руки, тож не вирішальний")))

    # Нульовий рівень: для довільних інтервалів у вікно ±1.2 мс на кроці
    # 15.625 мс потрапляє ~15%. Значення в межах 10-22% — це НЕ доказ
    # людяності, а відсутність сигналу; доказ автоматики — значення
    # близькі до 100%, як у наївного time.sleep.
    quant = values["квантування 15.6 мс"]
    null = 2 * 0.0012 / TIMER_QUANTUM
    ok = quant < 0.35
    checks.append((ok, f"квантування 15.6 мс: {quant:.0%} (шум ≈ {null:.0%}, у time.sleep 100%)"
                       + ("" if ok else " — час іде через time.sleep без timeBeginPeriod(1)")))

    # Частка накладань сильно залежить від темпу: у повільної руки їх одиниці
    # відсотків, у швидкої десятки. Тому поріг рухомий, а значущий випадок —
    # рівно нуль при швидкому наборі: він означає, що стану «дві клавіші
    # натиснуті» не існує в принципі, а так буває лише в циклі down/up/sleep.
    share = values["накладання клавіш"]
    slow = values["інтервал, медіана мс"] > 210
    ok = share > 0.03 or slow
    checks.append((ok, f"накладання клавіш: {share:.0%}" + (
        "" if share > 0.03 else
        " — темп низький, у руки їх тут теж майже не буває" if slow else
        " — клавіші ніколи не перетинаються, для такого темпу це неможливо")))

    # Груба міра: залежить від відсічки пауз (0.6 с) і на 150 натисканнях
    # має довірчий інтервал ~±0.15. Розрізняє лише uniform від усього іншого.
    ok = values["хвіст (сер/медіана)"] > 1.03
    checks.append((ok, f"хвіст розподілу: {values['хвіст (сер/медіана)']:.2f} (orієнтовно)"
                       + ("" if ok else " — рівне плато, схоже на random.uniform")))

    ratio = values["відношення пал/рук"]
    ok = ratio > 1.25
    checks.append((ok, f"одним пальцем / різними руками: {ratio:.2f}"
                       + ("" if ok else " — ритм не залежить від того, чим б'ють клавіші")))

    ok = values["утримання, CV"] > 0.15
    checks.append((ok, f"розкид утримання (CV): {values['утримання, CV']:.2f}"
                       + ("" if ok else " — надто однакове утримання")))

    return checks


# ----------------------------------------------------------------------
# Джерела подій
# ----------------------------------------------------------------------

def _quantize(seconds: float) -> float:
    """Що насправді робить `time.sleep` без timeBeginPeriod(1) — округляє вгору."""
    return (int(seconds / TIMER_QUANTUM) + 1) * TIMER_QUANTUM


def naive_schedule(text: str, seed: int = 0) -> List[Event]:
    """Наївна емуляція: down -> up -> сон, як у більшості прикладів у мережі.

    Відтворено і квантування `time.sleep`: без `timeBeginPeriod(1)` кожна
    пауза округляється вгору до кроку планувальника.
    """
    rng = random.Random(seed)
    events: List[Event] = []
    t = 0.0

    for ch in text.lower():
        scancode = _QWERTY.get(ch)
        if scancode is None:
            continue
        # Обидві паузи йдуть через time.sleep, тому обидві округляються вгору
        # до кроку планувальника — і в сумі інтервал між натисканнями завжди
        # кратний 15.6 мс, хоч би скільки випадковості було в аргументах.
        dwell = _quantize(rng.uniform(0.04, 0.09))
        events.append((t, scancode, True))
        events.append((t + dwell, scancode, False))
        t += dwell + _quantize(rng.uniform(0.05, 0.15))

    return events


def effortless_schedule(text: str, seed: int = 0) -> List[Event]:
    """Розклад effortless.keyboard, побудований офлайн — без реальних подій."""
    random.seed(seed)
    profile = new_profile(seed=seed)
    keystrokes = [Keystroke(_QWERTY[ch], char=ch) for ch in text.lower() if ch in _QWERTY]
    schedule = build_schedule(plan_strokes(keystrokes, profile), profile)
    return [(event.t, event.scancode, event.down) for event in schedule]


def load(path: str) -> Tuple[List[Event], float, int, float]:
    """Читає запис `keyboard_capture.py`.

    Повертає (події, частка injected, VK_PACKET, частка подій з time=0).

    Гігієна, без якої метрики брехали на живому записі:
      * модифікатори (Shift/Ctrl/Alt/Win) не входять у події: їхні утримання
        (218, 352, 1103 мс) підняли CV утримання з 0.24 до 1.03, а натискання
        літери під затиснутим Shift лічилося як «накладання»;
      * автоповтор системи (повторний `down` без `up`) — не натискання руки:
        один затиснутий Ctrl дав 19 «накладань» і 16 інтервалів по 30 мс;
      * розширені клавіші збираються як 0xE0XX, як у `layout`;
      * рядок без потрібних полів (запис іншого пристрою, бите JSON) —
        пропускається, а не валить аналіз.
    """
    events: List[Event] = []
    injected = packets = zero_time = total = 0
    held = set()

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "t" not in record or "scan" not in record or "down" not in record:
                continue

            total += 1
            injected += record.get("injected", 0)
            vk = record.get("vk")
            packets += vk == VK_PACKET
            zero_time += record.get("sys_time", 0) == 0

            if vk in _MODIFIER_VKS:
                continue
            scan = int(record["scan"]) | (0xE000 if record.get("extended") else 0)
            down = bool(record["down"])
            if down:
                if scan in held:
                    continue  # автоповтор
                held.add(scan)
            else:
                held.discard(scan)
            events.append((record["t"], scan, down))

    n = total
    return events, (injected / n if n else 0.0), packets, (zero_time / n if n else 0.0)


# ----------------------------------------------------------------------
# Виведення
# ----------------------------------------------------------------------

_PERCENT_KEYS = {"квантування 15.6 мс", "сітка опитування", "накладання клавіш",
                 "частка Backspace"}


def _format(key: str, value: float) -> str:
    if key in _PERCENT_KEYS:
        return f"{value:>12.1%}"
    if key in ("подій", "натискань", "одним пальцем, n", "різними руками, n"):
        return f"{value:>12.0f}"
    return f"{value:>12.2f}"


def print_table(columns: Sequence[Tuple[str, Dict[str, float]]]) -> None:
    width = max(len(key) for _, values in columns for key in values) + 2
    header = " " * width + "".join(f"{name:>12}" for name, _ in columns)
    print(header)
    print("-" * len(header))
    for key in columns[0][1]:
        print(key.ljust(width) + "".join(_format(key, values[key]) for _, values in columns))


def print_verdict(title: str, checks: Sequence[Tuple[bool, str]]) -> None:
    print(f"\n{title}")
    for ok, text in checks:
        print(f"  [{'+' if ok else '!'}] {text}")


SAMPLE = (
    "the quick brown fox jumps over the lazy dog while the sleepy cat "
    "watches from the warm windowsill and waits for dinner to appear "
    "somewhere near the kitchen door before the evening finally arrives "
) * 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Метрики людяності клавіатурного вводу")
    sub = parser.add_subparsers(dest="mode", required=True)

    sim = sub.add_parser("sim", help="Наївна емуляція проти effortless.keyboard")
    sim.add_argument("--seed", type=int, default=1)

    analyze = sub.add_parser("analyze", help="Метрики одного запису")
    analyze.add_argument("path")
    analyze.add_argument("--period", type=float, default=0.008,
                         help="Період опитування клавіатури, с (125 Гц = 0.008)")

    compare = sub.add_parser("compare", help="Два записи поруч")
    compare.add_argument("human")
    compare.add_argument("bot")
    compare.add_argument("--period", type=float, default=0.008)

    args = parser.parse_args()

    if args.mode == "sim":
        naive = metrics(naive_schedule(SAMPLE, args.seed))
        ours = metrics(effortless_schedule(SAMPLE, args.seed))
        print_table([("наївна", naive), ("effortless", ours)])
        print_verdict("Наївна емуляція:", verdict(naive))
        print_verdict("effortless.keyboard:", verdict(ours))
        print(
            "\nДва рядки в цьому режимі неінформативні. «Сітка опитування» низька\n"
            "в обох колонках, бо укладання на сітку робить emitter під час\n"
            "відтворення, а не побудова розкладу; прапорця injected немає з тієї\n"
            "ж причини — події в систему не йдуть. Щоб побачити обидва, запусти\n"
            "tools/keyboard_capture.py і набери текст через модуль."
        )
        return 0

    if args.mode == "analyze":
        events, injected, packets, zero_time = load(args.path)
        if not events:
            print("Порожній запис.", file=sys.stderr)
            return 1
        values = metrics(events, args.period)
        print_table([(Path(args.path).stem, values)])
        print_verdict("Висновок:", verdict(values, injected, packets, zero_time))
        return 0

    human_events, human_injected, human_packets, human_zero = load(args.human)
    bot_events, bot_injected, bot_packets, bot_zero = load(args.bot)
    human_values = metrics(human_events, args.period)
    bot_values = metrics(bot_events, args.period)

    print_table([("рука", human_values), ("бот", bot_values)])
    print_verdict("Рука:", verdict(human_values, human_injected, human_packets, human_zero))
    print_verdict("Бот:", verdict(bot_values, bot_injected, bot_packets, bot_zero))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
