"""
Калібрування профілю друкарки за реальним записом клавіатури.

Та сама ідея, що в `mouse/calibration.py`: модель розігрує параметри з
правдоподібних діапазонів, але «правдоподібних взагалі», а не для цієї
людини й цієї машини. З запису `tools/keyboard_capture.py` можна взяти:

  * утримання клавіші — медіану й розкид **по логарифмах**;
  * інтервали між натисканнями окремо для «різними руками», «однією рукою»
    та «одним пальцем» — звідси базовий темп і штрафи;
  * частку накладань, частку Backspace, темп опитування.

Гігієна даних, без якої числа брешуть (перевірено на живому записі):

  * **модифікатори геть.** Шість утримань Shift/Ctrl (218, 352, 1103 мс...)
    підняли CV утримання з 0.24 до 1.03 — і саме під цей артефакт спершу
    було підігнано `dwell_sigma`;
  * **автоповтор геть.** Затиснутий Ctrl породив 19 повторних `down` за
    секунду; вони лічилися як накладання і як інтервали в 30 мс;
  * **паузи геть.** Інтервал довший за 0.6 с — це не перехід між клавішами,
    а пауза на подумати;
  * скан-код розширених клавіш збирається як `0xE0XX`, як у `layout`.

    import effortless.keyboard as keyboard
    keyboard.set_profile(keyboard.profile_from_capture("human_kb.jsonl"))
"""
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List, Optional

from .layout import SC_SPACE, same_finger, same_hand
from .profile import TypistProfile, new_profile

__all__ = ["profile_from_capture", "capture_summary"]

# Віртуальні клавіші модифікаторів: Shift, Ctrl, Alt (обидва боки), Win.
_MODIFIER_VKS = frozenset((16, 17, 18, 160, 161, 162, 163, 164, 165, 91, 92))

_MAX_TRANSITION = 0.6     # с; довше — пауза, не перехід
_MIN_DWELL, _MAX_DWELL = 0.010, 1.0
_MIN_SAMPLES = 8

_STANDARD_RATES = (125, 250, 500, 1000)


def _scancode(event: dict) -> int:
    scan = int(event.get("scan", 0))
    if event.get("extended"):
        scan |= 0xE000
    return scan


def capture_summary(path: str) -> Dict[str, Optional[float]]:
    """Витягає з запису те, що можна перенести в профіль."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "t" in event and "down" in event and "vk" in event:
                rows.append(event)

    held: Dict[int, float] = {}
    dwells: List[float] = []
    downs: List[dict] = []
    rollovers = transitions = 0
    injected = total = 0
    backspaces = 0
    intervals: List[float] = []
    release_of: Dict[int, float] = {}
    prev_down: Optional[dict] = None

    for event in rows:
        total += 1
        injected += bool(event.get("injected", 0))
        vk = int(event["vk"])
        is_modifier = vk in _MODIFIER_VKS

        if event["down"]:
            if vk in held:
                continue  # автоповтор системи, не натискання руки
            held[vk] = event["t"]
            if is_modifier:
                continue
            downs.append(event)
            backspaces += vk == 8
            if prev_down is not None:
                gap = event["t"] - prev_down["t"]
                if 0 < gap <= _MAX_TRANSITION:
                    intervals.append(gap)
                    transitions += 1
                    prev_vk = int(prev_down["vk"])
                    if prev_vk in held or release_of.get(prev_vk, 0.0) > event["t"]:
                        rollovers += 1
            prev_down = event
        else:
            if vk in held:
                dwell = event["t"] - held.pop(vk)
                if not is_modifier and _MIN_DWELL <= dwell <= _MAX_DWELL:
                    dwells.append(dwell)
            release_of[vk] = event["t"]

    summary: Dict[str, Optional[float]] = {
        "events": float(total),
        "keys": float(len(downs)),
        "injected_share": injected / total if total else 0.0,
        "dwell_median": None,
        "dwell_sigma": None,
        "alternate_interval": None,
        "same_hand_penalty": None,
        "same_finger_penalty": None,
        "rollover_share": None,
        "backspace_share": None,
        "polling_hz": None,
    }

    if len(dwells) >= _MIN_SAMPLES:
        logs = [math.log(d) for d in dwells]
        summary["dwell_median"] = math.exp(statistics.median(logs))
        summary["dwell_sigma"] = statistics.pstdev(logs)

    # Інтервали за класом пари — по скан-кодах, бо саме вони знають геометрію.
    alternate, same_hand_gaps, same_finger_gaps = [], [], []
    for a, b in zip(downs, downs[1:]):
        gap = b["t"] - a["t"]
        if not 0 < gap <= _MAX_TRANSITION:
            continue
        sa, sb = _scancode(a), _scancode(b)
        # Пробіл — окремий клас (великий палець, свої множники в моделі),
        # у «чергування рук» він не входить.
        if sa == SC_SPACE or sb == SC_SPACE:
            continue
        if same_finger(sa, sb):
            same_finger_gaps.append(gap)
        elif same_hand(sa, sb):
            same_hand_gaps.append(gap)
        elif sa != sb:
            alternate.append(gap)

    if len(alternate) >= _MIN_SAMPLES:
        base = statistics.median(alternate)
        summary["alternate_interval"] = base
        if len(same_hand_gaps) >= _MIN_SAMPLES:
            summary["same_hand_penalty"] = statistics.median(same_hand_gaps) / base
        if len(same_finger_gaps) >= 5:
            summary["same_finger_penalty"] = statistics.median(same_finger_gaps) / base

    if transitions >= 20:
        summary["rollover_share"] = rollovers / transitions
    if len(downs) >= 50:
        summary["backspace_share"] = backspaces / len(downs)

    # Темп опитування — по найдрібнішому кроку сітки міток, як у миші.
    if len(rows) >= 40:
        gaps = sorted(b["t"] - a["t"] for a, b in zip(rows, rows[1:]) if 0 < b["t"] - a["t"] < 0.05)
        if len(gaps) >= 20:
            fine = statistics.median(gaps[: max(10, len(gaps) // 4)])
            measured = 1.0 / fine if fine > 0 else None
            if measured:
                best = min(_STANDARD_RATES, key=lambda r: abs(r - measured))
                summary["polling_hz"] = float(best if abs(best - measured) / measured < 0.25 else round(measured))

    return summary


def profile_from_capture(path: str, seed: Optional[int] = None) -> TypistProfile:
    """Профіль друкарки, у якому те, що видно в записі, взято з запису.

    Решта (одруки за видами, паузи речень, Shift) лишається випадковою: з
    потоку подій її не відновити, а вгадувати гірше, ніж розіграти.
    """
    if not Path(path).exists():
        raise FileNotFoundError(f"Немає запису {path}. Зроби його: python tools/keyboard_capture.py {path}")

    s = capture_summary(path)
    profile = new_profile(seed)

    if s["dwell_median"] is not None:
        profile.dwell_median = min(0.20, max(0.035, s["dwell_median"]))
    if s["dwell_sigma"] is not None:
        profile.dwell_sigma = min(0.6, max(0.12, s["dwell_sigma"]))
    if s["alternate_interval"] is not None:
        # У моделі базовий інтервал множиться на alternate_hand_bonus для
        # чергування рук, тому ділимо, щоб вихід збігся з виміряним.
        profile.base_interval = min(0.40, max(0.06, s["alternate_interval"] / profile.alternate_hand_bonus))
    if s["same_hand_penalty"] is not None:
        profile.same_hand_penalty = min(3.0, max(1.0, s["same_hand_penalty"] * profile.alternate_hand_bonus))
    if s["same_finger_penalty"] is not None:
        profile.same_finger_penalty = min(3.5, max(1.1, s["same_finger_penalty"] * profile.alternate_hand_bonus))
    if s["rollover_share"] is not None:
        # Частка накладань у виході — не сама схильність, а її наслідок через
        # темп і утримання; це груба обернена оцінка, підібрана на симуляції.
        profile.rollover_tendency = min(0.9, max(0.1, s["rollover_share"] / 0.35))
    if s["backspace_share"] is not None:
        # Один одрук стирає в середньому ~1.5 символу.
        profile.typo_rate = min(0.05, max(0.003, s["backspace_share"] / 1.5))
    if s["polling_hz"] is not None:
        profile.polling_hz = int(s["polling_hz"])

    return profile
