"""
Вимірювання "людяності" руху миші.

Скрипт рахує ті самі ознаки, за якими автоматику розпізнають на практиці, і
дозволяє порівняти три речі між собою: стару реалізацію, нову і **власну
руку**. Останнє важливе: еталон тут не літературні числа, а твій власний
запис — його не оскаржиш і його видно на захисті.

Режими:

    python tools/mouse_stats.py sim
        Порівнює стару реалізацію (WindMouse + time.sleep) з новою.
        Курсор не рухає — обидві траєкторії будуються офлайн.

    python tools/mouse_stats.py analyze human.jsonl
        Метрики одного запису з tools/mouse_capture.py.

    python tools/mouse_stats.py compare human.jsonl bot.jsonl
        Записи поруч: рука проти бота.

Що означають метрики — у докстрінгах відповідних функцій нижче.
"""
import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from effortless.mouse.profile import new_profile          # noqa: E402
from effortless.mouse.trajectory import generate_path     # noqa: E402
from effortless.mouse.wind_mouse import wind_mouse        # noqa: E402

if hasattr(sys.stdout, "reconfigure"):  # консоль Windows часто не UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

Track = List[Tuple[float, float, float]]  # (t, x, y)

# Крок системного планувальника Windows без timeBeginPeriod(1).
TIMER_QUANTUM = 0.015625

# Розрив у подіях, більший за це, вважаємо межею між окремими рухами.
MOVEMENT_GAP = 0.20

# Курсор, що протримався в межах IDLE_RADIUS пікселів довше за IDLE_TIME,
# вважається таким, що стоїть. Різати потік лише по паузах у подіях не можна:
# джерело з мікротремором у простої взагалі не має пауз, і вся сесія
# злипається в один нескінченний "рух" з безглуздими метриками.
# Пороги підібрані так, щоб кількість треків перестала від них залежати:
# на 3 px / 0.15 с сегментатор ріже одне наведення навпіл (повільна кінцева
# фаза виглядає як зупинка), і всі метрики рахуються по обрізках.
IDLE_RADIUS = 2.0
IDLE_TIME = 0.50

# Стрибок, більший за це, фізично неможливий і означає телепорт
# (абсолютне позиціонування, перемикання екрана). Такі місця теж ріжемо.
TELEPORT_STEP = 300.0


# ----------------------------------------------------------------------
# Метрики
# ----------------------------------------------------------------------

def _dt_series(track: Track) -> List[float]:
    return [b[0] - a[0] for a, b in zip(track, track[1:]) if b[0] > a[0]]


def quantization_share(dts: Sequence[float], tolerance: float = 0.0012) -> float:
    """Частка інтервалів, що лягають на кратні 15.6 мс.

    Головна ознака `time.sleep()` без підвищеної роздільності таймера: скільки
    б випадковості не було в аргументі, реальні паузи злипаються в кратні
    кроку планувальника. У живої миші такої структури немає — вона шле події
    за власним кварцом.
    """
    if not dts:
        return 0.0
    hits = 0
    for dt in dts:
        offset = dt % TIMER_QUANTUM
        if min(offset, TIMER_QUANTUM - offset) <= tolerance:
            hits += 1
    return hits / len(dts)


def dt_uniformity(dts: Sequence[float]) -> float:
    """Коефіцієнт варіації інтервалів між подіями (std / mean).

    Рахується лише по "робочих" інтервалах: паузи, довші за чотири медіани,
    відкидаються. Вони є і в людини (рука зупинилась між субрухами — миша
    не шле подій руху), тому не характеризують стабільність опитування, а
    лише розганяли б метрику.

    У фізичної миші темп подій задає кварц, тому значення мале. Великий
    розкид означає, що ритм задає код програми, а не залізо.
    """
    if len(dts) < 2:
        return 0.0
    median = statistics.median(dts)
    inliers = [dt for dt in dts if dt <= median * 4.0] or list(dts)
    mean = statistics.fmean(inliers)
    return statistics.pstdev(inliers) / mean if mean else 0.0


def straightness(track: Track) -> float:
    """Довжина шляху, поділена на пряму відстань.

    1.0 — ідеальна пряма (так рухається `pyautogui.moveTo`). У людини
    трохи більше за одиницю за рахунок дуги і довідних рухів.
    """
    if len(track) < 2:
        return 1.0
    path = sum(
        math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(track, track[1:])
    )
    chord = math.hypot(track[-1][1] - track[0][1], track[-1][2] - track[0][2])
    return path / chord if chord > 1 else 1.0


def _speeds(track: Track) -> List[Tuple[float, float]]:
    out = []
    for a, b in zip(track, track[1:]):
        dt = b[0] - a[0]
        if dt > 0:
            out.append((b[0], math.hypot(b[1] - a[1], b[2] - a[2]) / dt))
    return out


def peak_speed_position(track: Track) -> float:
    """Момент піку швидкості у частках тривалості руху.

    Рівномірний рух дає плато (значення "пливе"), симетричний профіль —
    близько 0.5. У людини пік зсунутий на початок: різкий кидок і довше
    гальмування.
    """
    speeds = _speeds(track)
    if len(speeds) < 3:
        return 0.0
    peak_t = max(speeds, key=lambda s: s[1])[0]
    duration = track[-1][0] - track[0][0]
    return (peak_t - track[0][0]) / duration if duration > 0 else 0.0


def submovement_count(track: Track) -> float:
    """Кількість субрухів — піків швидкості з помітною виразністю (prominence).

    Наївний підрахунок локальних максимумів тут не працює: тремор і
    округлення до пікселя дають десятки дрібних піків у будь-якій
    траєкторії. Тому крива спершу згладжується вікном, пропорційним її
    довжині, а потім кожен максимум оцінюється за глибиною провалу навколо
    нього — зараховуються лише ті, де швидкість перед піком і після нього
    падала щонайменше на чверть від його висоти.

    Один субрух означає, що курсор ведуть до цілі одним неперервним рухом.
    У людини їх зазвичай більше: балістичний кидок плюс довідні рухи.
    """
    speeds = [s for _, s in _speeds(track)]
    if len(speeds) < 8:
        return 0.0

    peak = max(speeds)
    if peak <= 0:
        return 0.0

    window = max(2, len(speeds) // 12)
    smooth = [
        statistics.fmean(speeds[max(0, i - window): i + window + 1])
        for i in range(len(speeds))
    ]

    min_prominence = peak * 0.12
    count = 0

    for i in range(1, len(smooth) - 1):
        if not (smooth[i] > smooth[i - 1] and smooth[i] >= smooth[i + 1]):
            continue

        # Спускаємось в обидва боки до першої точки, вищої за пік: найглибший
        # провал на цьому шляху і є виразністю піку.
        left = smooth[i]
        for j in range(i - 1, -1, -1):
            if smooth[j] > smooth[i]:
                break
            left = min(left, smooth[j])

        right = smooth[i]
        for j in range(i + 1, len(smooth)):
            if smooth[j] > smooth[i]:
                break
            right = min(right, smooth[j])

        if smooth[i] - max(left, right) >= min_prominence:
            count += 1

    return float(count)


def step_stats(track: Track) -> Tuple[float, float]:
    """Середній і максимальний зсув між сусідніми подіями, пікселів.

    За сталої частоти опитування саме зсув кодує швидкість. Якщо середній
    крок майже дорівнює максимальному — швидкість стала, чого при
    прицілюванні не буває.
    """
    steps = [math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(track, track[1:])]
    if not steps:
        return 0.0, 0.0
    return statistics.fmean(steps), max(steps)


def measure(track: Track) -> Dict[str, float]:
    """Рахує повний набір метрик для однієї траєкторії."""
    dts = _dt_series(track)
    mean_step, max_step = step_stats(track)
    return {
        "events": float(len(track)),
        "duration": track[-1][0] - track[0][0] if len(track) > 1 else 0.0,
        "dt_mean_ms": statistics.fmean(dts) * 1000 if dts else 0.0,
        "dt_cv": dt_uniformity(dts),
        "quantized": quantization_share(dts),
        "step_mean_px": mean_step,
        "step_max_px": max_step,
        "straightness": straightness(track),
        "peak_at": peak_speed_position(track),
        "submovements": submovement_count(track),
    }


def average(rows: Sequence[Dict[str, float]]) -> Dict[str, float]:
    """Зведення по треках — МЕДІАНА, не середнє.

    Один злиплий трек зі звивистістю 49 (сегментатор не розрізав п'ять
    наведень) давав середню «звивистість руки» 4.86 при медіані 1.25 — і
    цей викид став головною «різницею між рукою і ботом».
    """
    if not rows:
        return {}
    return {key: statistics.median(row[key] for row in rows) for key in rows[0]}


# ----------------------------------------------------------------------
# Побудова траєкторій для симуляції
# ----------------------------------------------------------------------

def old_track(start: Tuple[int, int], target: Tuple[int, int]) -> Track:
    """Відтворює стару реалізацію офлайн: WindMouse + `time.sleep(6..14 мс)`.

    Ключова деталь — затримка округлюється вгору до кроку планувальника,
    бо саме це відбувалося насправді: `time.sleep(0.008)` без
    `timeBeginPeriod(1)` спить ~15.6 мс.
    """
    points: Track = []
    clock = 0.0

    # Стара реалізація зсувала точку кліку на ±3 px — враховуємо це, інакше
    # порівняння було б нечесним на її користь у метриці влучання.
    target = (target[0] + random.randint(-3, 3), target[1] + random.randint(-3, 3))

    def callback(x, y):
        nonlocal clock
        points.append((clock, float(x), float(y)))
        requested = random.uniform(0.006, 0.014)
        clock += math.ceil(requested / TIMER_QUANTUM) * TIMER_QUANTUM

    wind_mouse(start[0], start[1], target[0], target[1], move_mouse=callback)
    return points


def new_track(start: Tuple[int, int], target: Tuple[int, int], profile) -> Track:
    return [(s.t, s.x, s.y) for s in generate_path(start, target, target_radius=10.0, profile=profile)]


def run_simulation(count: int, seed: Optional[int]) -> None:
    rng = random.Random(seed)
    profile = new_profile(seed)

    old_rows, new_rows = [], []
    old_hits, new_hits = [], []

    for _ in range(count):
        start = (rng.randint(100, 1800), rng.randint(100, 900))
        target = (rng.randint(100, 1800), rng.randint(100, 900))

        old = old_track(start, target)
        new = new_track(start, target, profile)
        if len(old) < 5 or len(new) < 5:
            continue

        old_rows.append(measure(old))
        new_rows.append(measure(new))
        old_hits.append((old[-1][1] - target[0], old[-1][2] - target[1]))
        new_hits.append((new[-1][1] - target[0], new[-1][2] - target[1]))

    print(f"\nПрофіль оператора: {profile.polling_hz} Гц опитування, "
          f"speed_scale={profile.speed_scale:.2f}, curvature={profile.curvature_bias:+.3f}\n")
    _print_table(("Метрика", "Стара", "Нова"), average(old_rows), average(new_rows))

    print("\nРозкид точки влучання відносно центру цілі (px):")
    for name, hits in (("стара", old_hits), ("нова", new_hits)):
        xs = [h[0] for h in hits]
        ys = [h[1] for h in hits]
        spread = statistics.pstdev(xs) if len(xs) > 1 else 0.0
        mean_err = statistics.fmean(math.hypot(*h) for h in hits)
        print(f"  {name:<6} середня похибка {mean_err:5.2f}, sigma_x {spread:5.2f}, "
              f"sigma_y {statistics.pstdev(ys) if len(ys) > 1 else 0.0:5.2f}")

    print(_verdict(average(old_rows), average(new_rows)))


_LABELS = {
    "events": "Подій на рух",
    "duration": "Тривалість, с",
    "dt_mean_ms": "Інтервал між подіями, мс",
    "dt_cv": "Розкид інтервалів (CV)",
    "quantized": "На сітці 15.6 мс (шум ≈15%)",
    "step_mean_px": "Середній крок, px",
    "step_max_px": "Максимальний крок, px",
    "straightness": "Звивистість шляху",
    "peak_at": "Пік швидкості (частка часу)",
    "submovements": "Субрухів на наведення",
}


def _print_table(headers: Tuple[str, ...], *columns: Dict[str, float]) -> None:
    print(f"{headers[0]:<30}" + "".join(f"{h:>12}" for h in headers[1:]))
    print("-" * (30 + 12 * len(columns)))
    for key, label in _LABELS.items():
        if not columns or key not in columns[0]:
            continue
        print(f"{label:<30}" + "".join(f"{col.get(key, 0.0):>12.3f}" for col in columns))


def _verdict(old: Dict[str, float], new: Dict[str, float]) -> str:
    lines = ["\nЩо саме змінилось:"]
    lines.append(
        "  * події більше не лягають на сітку планувальника: "
        f"{old.get('quantized', 0) * 100:.0f}% -> {new.get('quantized', 0) * 100:.0f}%"
    )
    lines.append(
        f"  * інтервали більше не ідеально однакові: CV {old.get('dt_cv', 0):.3f}"
        f" -> {new.get('dt_cv', 0):.3f}\n"
        "    (нуль у старій — це не стабільність заліза, а те, що всі паузи"
        " дорівнювали рівно одному кроку планувальника)"
    )
    lines.append(
        "  * швидкість кодується кроком, а не паузою: середній/максимальний крок "
        f"{old.get('step_mean_px', 0):.1f}/{old.get('step_max_px', 0):.1f} -> "
        f"{new.get('step_mean_px', 0):.1f}/{new.get('step_max_px', 0):.1f}"
    )
    lines.append(
        "  * з'явилися довідні субрухи (піки швидкості з виразністю понад 12%): "
        f"{old.get('submovements', 0):.1f} -> {new.get('submovements', 0):.1f}"
    )
    lines.append(
        "  * пік швидкості зсунувся на початок руху: "
        f"{old.get('peak_at', 0):.2f} -> {new.get('peak_at', 0):.2f} тривалості"
    )
    lines.append(
        "\nЗауваження: усе це — статистика траєкторії. Прапорець injected у\n"
        "низькорівневому хуці вона не прибирає; перевір його через\n"
        "tools/mouse_capture.py."
    )
    return chr(10).join(lines)


# ----------------------------------------------------------------------
# Аналіз записів
# ----------------------------------------------------------------------

def split_movements(moves: Track) -> List[Track]:
    """Ріже суцільний потік координат на окремі наведення.

    Межею вважається будь-що з трьох: пауза в подіях, ділянка, де курсор
    фактично стоїть (тремтить у межах кількох пікселів), або фізично
    неможливий стрибок.

    Друга умова — головна. Різати лише по паузах не можна: джерело, яке
    тремтить у простої, не має пауз узагалі, і весь запис злипається в один
    "рух" довжиною в сесію. Метрики з такого злипання не помилкові — вони
    просто ні про що.
    """
    tracks: List[Track] = []
    current: Track = []
    anchor: Optional[Tuple[float, float, float]] = None

    def flush() -> None:
        if len(current) >= 6:
            tracks.append(list(current))
        current.clear()

    for point in moves:
        if current:
            gap = point[0] - current[-1][0]
            step = math.hypot(point[1] - current[-1][1], point[2] - current[-1][2])
            if gap > MOVEMENT_GAP or step > TELEPORT_STEP:
                flush()
                anchor = None

        if anchor is None or math.hypot(point[1] - anchor[1], point[2] - anchor[2]) > IDLE_RADIUS:
            anchor = point
        elif point[0] - anchor[0] > IDLE_TIME:
            # Курсор стоїть уже довше за поріг — рух скінчився. Самі "стоячі"
            # семпли у трек не йдуть, інакше вони б занижували швидкість.
            flush()
            anchor = point
            continue

        current.append(point)

    flush()
    return tracks


def load_capture(path: str, injected: Optional[bool] = None) -> Tuple[List[Track], Dict[str, float]]:
    """Читає JSONL із `mouse_capture.py` і ріже потік на окремі рухи.

    Args:
        path: Файл запису.
        injected: `True` — лишити тільки події з прапорцем injected, `False` —
            тільки без нього, `None` — усі. Потрібно, коли в одному записі
            перемішані джерела: наприклад, рух рукою через віддалений доступ
            і рух бота. Без розділення метрики рахувались би по суміші й не
            означали б нічого.
    """
    moves: Track = []
    injected_count = total = zero_time = 0
    holds: List[float] = []
    pressed_at: Optional[float] = None

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            flag = bool(event.get("injected", 0))
            if injected is not None and flag != injected:
                continue
            total += 1
            injected_count += flag
            zero_time += event.get("sys_time", 0) == 0

            kind = event.get("type")
            if kind == "move":
                moves.append((event["t"], float(event["x"]), float(event["y"])))
            elif kind == "left_down":
                pressed_at = event["t"]
            elif kind == "left_up" and pressed_at is not None:
                holds.append(event["t"] - pressed_at)
                pressed_at = None

    tracks = split_movements(moves)

    extra = {
        "injected_share": injected_count / total if total else 0.0,
        "zero_time_share": zero_time / total if total else 0.0,
        "events_total": float(total),
        "clicks": float(len(holds)),
        "hold_median_ms": statistics.median(holds) * 1000 if holds else 0.0,
        "hold_cv": (statistics.pstdev(holds) / statistics.fmean(holds)) if len(holds) > 1 else 0.0,
    }
    return tracks, extra


def _summarize(path: str, injected: Optional[bool] = None) -> Tuple[Dict[str, float], Dict[str, float]]:
    tracks, extra = load_capture(path, injected)
    if not tracks:
        raise SystemExit(f"У {path} не знайшлось жодного руху (потрібно щонайменше 6 подій поспіль).")
    return average([measure(t) for t in tracks]), extra


def _print_extra(name: str, extra: Dict[str, float]) -> None:
    print(f"\n{name}:")
    print(f"  подій усього: {extra['events_total']:.0f}, з них injected: {extra['injected_share'] * 100:.1f}%")
    # Нульова мітка часу — головна ознака через Parsec: injected там стоїть і в
    # руки, а от time=0 буває тільки в SendInput з незаданим полем часу.
    zero = extra.get("zero_time_share", 0.0)
    mark = "" if zero < 0.5 else "  <-- SendInput з time=0, постав GetTickCount у win_api"
    print(f"  нульова мітка часу (time=0): {zero * 100:.1f}%{mark}")
    if extra["clicks"]:
        print(f"  кліків: {extra['clicks']:.0f}, медіана утримання {extra['hold_median_ms']:.0f} мс, "
              f"CV {extra['hold_cv']:.2f}")


def run_analyze(path: str) -> None:
    _, extra = _summarize(path)
    _print_extra(Path(path).name, extra)

    # Якщо в записі є події обох видів, рахуємо їх окремо: змішувати
    # ін'єктовані з апаратними — те саме, що усереднювати два різні
    # пристрої і робити висновок про неіснуючий третій.
    share = extra["injected_share"]
    if 0.02 < share < 0.98:
        print(chr(10) + "  У записі перемішані джерела — розділяю за прапорцем injected.")
        columns, headers = [], ["Метрика"]
        for flag, label in ((False, "без флага"), (True, "injected")):
            try:
                metrics, _ = _summarize(path, injected=flag)
            except SystemExit:
                continue
            columns.append(metrics)
            headers.append(label)
        if len(columns) > 1:
            print()
            _print_table(tuple(headers), *columns)
            return

    metrics, _ = _summarize(path)
    print()
    _print_table(("Метрика", "Значення"), metrics)


def run_compare(human_path: str, bot_path: str) -> None:
    human, human_extra = _summarize(human_path)
    bot, bot_extra = _summarize(bot_path)

    _print_extra(f"Рука ({Path(human_path).name})", human_extra)
    _print_extra(f"Бот ({Path(bot_path).name})", bot_extra)
    print()
    _print_table(("Метрика", "Рука", "Бот"), human, bot)

    print("\nНайбільші розбіжності (у разах):")
    gaps = []
    for key in human:
        h, b = human[key], bot[key]
        if abs(h) < 1e-6:
            continue
        gaps.append((abs(b - h) / abs(h), _LABELS.get(key, key), h, b))
    for ratio, label, h, b in sorted(gaps, reverse=True)[:4]:
        print(f"  {label:<30} рука {h:8.3f} | бот {b:8.3f}  ({ratio * 100:.0f}% різниці)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Метрики людяності руху миші")
    sub = parser.add_subparsers(dest="mode", required=True)

    sim = sub.add_parser("sim", help="Порівняти стару і нову реалізацію офлайн")
    sim.add_argument("--count", type=int, default=200, help="Скільки рухів згенерувати")
    sim.add_argument("--seed", type=int, default=None, help="Зерно для відтворюваності")

    analyze = sub.add_parser("analyze", help="Метрики одного запису")
    analyze.add_argument("capture")

    compare = sub.add_parser("compare", help="Порівняти два записи")
    compare.add_argument("human")
    compare.add_argument("bot")

    args = parser.parse_args()

    if args.mode == "sim":
        run_simulation(args.count, args.seed)
    elif args.mode == "analyze":
        run_analyze(args.capture)
    else:
        run_compare(args.human, args.bot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
