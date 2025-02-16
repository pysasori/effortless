import random
import time
import math


def generate_random_delay(min_delay: float = 0.05, max_delay: float = 0.2) -> float:
    """
    Генерує випадкову затримку у межах [min_delay, max_delay].

    Args:
        min_delay (float): Мінімальна затримка у секундах.
        max_delay (float): Максимальна затримка у секундах.

    Returns:
        float: Випадкове значення затримки.

    Raises:
        ValueError: Якщо min_delay або max_delay не є кінцевими числами,
                   якщо затримка від'ємна, або якщо min_delay > max_delay.
    """
    if not all(map(math.isfinite, (min_delay, max_delay))):
        raise ValueError("min_delay та max_delay повинні бути кінцевими числами.")

    if min_delay < 0 or max_delay < 0:
        raise ValueError("Затримка не може бути від'ємною.")

    if min_delay > max_delay:
        raise ValueError("min_delay не може бути більшим за max_delay.")

    return random.uniform(min_delay, max_delay)


def random_delay(min_delay: float = 0.05, max_delay: float = 0.2) -> None:
    """
    Виконує випадкову затримку між min_delay та max_delay.

    Args:
        min_delay (float): Мінімальна затримка у секундах.
        max_delay (float): Максимальна затримка у секундах.

    Raises:
        ValueError: Якщо min_delay або max_delay не є кінцевими числами,
                   якщо затримка від'ємна, або якщо min_delay > max_delay.
    """
    delay = generate_random_delay(min_delay, max_delay)
    if delay > 0:
        time.sleep(delay)