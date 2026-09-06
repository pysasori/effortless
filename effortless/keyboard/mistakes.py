"""
Одруки та їх виправлення.

Найпомітніша різниця між живим набором і згенерованим — не ритм, а те, що
людина помиляється. Текст без жодного Backspace на сотні символів — це не
«акуратний користувач», це статистична аномалія: типова частота одруків
1-3% натискань, і зникає вона лише в дуже коротких фрагментах.

Важливо, що одруки не випадкові за змістом. Живі помилки бувають чотирьох
видів, і кожен має фізичну причину:

    * **сусідня клавіша** — палець промазав; помилковий символ завжди
      фізично поруч із правильним, ніколи не на іншому краю клавіатури;
    * **перестановка** — руки випередили одна одну, тому переставляються
      здебільшого символи, які б'ють різними руками;
    * **здвоєння** — клавіша не встигла піднятись між двома ударами;
    * **пропуск** — удар вийшов надто слабким.

Випадковий символ з алфавіту, як роблять у наївних «humanizer»-ах, не
належить до жодного з цих видів і виглядає не як помилка руки, а як збій.

Виправлення теж має структуру: частину помилок людина ловить одразу, а
частину — аж на кінці слова, і тоді стирає все, що встигла набрати після
помилки. Перед серією Backspace є помітна пауза (це момент, коли помилку
побачили), а самі Backspace ідуть швидше за набір — це завчений рух.

Модуль **завжди** доводить текст до правильного: одруки тут — це шлях, а не
результат. Реальна людина частину помилок лишає, але контракт `type_text`
такого не передбачає.
"""
import random
from typing import List, Optional, Sequence, Tuple

from ..mouse.timing import lognormal_time
from .layout import SC_BACKSPACE, SC_SPACE, Keystroke, neighbors, same_hand
from .profile import TypistProfile, get_profile
from .rhythm import Stroke

__all__ = ["plan_strokes"]

SUBSTITUTION, TRANSPOSITION, DOUBLING, OMISSION = range(4)


def _typable(keystroke: Keystroke) -> bool:
    """Чи можна на цьому символі помилитись.

    Службові клавіші й символи, яких немає в розкладці, виключені: одрук —
    це промах пальця по клавіші, а не збій підстановки Unicode.
    """
    return (
        not keystroke.unicode_fallback
        and keystroke.char not in (None, "\n", "\t")
        and bool(neighbors(keystroke.scancode))
    )


def _pick_kind(profile: TypistProfile, has_next: bool) -> int:
    kinds = [SUBSTITUTION, TRANSPOSITION, DOUBLING, OMISSION]
    weights = list(profile.typo_mix)
    if not has_next:
        # Перестановка потребує наступного символу.
        weights[TRANSPOSITION] = 0.0
    if sum(weights) <= 0:
        # Профіль із самими перестановками на останньому символі: одруку тут
        # не буде. Раніше `random.choices` падав на нульовій сумі ваг.
        return DOUBLING
    return random.choices(kinds, weights=weights, k=1)[0]


def _wrong_and_intended(
    kind: int,
    keystrokes: Sequence[Keystroke],
    index: int,
) -> Optional[Tuple[List[Keystroke], List[Keystroke]]]:
    """Що буде набрано помилково і що мало бути набрано.

    Returns:
        (набране, задумане) або None, якщо цей вид одруку тут неможливий.
    """
    current = keystrokes[index]
    following = keystrokes[index + 1] if index + 1 < len(keystrokes) else None

    if kind == SUBSTITUTION:
        options = neighbors(current.scancode)
        if not options:
            return None
        # Модифікатори зберігаються: якщо мала бути велика літера, то й
        # промах буде великою — Shift уже затиснутий. `char=None`, а не «?»:
        # знак питання є в списку кінців речення, і кожен одрук-промах тягнув
        # за собою «паузу на межі речення» в пів секунди.
        wrong = Keystroke(random.choice(options), mods=current.mods, char=None)
        return [wrong], [current]

    if kind == TRANSPOSITION:
        if following is None or following.char in (None, "\n", "\t"):
            return None
        # Переставляються здебільшого символи, які б'ють різними руками:
        # саме там одна рука встигає випередити іншу.
        if same_hand(current.scancode, following.scancode) and random.random() < 0.7:
            return None
        return [following, current], [current, following]

    if kind == DOUBLING:
        return [current, current], [current]

    if kind == OMISSION:
        if following is None:
            return None
        return [following], [current, following]

    return None


def _common_prefix(wrong: Sequence[Keystroke], intended: Sequence[Keystroke]) -> int:
    """Скільки символів на початку збіглися — їх стирати не треба.

    Через це здвоєння виправляється одним Backspace без перенабору, як його
    і виправляє людина, а не «стерти все й набрати заново».
    """
    length = 0
    for a, b in zip(wrong, intended):
        if a.scancode != b.scancode or a.mods != b.mods:
            break
        length += 1
    return length


def _word_tail(keystrokes: Sequence[Keystroke], start: int, limit: int = 12) -> List[Keystroke]:
    """Решта слова після місця помилки — те, що встигнуть набрати до пробілу."""
    tail = []
    for keystroke in keystrokes[start:start + limit]:
        if keystroke.scancode == SC_SPACE or keystroke.char in (" ", "\n", "\t"):
            break
        tail.append(keystroke)
    return tail


def _emit_correction(
    wrong: Sequence[Keystroke],
    intended: Sequence[Keystroke],
    profile: TypistProfile,
) -> List[Stroke]:
    """Помилковий набір -> пауза -> Backspace -> правильний набір."""
    keep = _common_prefix(wrong, intended)
    to_delete = len(wrong) - keep
    to_retype = intended[keep:]

    strokes = [Stroke(keystroke) for keystroke in wrong]

    # Пауза — це момент, коли помилку побачили. Без неї серія Backspace
    # приклеюється до набору і виглядає як частина заздалегідь відомого плану.
    # Не кожну помилку помічають із затримкою: приблизно третину пальці
    # ловлять ще до того, як око її побачило, і Backspace іде без паузи. Без
    # цього КОЖНЕ виправлення мало паузу не менше 60 мс — регулярність.
    if random.random() < 0.3:
        pause = 0.0
    else:
        pause = lognormal_time(profile.correction_pause_median, 0.42, 0.06, 2.0)
    backspace = Keystroke(SC_BACKSPACE)
    for position in range(to_delete):
        strokes.append(
            Stroke(backspace, extra_pause=pause if position == 0 else 0.0, is_correction=True)
        )

    strokes.extend(Stroke(keystroke) for keystroke in to_retype)
    return strokes


def plan_strokes(
    keystrokes: Sequence[Keystroke],
    profile: Optional[TypistProfile] = None,
) -> List[Stroke]:
    """Розставляє одруки й виправлення у послідовності натискань.

    Args:
        keystrokes: Що треба надрукувати (`layout.resolve_text`).
        profile: Почерк; за замовчуванням — профіль сесії.

    Returns:
        Послідовність натискань, включно з помилковими і Backspace. Кінцевий
        текст на екрані збігається з вихідним.
    """
    profile = profile or get_profile()
    rate = profile.current_typo_rate

    strokes: List[Stroke] = []
    index = 0

    while index < len(keystrokes):
        current = keystrokes[index]

        if not _typable(current) or random.random() >= rate:
            strokes.append(Stroke(current))
            index += 1
            continue

        plan = _wrong_and_intended(
            _pick_kind(profile, has_next=index + 1 < len(keystrokes)), keystrokes, index
        )
        if plan is None:
            strokes.append(Stroke(current))
            index += 1
            continue

        wrong, intended = plan
        consumed = len(intended)

        # Частину помилок людина бачить одразу, а частину — лише дійшовши до
        # кінця слова. У другому випадку стирати доводиться разом із усім, що
        # встигла набрати після помилки.
        if random.random() >= profile.instant_catch_chance:
            tail = _word_tail(keystrokes, index + consumed)
            wrong = list(wrong) + tail
            intended = list(intended) + tail
            consumed += len(tail)

        strokes.extend(_emit_correction(wrong, intended, profile))
        index += consumed

    return strokes
