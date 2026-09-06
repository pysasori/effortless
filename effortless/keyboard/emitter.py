"""
Відтворення розкладу: з подій у відносному часі — реальні натискання.

Тут робиться дві речі, яких немає в наївному емуляторі.

**Укладання на сітку опитування.** Клавіатура не повідомляє про натискання в
момент натискання: вона опитується з фіксованою частотою (типово 125 Гц) і
шле HID-звіт зі станом усіх клавіш. Тому реальні часові мітки подій лягають
на сітку з кроком 8 мс, а всі зміни, що потрапили в один період опитування,
приходять **з однією міткою**. Значить, і Shift+літера, натиснуті «одночасно»,
на залізі мають рівно однаковий час, а не різницю в 40 мкс.

Наслідок, який видно на будь-якому логері: у живому потоці інтервали між
подіями кратні періоду опитування з мікроджитером, а у згенерованого —
довільні, ще й з піками на 15.6 мс, якщо всередині був `time.sleep`. Про
другу частину — див. `mouse/timing.py`.

**Стан замість подій.** Транспорту передається повний набір натиснутих
клавіш, а не окремі «натиснув»/«відпустив». Розсинхронізуватись у такій
моделі неможливо, і клавіша не лишиться затиснутою після винятку — на
відміну від коду, який шле `keyDown` і не доходить до `keyUp`.

Годинник спільний з мишею за конструкцією, але не за екземпляром: це два
різні пристрої з двома різними кварцами, тому й сітки в них незалежні.
"""
import time
from typing import List, Optional, Sequence, Set

from ..mouse.clock import PollingClock
from ..mouse.timing import enable_high_resolution_timer, sleep_until
from .profile import TypistProfile, get_profile
from .rhythm import KeyEvent
from .transport import get_transport

__all__ = ["play", "get_clock", "reset_clock", "pressed_keys", "release_all"]

_clock: Optional[PollingClock] = None
_clock_profile: Optional[TypistProfile] = None

# Поточний стан клавіш — єдине джерело правди для транспорту.
_pressed: Set[int] = set()


def get_clock(profile: Optional[TypistProfile] = None) -> PollingClock:
    """Годинник опитування клавіатури. Пересоздається при зміні профілю."""
    global _clock, _clock_profile

    profile = profile or get_profile()
    if _clock is None or _clock_profile is not profile:
        _clock = PollingClock(profile.polling_interval, profile.polling_jitter)
        _clock_profile = profile
    return _clock


def reset_clock() -> None:
    """Скидає годинник — знадобиться в тестах."""
    global _clock, _clock_profile
    _clock = _clock_profile = None


def pressed_keys() -> Set[int]:
    """Копія поточного стану — які клавіші зараз натиснуті."""
    return set(_pressed)


def _apply(pressed: Set[int]) -> None:
    get_transport().set_keys(frozenset(pressed))


def release_all() -> None:
    """Відпускає все, що лишилось натиснутим.

    Викликається з `finally`: якщо виняток стався між натисканням Shift і
    його відпусканням, затиснутий Shift дістанеться користувачеві, і це
    помітно значно сильніше за будь-яку статистику.
    """
    # Без умови `if _pressed`. Виняток може прилетіти в `sleep_until` усередині
    # `flush` — після того, як стан уже спорожнено, але ДО того, як його
    # передали транспорту. Тоді наш список порожній, а система тримає клавішу.
    # Заміряно: 3 із 16 перериваннь лишали клавішу фізично натиснутою, і
    # `release_all` з умовою це «не бачив». Транспорт рахує різницю сам, тож
    # зайвий порожній стан нічого не коштує.
    _pressed.clear()
    _apply(_pressed)


def play(events: Sequence[KeyEvent], profile: Optional[TypistProfile] = None,
         persist: bool = False) -> None:
    """Відтворює розклад у реальному часі.

    Події, що потрапили в один період опитування, застосовуються разом і
    їдуть одним викликом транспорту — так само, як вони приїхали б одним
    HID-звітом від фізичної клавіатури.

    Args:
        events: Розклад із `rhythm.build_schedule`, відсортований за часом.
        profile: Почерк; за замовчуванням — профіль сесії.
        persist: Лишити клавіші натиснутими після завершення розкладу. Для
            `key_down`, де відпускання робить окремий виклик. При винятку
            все одно відпускається все — затиснута клавіша гірша за виняток.
    """
    if not events:
        return

    profile = profile or get_profile()
    enable_high_resolution_timer()
    clock = get_clock(profile)

    origin = time.perf_counter()
    batch: List[KeyEvent] = []
    batch_tick: Optional[int] = None

    def flush() -> None:
        if batch_tick is None:
            return
        # Джитер такту розігрується один раз на такт, а не на подію: у
        # фізичної клавіатури зсув періоду опитування спільний для всього
        # звіту, бо звіт один.
        #
        # Спати — ДО зміни стану. Якщо виняток прилетить під час сну, наш
        # список і те, що бачить система, лишаться узгодженими, і аварійне
        # відпускання знатиме, що відпускати.
        sleep_until(clock.time_of(batch_tick))

        # Спершу відпускання: інакше в стані промайне мить, коли натиснуті
        # і стара, і нова клавіші, хоча в цей такт цього не було.
        for event in sorted(batch, key=lambda e: e.down):
            if event.down:
                _pressed.add(event.scancode)
            else:
                _pressed.discard(event.scancode)
        _apply(_pressed)

    try:
        for event in events:
            tick = clock.index_at(origin + event.t)
            if batch_tick is not None:
                # Відпускання й повторне натискання ТІЄЇ САМОЇ клавіші в
                # одному такті злипаються в «досі натиснута», і друге
                # натискання зникає. Розклад цього не допускає (`_separate`),
                # але це остання лінія: такий down переноситься на наступний
                # такт — рівно так, як його побачила б і фізична клавіатура.
                if event.down and any(e.scancode == event.scancode and not e.down for e in batch):
                    tick = max(tick, batch_tick + 1)
                if tick != batch_tick:
                    flush()
                    batch = []
            batch_tick = tick
            batch.append(event)
        flush()
    except BaseException:
        # Клавіші, які лишились натиснутими через виняток, відпускаються тут
        # же: затиснутий Shift, що дістався користувачеві, помітніший за
        # будь-яку статистику.
        release_all()
        raise
    else:
        if not persist:
            # Відпускаємо лише те, що натискав ЦЕЙ розклад. `release_all`
            # тут скидав би й клавіші, затиснуті через `key_down` навмисно
            # (наприклад, Ctrl на час серії натискань).
            own = {event.scancode for event in events}
            leftover = _pressed & own
            if leftover:
                _pressed.difference_update(leftover)
                _apply(_pressed)

    profile.keys_done += sum(1 for event in events if event.down)
