"""
Генерація людяної траєкторії наведення курсора.

Модель складається з трьох незалежних шарів — кожен закриває свій клас
ознак, за якими розпізнають бота.

**1. Структура руху — модель субрухів (Meyer et al.).**
Людина не веде курсор до цілі одним рухом. Спочатку йде швидкий балістичний
кидок "приблизно туди" — він за статистикою трохи *недольотний* (близько 90%
відстані), бо недоліт коштує дешевше за переліт. Далі, якщо курсор не
влучив у ціль, йдуть 1-3 коротші коригувальні рухи. Саме тому траєкторія
живої руки в кінці має характерне "сходинкове" уповільнення, а не рівний
в'їзд у точку.

**2. Профіль швидкості — мінімум ривка з асиметрією.**
Класичний min-jerk дає симетричний дзвін швидкості. У людини дзвін
асиметричний: пік швидкості припадає приблизно на 40% тривалості, після
чого йде довше гальмування. Асиметрія робиться деформацією часу.

**3. Шум — тремор, дрейф і викривлення.**
Фізіологічний тремор кисті — вузька смуга 8-12 Гц з амплітудою в частки
пікселя; повільний дрейф 1-3 Гц дає амплітуду близько пікселя; додатково
накладений процес Орнштейна-Уленбека прибирає "чистоту" спектра, бо сума
кількох синусоїд у спектрі виглядає так само штучно, як і біле шумове поле.
Плюс траєкторія трохи вигинається вбік — рука рухається дугою через лікоть,
а не по прямій.

Модуль **чистий**: нічого не рухає, лише повертає список семплів. Це
навмисно — так траєкторію можна проаналізувати статистично, не торкаючись
курсора (див. `tools/mouse_stats.py`).
"""
import math
import random
from typing import List, NamedTuple, Optional, Tuple

from .profile import OperatorProfile, get_profile

__all__ = [
    "Sample",
    "generate_path",
    "generate_idle_path",
    "reaction_delay",
    "click_hold_duration",
]

# Максимум коригувальних субрухів. Більше трьох — це вже не "не влучив", а
# тремтіння над ціллю, чого в нормальної людини не буває.
_MAX_CORRECTIONS = 3

# Найкоротше правдоподібне утримання кнопки. Нижче цього людський палець
# не встигає: 25 мс, що стояли раніше, фізіологічно неможливі.
_MIN_CLICK_HOLD = 0.045

# Типовий "розмір цілі", коли викликач його не назвав.
_DEFAULT_TARGET_RADIUS = 8.0

# Наскільки корекція починається РАНІШЕ, ніж завершився попередній субрух.
# 0.7 означає, що вона стартує на 70% його тривалості — тобто профілі
# швидкості накладаються, і швидкість між ними не падає до нуля.
_SUBMOVEMENT_OVERLAP = (0.55, 0.85)

# Підгінні параметри моделі зібрані тут навмисно: їх калібрують по запису
# живої руки (див. calibration.py і tools/mouse_stats.py), і шукати їх по
# тілу функцій незручно.

# Наскільки первинний кидок недольотний. Недоліт коштує дешевше за переліт,
# тому людина систематично не доводить руку до цілі. Значення підібране по
# запису живої руки: воно керує тим, наскільки помітні довідні рухи на
# профілі швидкості (0.93 давало 1.05 субруху на наведення, 0.82 дає 1.28
# при заміряних у людини 1.27).
_PRIMARY_GAIN = (0.82, 0.05)

# Частка загального часу, що припадає на первинний кидок. Чим менша, тим
# швидші (і помітніші на профілі швидкості) довідні рухи.
_PRIMARY_TIME_SHARE = (0.62, 0.78)

# У скільки разів розкид кінцевої точки більший уздовж руху, ніж упоперек.
_ENDPOINT_ANISOTROPY = 1.6

# Наскільки слабший тремор руки, що лежить на миші, порівняно з рукою,
# яка веде курсор.
_IDLE_NOISE_SCALE = 0.35

# Розкид амплітуди спокою від паузи до паузи. Без нього кожен клік давав би
# схожу кількість мікрозсувів, а це така сама регулярність, як і повна
# нерухомість: іноді рука під час натискання підтискається й завмирає
# майже повністю, іноді помітно гуляє.
_IDLE_NOISE_SPREAD = 0.55


class Sample(NamedTuple):
    """Одна точка траєкторії: час від початку руху (с) і координати (px)."""

    t: float
    x: float
    y: float


class _Submovement(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
    duration: float
    skew: float       # деформація часу: <1 зсуває пік швидкості на початок
    curvature: float  # бічний вигин у частках довжини субруху


def _min_jerk(u: float) -> float:
    """Профіль мінімального ривка: 0 -> 1 з нульовими швидкістю і прискоренням на кінцях."""
    return u * u * u * (10.0 - 15.0 * u + 6.0 * u * u)


def _fitts_time(distance: float, target_width: float, profile: OperatorProfile) -> float:
    """Тривалість наведення за законом Фітца, з логнормальним розкидом.

    T = a + b * log2(2D / W + 1). Одиниця під логарифмом — форма Шеннона:
    вона не йде в мінус на дуже близьких цілях.
    """
    index_of_difficulty = math.log2(2.0 * distance / max(target_width, 1.0) + 1.0)
    nominal = profile.fitts_a + profile.fitts_b * index_of_difficulty
    # Людина не повторює той самий рух за той самий час навіть двічі поспіль.
    return nominal * profile.current_speed_scale * random.lognormvariate(0.0, 0.16)


def _plan_submovements(
    start: Tuple[float, float],
    aim: Tuple[float, float],
    target_radius: float,
    profile: OperatorProfile,
    guarantee: Tuple[float, float],
) -> List[_Submovement]:
    """Розкладає наведення на первинний кидок і коригувальні субрухи.

    Args:
        aim: Куди людина ЦІЛИТЬСЯ — центр цілі плюс систематичний зсув руки.
        guarantee: Справжній центр цілі. Саме відносно нього перевіряється,
            чи влучили: зсув прицілу не має виносити клік за межі кнопки.
    """
    x, y = start
    aim_x, aim_y = aim

    total_distance = math.hypot(aim_x - x, aim_y - y)
    total_time = _fitts_time(total_distance, target_radius * 2.0, profile)

    dx, dy = aim_x - x, aim_y - y
    # Похибка кидка росте пропорційно його амплітуді (закон Вебера для моторики).
    sigma = profile.current_endpoint_noise * total_distance

    if random.random() < profile.overshoot_tendency:
        gain = random.uniform(1.02, 1.11)
    else:
        # Систематичний недоліт: типово 88-97% відстані.
        gain = random.gauss(*_PRIMARY_GAIN)

    # Розкид кінцевої точки анізотропний: уздовж напрямку руху він у півтора-два
    # рази більший, ніж упоперек — рука краще контролює «куди», ніж «наскільки».
    # Ізотропний розкид (0.998 відношення осей) був однією з ознак моделі.
    if total_distance > 1e-6:
        along_x, along_y = dx / total_distance, dy / total_distance
    else:
        along_x, along_y = 1.0, 0.0
    along = random.gauss(0.0, sigma * _ENDPOINT_ANISOTROPY)
    across = random.gauss(0.0, sigma)
    end_x = x + dx * gain + along * along_x - across * along_y
    end_y = y + dy * gain + along * along_y + across * along_x

    primary = _Submovement(
        x0=x,
        y0=y,
        x1=end_x,
        y1=end_y,
        duration=0.0,  # проставимо нижче, коли знатимемо кількість корекцій
        skew=random.uniform(0.70, 0.88),
        curvature=profile.curvature_bias * random.lognormvariate(0.0, 0.35),
    )

    submovements = [primary]
    x, y = end_x, end_y

    # Корекції, поки курсор поза ціллю. Поріг трохи "розмитий": людина
    # припиняє довідні рухи не рівно на межі кнопки, а коли суб'єктивно
    # впевнена, що влучила.
    accept_radius = target_radius * random.uniform(0.25, 0.55)
    for _ in range(_MAX_CORRECTIONS):
        error = math.hypot(aim_x - x, aim_y - y)
        if error <= accept_radius:
            break

        # Корекція забирає більшу частину похибки, але теж не ідеальна.
        correction_gain = random.gauss(0.88, 0.09)
        corr_sigma = 0.16 * error
        cx = x + (aim_x - x) * correction_gain + random.gauss(0.0, corr_sigma)
        cy = y + (aim_y - y) * correction_gain + random.gauss(0.0, corr_sigma)

        submovements.append(
            _Submovement(
                x0=x,
                y0=y,
                x1=cx,
                y1=cy,
                duration=0.0,
                # Дрібні довідні рухи майже симетричні: гальмувати нема чого.
                skew=random.uniform(0.88, 1.0),
                curvature=profile.curvature_bias * 0.3,
            )
        )
        x, y = cx, cy

    # Гарантія влучання: якщо після всіх корекцій курсор усе ще поза ціллю,
    # підтягуємо фінальну точку всередину.
    #
    # Рахується вона від `guarantee`, тобто від СПРАВЖНЬОЇ цілі, а не від
    # точки прицілювання. Різниця між ними — систематичний зсув руки
    # (`aim_bias`), сталий на всю сесію. Поки гарантія рахувалась від
    # зсунутої точки, зсув просто виносив влучання за межі кнопки: на
    # невдалому профілі зі зсувом 3 px по вертикалі більш ніж половина
    # кліків по трипіксельній цілі летіла повз — і так усю сесію, бо зсув
    # не перерозігрується.
    goal_x, goal_y = guarantee
    last = submovements[-1]
    error = math.hypot(goal_x - last.x1, goal_y - last.y1)
    if error > target_radius * 0.85:
        scale = (target_radius * random.uniform(0.15, 0.75)) / error
        submovements[-1] = last._replace(
            x1=goal_x + (last.x1 - goal_x) * scale,
            y1=goal_y + (last.y1 - goal_y) * scale,
        )

    # Розподіл часу: первинний кидок забирає більшість, корекції — короткі
    # й дедалі коротші.
    count = len(submovements)
    if count == 1:
        weights = [1.0]
    else:
        primary_share = random.uniform(*_PRIMARY_TIME_SHARE)
        rest = [0.55 ** i for i in range(count - 1)]
        rest_sum = sum(rest)
        weights = [primary_share] + [(1.0 - primary_share) * w / rest_sum for w in rest]

    return [
        sub._replace(duration=max(0.012, total_time * w))
        for sub, w in zip(submovements, weights)
    ]


def _peak_speed(
    submovements: List[_Submovement],
    onsets: List[float],
    total: float,
    probes: int = 120,
) -> float:
    """Оцінює пікову швидкість траєкторії, px/с.

    Рахується грубою вибіркою (сто з гаком точок): точне аналітичне значення
    для суми зміщених профілів мінімального ривка виводити не варто — оцінка
    потрібна лише для того, щоб вирішити, розтягувати рух чи ні.
    """
    if total <= 0:
        return 0.0

    step = total / probes
    peak = 0.0
    prev = None

    for i in range(probes + 1):
        t = total * i / probes
        x = y = 0.0
        for sub, onset in zip(submovements, onsets):
            tau = (t - onset) / sub.duration
            if tau <= 0.0:
                continue
            u = _min_jerk(min(1.0, tau) ** sub.skew)
            x += (sub.x1 - sub.x0) * u
            y += (sub.y1 - sub.y0) * u
        if prev is not None:
            peak = max(peak, math.hypot(x - prev[0], y - prev[1]) / step)
        prev = (x, y)

    return peak


class _Resonator:
    """Смуговий шум: гармонічний осцилятор, який штовхає білий шум.

    Дає широку смугу навколо своєї частоти, а не одну лінію. Саме цим живий
    тремор відрізняється від синусоїди: у спектрі руки — горб 8-12 Гц, а не
    спиця. Попередня версія була двома чистими синусоїдами: потужність рівно
    на частоті тремору перевищувала сусідні частоти у 139 разів, а x та y мали
    одну частоту й одну амплітуду з різницею лише у фазі — тобто курсор
    малював замкнений еліпс сталого розміру.

    Крок робиться напівнеявним Ейлером по фактичному `dt`, бо семпли лягають
    нерівномірно. `sigma` підібрана так, щоб стаціонарне середньоквадратичне
    відхилення дорівнювало `amp`: для осцилятора з білою силою дисперсія
    x = sigma^2 / (4 * zeta * omega^3).
    """

    _MAX_STEP = 0.004  # с; більший крок ділиться на підкроки заради стійкості

    def __init__(self, hz: float, amp: float, zeta: float) -> None:
        self._omega = 2.0 * math.pi * hz
        self._zeta = zeta
        self._sigma = amp * math.sqrt(4.0 * zeta * self._omega ** 3)
        # Стартуємо не з нуля — інакше перша секунда сесії була б «холодна».
        self._x = random.gauss(0.0, amp)
        self._v = random.gauss(0.0, amp * self._omega)

    def step(self, dt: float) -> float:
        if dt <= 0.0:
            return self._x
        parts = max(1, int(math.ceil(dt / self._MAX_STEP)))
        h = dt / parts
        for _ in range(parts):
            force = random.gauss(0.0, self._sigma / math.sqrt(h))
            self._v += (-self._omega ** 2 * self._x - 2.0 * self._zeta * self._omega * self._v + force) * h
            self._x += self._v * h
        return self._x


class _HandNoise:
    """Тремор + дрейф + процес Орнштейна-Уленбека, спільні на всю СЕСІЮ.

    Один екземпляр на профіль: осцилятори не перезапускаються ні між
    субрухами, ні між рухами, ні між паузами. Раніше кожен рух і кожна пауза
    заводили новий шум з новою фазою — 10-герцовий тремор «перезапускався» з
    випадкової фази на кожному натисканні кнопки.

    Осі незалежні і навіть трохи різної частоти: рука не тремтить по колу.
    """

    def __init__(self, profile: OperatorProfile) -> None:
        amp = profile.current_tremor_amp
        spread = 0.045
        self._tremor = (
            _Resonator(profile.tremor_hz * random.uniform(1 - spread, 1 + spread), amp, zeta=0.18),
            _Resonator(profile.tremor_hz * random.uniform(1 - spread, 1 + spread), amp, zeta=0.18),
        )
        self._drift = (
            _Resonator(profile.drift_hz * random.uniform(0.85, 1.15), profile.drift_amp, zeta=0.45),
            _Resonator(profile.drift_hz * random.uniform(0.85, 1.15), profile.drift_amp, zeta=0.45),
        )
        # Незалежне блукання по осях, щоб шум не був "діагональним".
        self._ou_x = 0.0
        self._ou_y = 0.0
        self._ou_sigma = amp * 0.8
        self._ou_tau = 0.09
        self._last_t: Optional[float] = None

    def begin(self) -> None:
        """Новий шматок відносного часу (рух або пауза) — без скидання стану."""
        self._last_t = None

    def at(self, t: float, scale: float = 1.0) -> Tuple[float, float]:
        # Перший семпл шматка: невідомо, скільки минуло від попереднього —
        # робимо один типовий крок, щоб фаза «жила» далі, а не завмирала.
        dt = 0.008 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t

        # Орнштейн-Уленбек: повертається до нуля, тому курсор не "спливає".
        decay = math.exp(-dt / self._ou_tau) if dt else 1.0
        kick = self._ou_sigma * math.sqrt(max(0.0, 1.0 - decay * decay))
        self._ou_x = self._ou_x * decay + random.gauss(0.0, kick)
        self._ou_y = self._ou_y * decay + random.gauss(0.0, kick)

        nx = self._tremor[0].step(dt) + self._drift[0].step(dt) + self._ou_x
        ny = self._tremor[1].step(dt) + self._drift[1].step(dt) + self._ou_y
        return nx * scale, ny * scale


_hand_noises: dict = {}


def _hand_noise(profile: OperatorProfile) -> _HandNoise:
    """Сесійний шум для профілю. Створюється раз і живе, поки живе профіль."""
    noise = _hand_noises.get(id(profile))
    if noise is None:
        noise = _hand_noises[id(profile)] = _HandNoise(profile)
    noise.begin()
    return noise


def generate_path(
    start: Tuple[float, float],
    target: Tuple[float, float],
    target_radius: Optional[float] = None,
    profile: Optional[OperatorProfile] = None,
) -> List[Sample]:
    """Будує траєкторію від `start` до околиці `target`.

    Курсор навмисно приходить **не рівно** в `target`, а у випадкову точку
    в межах `target_radius`: людина не влучає двічі в один і той самий піксель,
    а ідеальне влучання в центр — одна з найпростіших ознак автоматики.

    Args:
        start: Початкові координати (x, y).
        target: Центр цілі (x, y).
        target_radius: Радіус цілі в пікселях — впливає і на розкид влучання,
            і (за законом Фітца) на тривалість руху. У дрібну ціль курсор
            їде довше.
        profile: Профіль оператора; за замовчуванням — профіль сесії.

    Returns:
        List[Sample]: Семпли з часом від початку руху. Порожній список, якщо
        рухатись нікуди.
    """
    profile = profile or get_profile()
    target_radius = float(target_radius if target_radius is not None else _DEFAULT_TARGET_RADIUS)
    target_radius = max(1.0, target_radius)

    bias_x, bias_y = profile.aim_bias
    aim = (target[0] + bias_x, target[1] + bias_y)

    if not all(math.isfinite(v) for v in (start[0], start[1], target[0], target[1])):
        raise ValueError(f"Нечислові координати: start={start}, target={target}")

    # Порівнюємо зі справжньою ціллю: зсунута точка прицілювання може бути за
    # кілька пікселів звідси, і тоді курсор відмовився б рухатись, стоячи
    # повз ціль.
    if math.hypot(target[0] - start[0], target[1] - start[1]) < 0.5:
        return []

    submovements = _plan_submovements(start, aim, target_radius, profile, guarantee=target)
    noise = _hand_noise(profile)

    dt = profile.polling_interval
    jitter = profile.polling_jitter * dt

    # Субрухи НАКЛАДАЮТЬСЯ, а не йдуть один за одним. Якщо їх ставити
    # послідовно, кожен починається і закінчується з нульовою швидкістю, і
    # курсор завмирає посеред наведення, а потім стрибає в новий бік — рух
    # виходить кутастим, як у механізму. Жива рука починає довідний рух ще
    # до того, як завершився кидок, і профілі швидкості складаються.
    onsets: List[float] = []
    clock = 0.0
    for index, sub in enumerate(submovements):
        onsets.append(clock)
        if index == len(submovements) - 1:
            break
        if random.random() < 0.06:
            # Зрідка людина таки зупиняється і дивиться, куди влучила.
            clock += sub.duration + random.lognormvariate(0.0, 0.4) * 0.18
        else:
            clock += sub.duration * random.uniform(*_SUBMOVEMENT_OVERLAP)

    total = max(onset + sub.duration for onset, sub in zip(onsets, submovements))

    # Стеля швидкості. Закон Фітца сам по собі нічим не обмежений: на довгій
    # дистанції в дрібну ціль він може видати рух, швидший за будь-яку живу
    # руку. Якщо так — розтягуємо весь рух у часі, зберігаючи форму.
    excess = _peak_speed(submovements, onsets, total) / max(1.0, profile.max_velocity)
    if excess > 1.0:
        onsets = [onset * excess for onset in onsets]
        submovements = [sub._replace(duration=sub.duration * excess) for sub in submovements]
        total *= excess

    # Геометрія кожного субруху рахується один раз: зміщення і вектор упоперек,
    # уздовж якого кладеться вигин дуги.
    geometry = []
    for sub in submovements:
        dx, dy = sub.x1 - sub.x0, sub.y1 - sub.y0
        length = math.hypot(dx, dy)
        if length > 1e-6:
            perp = (-dy / length, dx / length)
        else:
            perp = (0.0, 0.0)
        geometry.append((dx, dy, perp, sub.curvature * length))

    origin_x, origin_y = submovements[0].x0, submovements[0].y0

    # Кількість подій задає частота опитування миші, а не "гладкість": саме
    # тому швидкий рух дає рідкі великі стрибки координат, а повільний —
    # щільні дрібні. Бот, що шле фіксовану кількість кроків на рух, має сталу
    # швидкість у подіях і цим вирізняється.
    steps = max(2, int(round(total / dt)))
    samples: List[Sample] = []

    for step in range(1, steps + 1):
        progress = step / steps
        # Час події зі своїм джитером — кварц у миші не ідеальний.
        t = total * progress + (random.gauss(0.0, jitter) if step < steps else 0.0)
        t = max(0.0, t)

        x, y = origin_x, origin_y
        for sub, onset, (dx, dy, perp, bow) in zip(submovements, onsets, geometry):
            tau = (total * progress - onset) / sub.duration
            if tau <= 0.0:
                continue
            tau = min(1.0, tau)

            u = _min_jerk(tau ** sub.skew)
            x += dx * u
            y += dy * u

            # Дуга: максимум відхилення посередині субруху, нуль на кінцях.
            arc = bow * math.sin(math.pi * tau)
            x += perp[0] * arc
            y += perp[1] * arc

        nx, ny = noise.at(t)
        samples.append(Sample(t, x + nx, y + ny))

    # Остання точка лишається «живою» — з тремором, як усі інші: нульовий
    # залишок шуму рівно на фінальному семплі кожного руху сам по собі
    # помітний. Але вона не має права винести влучання за межі цілі, тому
    # якщо шум вивів курсор назовні, підтягуємо його назад, зберігаючи
    # напрямок відхилення.
    final = samples[-1]
    error_x, error_y = final.x - target[0], final.y - target[1]
    error = math.hypot(error_x, error_y)
    limit = target_radius * 0.85
    if error > limit:
        scale = limit / error
        samples[-1] = Sample(final.t, target[0] + error_x * scale, target[1] + error_y * scale)

    return samples


def generate_idle_path(
    anchor: Tuple[float, float],
    duration: float,
    profile: Optional[OperatorProfile] = None,
) -> List[Sample]:
    """Мікрорухи курсора під рукою, що лежить на миші й нікуди не веде.

    Абсолютно нерухомий курсор — окрема ознака, і найпомітніша вона саме
    там, де рука точно на миші: між натисканням і відпусканням кнопки.
    Жива рука в цей момент тремтить, тому реальний клік майже завжди має
    один-два мікрозсуви між `down` і `up`, а довге утримання — більше.

    Амплітуда навмисно менша за рухому: більшість семплів округлюється до
    нуля пікселів і жодної події не породжує. На виході виходить не потік,
    а рідкі поодинокі зсуви на піксель — саме те, що дає рука в спокої.

    Args:
        anchor: Точка, навколо якої тремтить курсор.
        duration: Тривалість у секундах.
        profile: Профіль оператора; за замовчуванням — профіль сесії.
    """
    profile = profile or get_profile()
    dt = profile.polling_interval

    steps = int(duration / dt)
    if steps < 1:
        return []

    scale = _IDLE_NOISE_SCALE * random.lognormvariate(0.0, _IDLE_NOISE_SPREAD)
    noise = _hand_noise(profile)
    samples: List[Sample] = []

    # Шум — відхилення від якоря, тож центруємо його на першому семплі:
    # сесійний осцилятор у цю мить стоїть у довільній фазі, і без цього курсор
    # стрибнув би на її значення в перший же такт паузи.
    base_x, base_y = noise.at(0.0, scale)
    for step in range(1, steps + 1):
        t = step * dt
        nx, ny = noise.at(t, scale)
        samples.append(Sample(t, anchor[0] + nx - base_x, anchor[1] + ny - base_y))

    return samples


def _resample(draw, low: float, high: float, attempts: int = 20) -> float:
    """Тягне значення з `draw`, поки воно не влучить у [low, high].

    Обрізання замість перевибірки лишає на межі АТОМ — скупчення однакових
    значень рівно на low чи high. Заміряно на попередній версії: 3.8% усіх
    утримань кліку дорівнювали рівно 25.000 мс. Однакове з точністю до
    мікросекунди значення, повторене сотні разів, — готова ознака.
    """
    for _ in range(attempts):
        value = draw()
        if low <= value <= high:
            return value
    # Виродженим параметрам (наприклад, медіана поза діапазоном) віддаємо
    # випадкову точку всередині, а не межу.
    return random.uniform(low, high)


def reaction_delay(profile: Optional[OperatorProfile] = None) -> float:
    """Пауза "побачив ціль -> почав рухатись", секунди."""
    profile = profile or get_profile()
    median = profile.reaction_median * (1.0 + 0.25 * profile.fatigue)
    return _resample(lambda: random.lognormvariate(0.0, 0.34) * median, 0.06, 1.2)


def click_hold_duration(profile: Optional[OperatorProfile] = None) -> float:
    """Тривалість утримання кнопки в кліку, секунди.

    Логнормальний розподіл: щільний пік близько 70-90 мс і довгий хвіст.
    Рівномірний `uniform(0.04, 0.12)`, який зазвичай ставлять у ботах, дає
    на гістограмі рівне плато — форму, якої в людини не буває.
    """
    profile = profile or get_profile()
    return _resample(
        lambda: random.lognormvariate(0.0, profile.click_hold_sigma) * profile.click_hold_median,
        _MIN_CLICK_HOLD,
        0.60,
    )
