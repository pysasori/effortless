"""
Калібрування профілю оператора за реальним записом подій.

Модель у `profile.py` розігрує параметри з правдоподібних діапазонів — але
правдоподібних «взагалі», а не для конкретної машини. На практиці це б'є в
двох місцях:

  * **частота подій.** Профіль може випасти на 1000 Гц, тоді як у цьому
    середовищі ввід фізично приходить раз на ~12 мс. Через віддалений
    доступ (Parsec, RDP) події взагалі не мають апаратної частоти: їх темп
    задає мережа. Профіль «ігрової миші» там виглядає чужорідно;

  * **розкид темпу.** Фізична миша шле події рівним потоком: інтервали
    утиснуті у вузький пік навколо періоду опитування. Мережевий шлях
    цей пік розмазує. Модель із власного розкиду в 1-4% дає потік
    рівніший, ніж будь-що реальне в такому середовищі, — тобто бот
    виглядає як краща миша, ніж на машині взагалі буває;

  * **тривалість утримання кліку.** Медіана й розкид у різних людей
    відрізняються в рази, і вгадати їх неможливо.

Тому обидва беруться з запису, зробленого `tools/mouse_capture.py`. Решта
параметрів лишається випадковою: їх із потоку подій не відновити.

    import effortless.mouse as mouse
    mouse.set_profile(mouse.profile_from_capture("human.jsonl"))
"""
import json
import math
import statistics
from pathlib import Path
from typing import List, Optional

from .profile import OperatorProfile, new_profile

__all__ = ["profile_from_capture", "capture_summary"]

# Інтервали, довші за це, — паузи між діями, а не темп подій.
_MAX_INTERVAL = 0.20

# Скільки кліків потрібно, щоб статистика утримання щось означала.
_MIN_CLICKS = 8

# Зсув, більший за це, — не рух руки, а телепорт курсора (перемикання вікна,
# абсолютне позиціонування). У статистику кроку такі події не йдуть.
_TELEPORT_STEP = 300.0

# Скільки зсувів потрібно, щоб оцінка стелі щось означала.
_MIN_STEPS = 50

# Утримання довше за це — не клік, а перетягування чи довге натискання.
_MAX_CLICK_HOLD = 0.33

# Реальні частоти опитування мишей. Оцінка з запису підтягується до
# найближчої: миші, що опитується на 118 Гц, не існує, і таке значення
# саме по собі було б ознакою.
_STANDARD_RATES = (60, 125, 250, 500, 1000)
_RATE_SNAP_TOLERANCE = 0.18


def capture_summary(path: str) -> dict:
    """Витягає з запису те, що можна перенести в профіль.

    Returns:
        dict з ключами `polling_hz`, `hold_median`, `hold_sigma`, `clicks`,
        `injected_share`. Відсутні дані повертаються як None.
    """
    intervals: List[float] = []
    steps: List[float] = []
    holds: List[float] = []
    injected = total = 0
    last_move: Optional[float] = None
    pressed_at: Optional[float] = None

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            total += 1
            injected += bool(event.get("injected", 0))

            kind = event.get("type")
            if kind == "move":
                if last_move is not None:
                    gap = event["t"] - last_move[0]
                    if 0 < gap <= _MAX_INTERVAL:
                        intervals.append(gap)
                        step = math.hypot(event["x"] - last_move[1], event["y"] - last_move[2])
                        if step <= _TELEPORT_STEP:
                            steps.append(step)
                last_move = (event["t"], event["x"], event["y"])
            elif kind == "left_down":
                pressed_at = event["t"]
            elif kind == "left_up" and pressed_at is not None:
                hold = event["t"] - pressed_at
                # Верхня межа _MAX_CLICK_HOLD відсікає не викиди, а ІНШУ ДІЮ:
                # утримання довше третини секунди — це перетягування або
                # довге натискання, і в статистиці кліку йому не місце. У
                # записі, на якому це калібрування писалося, таких було
                # чотири з 76, і саме вони втричі роздували оцінку розкиду.
                if 0 < hold <= _MAX_CLICK_HOLD:
                    holds.append(hold)
                pressed_at = None

    summary = {
        "clicks": len(holds),
        "injected_share": injected / total if total else 0.0,
        "polling_hz": None,
        "polling_jitter": None,
        "max_velocity": None,
        "hold_median": None,
        "hold_sigma": None,
    }

    if intervals:
        # Медіана, а не середнє: поодинокі довгі інтервали (пропущений кадр,
        # затримка мережі) не мають зсувати оцінку темпу.
        period = statistics.median(intervals)
        summary["polling_hz"] = 1.0 / period

        # Розкид рахуємо лише по інтервалах в один такт: довші — це пропущені
        # події (курсор не зрушив на цілий піксель), і вони описують не
        # тремтіння такту, а щільність руху.
        single = [i for i in intervals if 0.5 * period < i < 1.8 * period]
        if len(single) >= 20:
            spread = statistics.pstdev(single)
            # Інтервал — різниця двох сусідніх тактів, тож його дисперсія
            # удвічі більша за дисперсію одного такту.
            summary["polling_jitter"] = spread / (math.sqrt(2.0) * period)

    if len(steps) >= _MIN_STEPS and intervals:
        # Стелю виводимо з розподілу ЗСУВІВ, а не з миттєвої швидкості.
        # Швидкість — це крок, поділений на інтервал, а інтервал у записі
        # гуляє (мережа, буферизація, згладжування хука), і ділення на
        # поодинокий мікросекундний інтервал роздуває значення в мільйони
        # пікселів за секунду. Розмір кроку таких артефактів не має.
        ordered = sorted(steps)
        # 99-й перцентиль, а не максимум: один викид не має ставати межею.
        step_limit = ordered[int(len(ordered) * 0.99)]
        summary["max_velocity"] = step_limit / statistics.median(intervals)

    if len(holds) >= _MIN_CLICKS:
        # Параметри логнормального розподілу оцінюються ПО ЛОГАРИФМАХ, а не
        # через коефіцієнт варіації. Формула sigma = sqrt(log(1 + CV^2))
        # алгебраїчно правильна, але це метод моментів: він тримається на
        # правому хвості, тож кілька довгих утримань зсувають оцінку в рази.
        # Оцінка по логарифмах стійка до цього.
        logs = [math.log(h) for h in holds]
        summary["hold_median"] = math.exp(statistics.median(logs))
        spread = statistics.pstdev(logs)
        summary["hold_sigma"] = spread if spread > 0 else None

    return summary


def _snap_rate(measured: float) -> int:
    """Підтягує виміряну частоту до найближчої реальної, якщо вона поруч."""
    best = min(_STANDARD_RATES, key=lambda rate: abs(rate - measured))
    if abs(best - measured) / measured <= _RATE_SNAP_TOLERANCE:
        return best
    return int(round(measured))


def profile_from_capture(path: str, seed: Optional[int] = None) -> OperatorProfile:
    """Створює профіль, у якому темп подій і клік узяті з реального запису.

    Args:
        path: Файл запису з `tools/mouse_capture.py`.
        seed: Зерно для решти параметрів, які з запису не відновлюються.

    Raises:
        FileNotFoundError: Якщо запису немає.
        ValueError: Якщо в записі бракує даних для калібрування.
    """
    if not Path(path).exists():
        raise FileNotFoundError(f"Немає запису {path}. Зроби його: python tools/mouse_capture.py {path}")

    summary = capture_summary(path)
    profile = new_profile(seed)

    if summary["polling_hz"] is None:
        raise ValueError(f"У {path} немає подій руху — калібрувати нічого.")

    # Обрізаємо в межах фізично осмисленого: занизька частота зробила б рух
    # ривковим, зависока — недосяжною для відтворення.
    profile.polling_hz = _snap_rate(min(1000.0, max(30.0, summary["polling_hz"])))

    if summary["polling_jitter"] is not None:
        # Верхня межа: за розкидом понад половину періоду поняття такту
        # втрачає сенс, і рух почав би смикатись.
        profile.polling_jitter = min(0.50, max(0.005, summary["polling_jitter"]))

    if summary["max_velocity"] is not None:
        profile.max_velocity = min(20000.0, max(1200.0, summary["max_velocity"]))

    if summary["hold_median"] is not None:
        profile.click_hold_median = summary["hold_median"]
    if summary["hold_sigma"] is not None:
        profile.click_hold_sigma = min(1.2, max(0.15, summary["hold_sigma"]))

    return profile
