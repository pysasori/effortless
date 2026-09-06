"""
Ритм набору: з послідовності натискань — розклад подій у часі.

Тут живе вся «людяність» клавіатури. Три речі, які роблять розклад живим і
яких немає в наївному `for ch in text: press(ch); sleep(random())`:

**1. Інтервал залежить від пари клавіш, а не від символу.**
Дві клавіші одним пальцем — палець мусить фізично переїхати, це найповільніший
перехід. Чергування рук — найшвидший: друга рука вже в дорозі, поки перша
дотискає. Різниця приблизно вдвічі. У бота, який спить `uniform(0.05, 0.15)`,
гістограма інтервалів для «одним пальцем» і «різними руками» однакова, і це
видно на паруванні digraph latency з геометрією за пару рядків тексту.

**2. Накладання (rollover).** У людини, яка друкує швидше ~40 слів/хв,
наступна клавіша йде вниз раніше, ніж відпускається попередня. Тобто інтервал
`up[i] -> down[i+1]` регулярно **від'ємний**. Цикл `down; up; sleep` дає рівно
нуль таких переходів — ознака не «підозріла», а неможлива на живій руці.

**3. Текст має структуру.** Набір іде чанками по кілька символів упритул,
між ними — мікропаузи на планування; на межі речення пауза довша; перед
символом, який вимагає Shift або пошуку клавіші, є помітне зволікання.
Рівний потік без цієї структури схожий на відтворення запису, а не на набір.

Розклад будується офлайн, у відносному часі, і лише потім укладається на
сітку опитування в `emitter.py`. Завдяки цьому весь ритм можна порахувати й
перевірити без жодної реальної події — цим користується `tools/keyboard_stats.py`.
"""
import random
from dataclasses import dataclass
from typing import List, Optional, Sequence

from ..mouse.timing import gauss_clamped, lognormal_time
from .layout import (MOD_ALT, MOD_CTRL, MOD_SHIFT, SC_LALT, SC_LCTRL, SC_LSHIFT, SC_RALT,
                     SC_RSHIFT, SC_SPACE, GEOMETRY, Keystroke, same_finger, same_hand, travel)
from .profile import TypistProfile, get_profile

__all__ = ["Stroke", "KeyEvent", "build_schedule", "digraph_interval"]

# Символи, після яких людина робить довшу паузу — кінець думки, а не слова.
_SENTENCE_END = ".!?…:;"
# Пауза перед цими символами: їх не «знаходить» м'язова пам'ять слова.
_AWKWARD = "0123456789@#$%^&*()_+=[]{}|\\<>/~`\"'"


@dataclass
class Stroke:
    """Заплановане натискання разом з тим, що з ним пов'язано.

    Attributes:
        keystroke: Яку клавішу бити.
        extra_pause: Додаткова пауза перед натисканням, секунди. Сюди
            потрапляє зволікання перед виправленням одруку.
        is_correction: Натискання належить до виправлення помилки. Серія
            Backspace іде своїм, швидшим темпом — це завчений рух, а не набір.
    """

    keystroke: Keystroke
    extra_pause: float = 0.0
    is_correction: bool = False


@dataclass
class KeyEvent:
    """Подія в розкладі: коли і що.

    Attributes:
        t: Час від початку набору, секунди.
        scancode: Скан-код клавіші.
        down: True — натискання, False — відпускання.
    """

    t: float
    scancode: int
    down: bool


@dataclass
class _Press:
    """Проміжна форма: натискання з тривалістю утримання."""

    scancode: int
    t: float
    dwell: float
    mods: int
    is_correction: bool = False

    @property
    def release(self) -> float:
        return self.t + self.dwell


# ----------------------------------------------------------------------
# Інтервали
# ----------------------------------------------------------------------

def digraph_interval(prev: int, current: int, profile: TypistProfile) -> float:
    """Час між натисканням `prev` і натисканням `current` (down-to-down).

    Саме ця величина в літературі зветься digraph latency і саме за її
    розподілом упізнають конкретну людину. Множники беруться з фізики руки:
    один палець на дві клавіші — найдовше, дві руки — найшвидше.
    """
    base = profile.current_interval

    if same_finger(prev, current):
        base *= profile.same_finger_penalty
    elif prev == current:
        # Та сама клавіша двічі — палець не переїжджає, лише піднімається й
        # падає. Це швидко, але не миттєво: клавіша мусить встигнути вийти.
        base *= 0.92
    elif same_hand(prev, current):
        base *= profile.same_hand_penalty
    else:
        base *= profile.alternate_hand_bonus

    base *= 1.0 + profile.travel_penalty * travel(prev, current)

    # Пробіл б'є великий палець, який уже лежить на клавіші, тому перехід
    # літера->пробіл швидший за будь-який літерний (у живій руці 0.57 від
    # внутрішньослівного, у моделі без цієї поправки виходило 0.94), а
    # пробіл->літера — повільніший: рука повертається на ряд.
    if current == SC_SPACE:
        base *= 0.62
    elif prev == SC_SPACE:
        base *= 1.20

    return lognormal_time(base, profile.interval_sigma, 0.022, 1.5)


def _context_pause(prev_char: Optional[str], keystroke: Keystroke,
                   profile: TypistProfile) -> float:
    """Додаткова пауза від структури тексту, а не від геометрії клавіш."""
    pause = 0.0
    char = keystroke.char

    if prev_char and prev_char in _SENTENCE_END:
        pause += lognormal_time(profile.sentence_pause_median, 0.45, 0.05, 2.5)
    elif prev_char == " ":
        # Пауза на межі слова — неперервна величина з важким хвостом, а не
        # монетка «є пауза / нема». Монетка давала двокомпонентну суміш:
        # половина слів починалась рівно за digraph-інтервал, половина — з
        # окремою паузою, і між ними була порожнеча. `word_pause_chance`
        # тепер задає, наскільки важкий хвіст.
        median = profile.chunk_pause_median * (0.15 + 0.7 * profile.word_pause_chance)
        pause += lognormal_time(median, 0.95, 0.0, 1.5)

    if char and (char in _AWKWARD or keystroke.mods & (MOD_CTRL | MOD_ALT)):
        pause += lognormal_time(profile.awkward_pause_median, 0.38, 0.01, 0.8)

    return pause


# ----------------------------------------------------------------------
# Побудова розкладу
# ----------------------------------------------------------------------

def _plan_presses(strokes: Sequence[Stroke], profile: TypistProfile) -> List[_Press]:
    """Часи натискань і тривалості утримання, ще без модифікаторів."""
    presses: List[_Press] = []
    t = 0.0
    prev_scan: Optional[int] = None
    prev_char: Optional[str] = None

    for stroke in strokes:
        keystroke = stroke.keystroke

        if prev_scan is None:
            gap = 0.0
        elif stroke.is_correction:
            # Backspace — завчена серія: темп свій і майже не залежить від
            # того, яка клавіша була перед ним.
            gap = lognormal_time(profile.backspace_interval, 0.30, 0.02, 0.6)
        else:
            gap = digraph_interval(prev_scan, keystroke.scancode, profile)
            gap += _context_pause(prev_char, keystroke, profile)

        t += gap + stroke.extra_pause

        presses.append(
            _Press(
                scancode=keystroke.scancode,
                t=t,
                # Стеля 0.30: у 121 утриманні літер із живого запису найдовше
                # було 128 мс (95-й перцентиль). Довші — це модифікатори й
                # навмисні затиски, а не набір. Попередня стеля 0.65 була
                # підігнана під артефакт CV ~1.0 (див. profile.dwell_sigma).
                dwell=lognormal_time(profile.dwell_median, profile.dwell_sigma, 0.020, 0.30),
                mods=keystroke.mods,
                is_correction=stroke.is_correction,
            )
        )
        prev_scan = keystroke.scancode
        prev_char = keystroke.char

    return presses


def _apply_rollover(presses: List[_Press], profile: TypistProfile) -> None:
    """Вирішує, які переходи йдуть з накладанням клавіш.

    Накладання не «домальовується» до довільного переходу: воно фізично може
    статися лише там, де наступна клавіша йде вниз раніше, ніж палець встиг
    підняти попередню, тобто де інтервал сумірний з часом утримання. Тому
    тут два випадки.

    Якщо утримання й так довше за інтервал, накладання виникло само — його
    лишають (з обмеженою глибиною) або, з рештою ймовірності, розводять.
    Якщо ж інтервал помітно довший за утримання, накладання можливе тільки
    на швидких переходах, і саме там воно й дораховується.

    Наслідок, який і потрібен: частка накладань виходить не константою з
    профілю, а функцією темпу — купчиться на швидких буквосполученнях і
    зникає на паузах, як у живого набору.
    """
    for index, (current, following) in enumerate(zip(presses, presses[1:])):
        gap = following.t - current.t

        # Та сама клавіша двічі поспіль накластись фізично не може: щоб
        # натиснути її вдруге, треба спершу відпустити.
        if current.scancode == following.scancode:
            _separate(current, following, profile, presses, index + 1)
            continue

        limit = max(0.015, profile.rollover_depth * current.dwell)

        # Накладання — це фізика двох рук: коли обидві клавіші б'є одна рука,
        # палець мусить піднятись, щоб інший опустився. Заміряно на живій
        # руці: різними руками 28%, однією рукою 7%, одним пальцем 0.
        # У бота було 26 / 20 / 15 — накладання не знало, чим б'ють.
        if same_finger(current.scancode, following.scancode):
            tendency = 0.0
        elif same_hand(current.scancode, following.scancode):
            tendency = profile.rollover_tendency * 0.3
        else:
            tendency = profile.rollover_tendency

        if current.dwell > gap:
            if random.random() >= tendency:
                _separate(current, following, profile, presses, index + 1)
            else:
                current.dwell = min(current.dwell, gap + limit)
        elif gap < current.dwell * (1.0 + profile.rollover_depth):
            # Перехід повільніший за утримання: рука встигає підняти палець,
            # і накладання можливе лише коли інтервал майже дорівнює утриманню.
            if random.random() < tendency:
                current.dwell = gap + limit * random.uniform(0.2, 1.0)

        # Двоклавішне накладання — це down[i+1] < up[i] < up[i+1]. Утримання,
        # що переживає ще й відпускання наступної клавіші, — уже не накладання,
        # а завислий палець; воно тягнеться через кілька наступних натискань,
        # і коли той самий скан-код повторюється за 2-3 клавіші («sis», «ppi»,
        # «here»), друге натискання стає no-op — літера зникає. Заміряно: ~7%
        # прогонів короткого тексту.
        current.dwell = min(current.dwell, max(0.018, following.release - current.t
                                                - gauss_clamped(0.008, 0.003, 0.002, 0.02)))

    # Остання лінія проти того самого: жодна клавіша не лишається натиснутою
    # до свого наступного натискання, хай як далеко воно стоїть.
    next_same: dict = {}
    for press in reversed(presses):
        later = next_same.get(press.scancode)
        if later is not None:
            press.dwell = min(press.dwell, max(0.018, later.t - press.t - _release_margin(profile)))
        next_same[press.scancode] = press


def _release_margin(profile: TypistProfile) -> float:
    """Проміжок між відпусканням клавіші й наступним натисканням.

    Логнормальний з ПЕРЕВИБІРКОЮ понад фізичний мінімум (1.6 такту), а не
    `max(мінімум, gauss)`: обрізання давало атом рівно на 12.800 мс у 3.5%
    переходів — одне й те саме значення з точністю до мікросекунди, повторене
    тисячі разів, а в живій руці цей проміжок гладко проходить через нуль.
    """
    floor = profile.polling_interval * 1.6
    for _ in range(12):
        value = lognormal_time(0.026, 0.5, 0.004, 0.14)
        if value >= floor:
            return value
    return floor * random.uniform(1.0, 1.6)


def _separate(current: _Press, following: _Press, profile: TypistProfile,
              presses: List[_Press], following_index: int) -> None:
    """Розводить утримання й наступне натискання так, щоб клавіша встигла піднятись.

    Проміжок між відпусканням і наступним натисканням не може бути коротшим
    за період опитування з запасом. Клавіатура шле не події, а стан: якщо
    up і повторний down тієї самої клавіші потрапляють в один такт, у звіті
    вона просто «ще натиснута», і друге натискання не існує. Заміряно до
    правки: «Hello» -> «Helo», «jumps» -> «jups», а одрук-здвоєння з'їдав
    саму літеру, бо Backspace стирав єдину, що зареєструвалась.

    Якщо вкоротити утримання нижче фізичного мінімуму не можна, зсувається
    вперед НАСТУПНЕ натискання (і все після нього): краще трохи повільніший
    набір, ніж загублена літера.
    """
    if current.release <= following.t:
        return
    margin = _release_margin(profile)
    shortest = 0.018
    wanted = following.t - current.t - margin
    if wanted >= shortest:
        current.dwell = wanted
        return

    current.dwell = shortest
    delay = current.t + shortest + margin - following.t
    for later in presses[following_index:]:
        later.t += delay


def _shift_scancode(scancode: int, profile: TypistProfile) -> int:
    """Який саме Shift тиснути під літеру.

    Сліпий метод вимагає протилежної руки: для лівої половини клавіатури —
    правий Shift, і навпаки. Бот, який завжди шле лівий, видає себе на кожній
    великій літері з лівої половини — на залізі така комбінація майже не
    трапляється.
    """
    if not profile.opposite_shift:
        return SC_LSHIFT

    pos = GEOMETRY.get(scancode)
    if pos is None or pos.hand == 0:
        return SC_LSHIFT
    return SC_RSHIFT if pos.hand < 0 else SC_LSHIFT


def _modifier_events(presses: Sequence[_Press], profile: TypistProfile) -> List[KeyEvent]:
    """Події модифікаторів, з групуванням підряд.

    Модифікатор натискається раніше за клавішу й відпускається пізніше — це
    те, що робить рука. Кілька символів підряд з тим самим модифікатором
    покриваються одним утриманням: людина не відпускає Shift між двома
    великими літерами.
    """
    events: List[KeyEvent] = []
    index = 0

    while index < len(presses):
        mods = presses[index].mods
        if not mods:
            index += 1
            continue

        run_end = index
        while run_end + 1 < len(presses) and presses[run_end + 1].mods == mods:
            # Розрив у наборі рве утримання: через півсекунди пауза руки
            # Shift уже відпущений, навіть якщо далі знову велика літера.
            if presses[run_end + 1].t - presses[run_end].release > 0.45:
                break
            run_end += 1

        first, last = presses[index], presses[run_end]
        lead = lognormal_time(profile.shift_lead_median, 0.35, 0.008, 0.25)
        tail = lognormal_time(profile.shift_hold_extra, 0.40, 0.004, 0.20)
        press_at = first.t - lead
        release_at = last.release + tail

        # Дзеркальна межа: модифікатор не може піти вниз РАНІШЕ, ніж пішла
        # вниз попередня клавіша, якій він не потрібен — інакше велика
        # літера «протікає» назад: «broWn». Раніше за її відпускання — можна,
        # це звичайне накладання, і на символ воно вже не впливає.
        if index > 0:
            previous = presses[index - 1]
            # Запас не менший за такт із гаком: якщо Shift і попередня літера
            # потрапляють в один такт, транспорт бачить їх одним станом і
            # порядок між ними втрачається — Shift може піти першим і
            # капіталізувати попередню літеру.
            press_at = max(press_at, previous.t + _release_margin(profile))
            # ...і не пізніше за власну клавішу.
            press_at = min(press_at, first.t - 0.002)

        # Модифікатор МУСИТЬ піднятись до того, як піде вниз наступна клавіша,
        # якій він не потрібен. Інакше при накладанні (мала літера йде вниз
        # раніше, ніж підняли попередню) Shift ще затиснутий — і на екрані
        # «HEllo» замість «Hello». Заміряно до цієї правки: 12.6% малих літер
        # після великої друкувались великими; на швидких профілях — усі.
        # Це не ознака бота, це просто неправильний текст.
        if run_end + 1 < len(presses):
            following = presses[run_end + 1]
            margin = gauss_clamped(0.010, 0.004, 0.002, 0.025)
            release_at = min(release_at, following.t - margin)
            # Але не раніше, ніж клавіша під ним устигла зареєструватись.
            release_at = max(release_at, last.t + 0.004)

        for flag, scancode in (
            (MOD_SHIFT, _shift_scancode(first.scancode, profile)),
            (MOD_CTRL, SC_LCTRL),
            (MOD_ALT, SC_RALT if mods & MOD_CTRL and mods & MOD_ALT else SC_LALT),
        ):
            if mods & flag:
                events.append(KeyEvent(press_at, scancode, True))
                events.append(KeyEvent(release_at, scancode, False))

        index = run_end + 1

    return events


def build_schedule(strokes: Sequence[Stroke],
                   profile: Optional[TypistProfile] = None) -> List[KeyEvent]:
    """Будує повний розклад подій клавіатури у відносному часі.

    Args:
        strokes: Що набирати, вже з одруками й виправленнями (`mistakes.py`).
        profile: Почерк; за замовчуванням — профіль сесії.

    Returns:
        Події, відсортовані за часом. Час — від нуля; прив'язку до реального
        годинника робить `emitter.play`.
    """
    profile = profile or get_profile()
    if not strokes:
        return []

    presses = _plan_presses(strokes, profile)
    _apply_rollover(presses, profile)

    events = _modifier_events(presses, profile)
    for press in presses:
        events.append(KeyEvent(press.t, press.scancode, True))
        events.append(KeyEvent(press.release, press.scancode, False))

    # Відпускання перед натисканням при однаковому часі: інакше в стані
    # промайне мить, коли натиснуті обидві клавіші, чого в цю мить не було.
    events.sort(key=lambda event: (event.t, event.down))

    # Розклад може початися з від'ємного часу через випередження модифікатора.
    shift = min(event.t for event in events)
    if shift < 0:
        for event in events:
            event.t -= shift

    return events
