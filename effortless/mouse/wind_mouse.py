"""
Алгоритм WindMouse — генерація "людяної" траєкторії руху миші від точки до точки.
"""
from typing import Callable, Tuple

import numpy as np

sqrt3 = np.sqrt(3)
sqrt5 = np.sqrt(5)


def wind_mouse(
    start_x: float,
    start_y: float,
    dest_x: float,
    dest_y: float,
    G_0: float = 9,
    W_0: float = 3,
    M_0: float = 15,
    D_0: float = 12,
    move_mouse: Callable[[int, int], None] = lambda x, y: None,
) -> Tuple[int, int]:
    """Проводить курсор від (start_x, start_y) до (dest_x, dest_y) за алгоритмом WindMouse.

    На кожному проміжному кроці викликає `move_mouse(x, y)`.

    Returns:
        Tuple[int, int]: Фінальні координати курсора.
    """
    current_x, current_y = start_x, start_y
    v_x = v_y = W_x = W_y = 0

    while (dist := np.hypot(dest_x - start_x, dest_y - start_y)) >= 1:
        W_mag = min(W_0, dist)

        if dist >= D_0:
            W_x = W_x / sqrt3 + (2 * np.random.random() - 1) * W_mag / sqrt5
            W_y = W_y / sqrt3 + (2 * np.random.random() - 1) * W_mag / sqrt5
        else:
            W_x /= sqrt3
            W_y /= sqrt3
            if M_0 < 3:
                M_0 = np.random.random() * 3 + 3
            else:
                M_0 /= sqrt5

        v_x += W_x + G_0 * (dest_x - start_x) / dist
        v_y += W_y + G_0 * (dest_y - start_y) / dist

        v_mag = np.hypot(v_x, v_y)
        if v_mag > M_0:
            v_clip = M_0 / 2 + np.random.random() * M_0 / 2
            v_x = (v_x / v_mag) * v_clip
            v_y = (v_y / v_mag) * v_clip

        start_x += v_x
        start_y += v_y

        move_x = int(np.round(start_x))
        move_y = int(np.round(start_y))

        if current_x != move_x or current_y != move_y:
            move_mouse(current_x := move_x, current_y := move_y)

    return current_x, current_y
