"""
Профіль друкарки — персональний почерк набору на час сесії.

Та сама логіка, що в `mouse/profile.py`: окремо взятий інтервал може бути
скільзавгодно правдоподібним, але якщо статистика всіх інтервалів однакова
від сесії до сесії — це вже підпис програми, а не людини.

Клавіатурний почерк вивчають давно і під назвою keystroke dynamics: за
розподілом часу утримання клавіш і пауз між ними людину впізнають з
точністю, порівнянною з відбитком пальця. Нам потрібен зворотний бік цієї ж
задачі: щоб згенерований потік мав ті властивості, за якими розпізнають
живий набір, а не рівні інтервали з `time.sleep`.

Три групи параметрів:

  * **сталі для сесії** — швидкість, частота опитування клавіатури (це взагалі
    властивість заліза), схильність до одруків, звичка тримати Shift
    протилежною рукою, базовий час утримання клавіші;
  * **похідні від втоми** — швидкість падає, одруків більшає, паузи довшають;
  * **разові** — розігруються на кожне натискання.

Ключова величина, якої немає в жодному наївному емуляторі — **rollover**:
частка переходів, де наступна клавіша натискається ще до відпускання
попередньої. У людини, яка друкує швидше ~40 слів/хв, таких переходів
більшість; у циклу `press(); release(); sleep()` їх рівно нуль. Це та сама
за характером ознака, що прапорець ін'єкції: не «підозріло», а неможливо.
"""
import random
import time
from dataclasses import dataclass, field
from typing import Tuple

__all__ = ["TypistProfile", "get_profile", "set_profile", "new_profile"]

# Частота опитування клавіатури — характеристика заліза. Офісні USB-клавіатури
# майже завжди 125 Гц; вищі частоти бувають лише в ігрових.
_POLLING_RATES: Tuple[Tuple[int, float], ...] = (
    (125, 0.72),
    (250, 0.08),
    (500, 0.10),
    (1000, 0.10),
)


@dataclass
class TypistProfile:
    """Манера набору. Створюється раз на сесію (`new_profile()`), далі втомлюється."""

    # --- залізо ---
    polling_hz: int = 125
    polling_jitter: float = 0.02

    # --- швидкість ---
    # Базовий інтервал між натисканнями сусідніх клавіш різними руками,
    # секунди. 0.16 с ≈ 75 слів/хв — швидкий, але звичайний користувач.
    base_interval: float = 0.16
    interval_sigma: float = 0.30

    # Множники інтервалу залежно від того, чим б'ють клавіші. Значення взяті
    # з порядку величин, які дають дослідження digraph latency: одним пальцем
    # помітно довше, різними руками помітно швидше.
    same_finger_penalty: float = 1.85
    same_hand_penalty: float = 1.75
    alternate_hand_bonus: float = 0.86
    travel_penalty: float = 0.055     # додаток за кожну ширину клавіші польоту

    # --- утримання клавіші (dwell time) ---
    dwell_median: float = 0.082
    # Історія цього числа. Спершу тут стояло 1.05 з поясненням «у живій руці
    # CV ~1.0». Той CV був артефактом: у записі його давали шість утримань
    # МОДИФІКАТОРІВ (218, 352, 1103 мс...), а 121 утримання літер мають CV 0.24
    # (оцінка по логарифмах 0.237). Sigma 1.05 робила розкид учетверо ширшим за
    # людський і, що гірше, породжувала утримання в пів секунди, які висіли
    # через кілька наступних натискань і з'їдали повторні літери.
    dwell_sigma: float = 0.25
    # Частка переходів з накладанням: наступна клавіша йде вниз раніше, ніж
    # попередня вгору. Залежить від швидкості; тут — схильність оператора.
    rollover_tendency: float = 0.65
    rollover_depth: float = 0.60      # наскільки глибоко накладаються, в частках dwell

    # --- паузи в тексті ---
    # Набір іде чанками: кілька символів упритул, потім мікропауза на
    # планування наступного шматка. Рівний потік без чанків виглядає як
    # автовідтворення запису, а не як набір.
    chunk_pause_median: float = 0.20
    word_pause_chance: float = 0.35   # ймовірність паузи на межі слова
    sentence_pause_median: float = 0.42
    # Пауза перед символом, який вимагає модифікатора або пошуку клавіші.
    awkward_pause_median: float = 0.13

    # --- одруки ---
    typo_rate: float = 0.018          # частка натискань з помилкою
    # Розподіл видів одруку: сусідня клавіша / перестановка / здвоєння / пропуск.
    typo_mix: Tuple[float, float, float, float] = (0.45, 0.25, 0.15, 0.15)
    # Ймовірність помітити помилку одразу; решту ловлять на межі слова.
    instant_catch_chance: float = 0.62
    correction_pause_median: float = 0.28
    backspace_interval: float = 0.075  # серія Backspace іде швидше за набір

    # --- Shift ---
    # Чи тисне оператор Shift протилежною рукою (звичка сліпого методу).
    opposite_shift: bool = True
    shift_lead_median: float = 0.055   # наскільки раніше Shift іде вниз
    shift_hold_extra: float = 0.030    # наскільки пізніше відпускається

    # --- втома ---
    started_at: float = field(default_factory=time.perf_counter)
    keys_done: int = 0

    # ------------------------------------------------------------------

    @property
    def fatigue(self) -> float:
        """Рівень втоми 0..1 — від часу роботи й кількості натискань."""
        by_time = (time.perf_counter() - self.started_at) / 2400.0
        by_keys = self.keys_done / 6000.0
        return min(1.0, by_time + by_keys)

    @property
    def current_interval(self) -> float:
        """Базовий інтервал з поправкою на втому: до +18% до кінця сесії."""
        return self.base_interval * (1.0 + 0.18 * self.fatigue)

    @property
    def current_typo_rate(self) -> float:
        """Втомлена людина помиляється майже вдвічі частіше."""
        return self.typo_rate * (1.0 + 0.9 * self.fatigue)

    @property
    def polling_interval(self) -> float:
        return 1.0 / self.polling_hz

    @property
    def wpm(self) -> float:
        """Приблизна швидкість у словах за хвилину (слово = 5 символів)."""
        return 60.0 / (self.current_interval * 5.0)

    def note_key(self) -> None:
        self.keys_done += 1


def new_profile(seed: int = None) -> TypistProfile:
    """Створює випадковий профіль друкарки.

    Args:
        seed: Якщо заданий — профіль відтворюваний (для тестів і для
              `tools/keyboard_stats.py`). У бою лишай None.
    """
    rng = random.Random(seed)

    rates, weights = zip(*_POLLING_RATES)
    base_interval = rng.uniform(0.105, 0.235)

    # Швидкі друкарки накладають клавіші сильніше: у цьому й полягає
    # швидкість сліпого методу — рука не чекає, поки палець підніметься.
    speed_factor = (0.235 - base_interval) / 0.13  # 0 — повільно, 1 — швидко

    return TypistProfile(
        polling_hz=rng.choices(rates, weights=weights, k=1)[0],
        polling_jitter=rng.uniform(0.012, 0.045),
        base_interval=base_interval,
        interval_sigma=rng.uniform(0.24, 0.40),
        same_finger_penalty=rng.uniform(1.6, 2.15),
        same_hand_penalty=rng.uniform(1.45, 2.10),
        alternate_hand_bonus=rng.uniform(0.80, 0.92),
        travel_penalty=rng.uniform(0.035, 0.075),
        dwell_median=rng.uniform(0.060, 0.105),
        dwell_sigma=rng.uniform(0.18, 0.34),
        rollover_tendency=min(0.90, max(0.20, rng.gauss(0.45 + 0.45 * speed_factor, 0.10))),
        rollover_depth=rng.uniform(0.50, 0.70),
        chunk_pause_median=rng.uniform(0.14, 0.30),
        word_pause_chance=rng.uniform(0.20, 0.50),
        sentence_pause_median=rng.uniform(0.30, 0.70),
        awkward_pause_median=rng.uniform(0.08, 0.20),
        typo_rate=rng.uniform(0.006, 0.032),
        instant_catch_chance=rng.uniform(0.45, 0.78),
        correction_pause_median=rng.uniform(0.18, 0.45),
        backspace_interval=rng.uniform(0.055, 0.105),
        opposite_shift=rng.random() < 0.75,
        shift_lead_median=rng.uniform(0.030, 0.085),
        shift_hold_extra=rng.uniform(0.015, 0.050),
    )


_profile: TypistProfile = None


def get_profile() -> TypistProfile:
    """Профіль поточної сесії, створюється за першим зверненням."""
    global _profile
    if _profile is None:
        _profile = new_profile()
    return _profile


def set_profile(profile: TypistProfile) -> None:
    """Встановлює профіль сесії (наприклад, відтворюваний, з `new_profile(seed)`)."""
    global _profile
    _profile = profile
