"""
Керування мишею з людяними траєкторіями руху (WindMouse) + низькорівневі кліки через WinAPI.

Приклад:
    import effortless.mouse as mouse

    mouse.move(x=100, y=200)
    mouse.click(x=150, y=250)
"""
import time
from typing import Optional

import numpy as np
import pyautogui

from ..utils.random_delay import random_delay
from .wind_mouse import wind_mouse
from . import win_api

pyautogui.FAILSAFE = False

__all__ = [
    "move",
    "move_from_point",
    "move_and_click",
    "drag",
    "click",
    "long_click",
    "scroll",
]


def _move_wind(x: int, y: int) -> None:
    start_x, start_y = win_api.get_cursor_pos()

    def callback(mx, my):
        win_api.send_mouse_move(mx, my)
        time.sleep(np.random.uniform(0.006, 0.012))

    wind_mouse(start_x, start_y, x, y, move_mouse=callback)


def move(x: Optional[int] = None, y: Optional[int] = None) -> None:
    """Переміщує курсор в абсолютні координати (x, y)."""
    current_x, current_y = win_api.get_cursor_pos()
    target_x = x if x is not None else current_x
    target_y = y if y is not None else current_y

    _move_wind(target_x, target_y)
    random_delay()


def move_from_point(x: Optional[int] = None, y: Optional[int] = None) -> None:
    """Переміщує курсор відносно поточної позиції на (x, y)."""
    current_x, current_y = win_api.get_cursor_pos()
    target_x = current_x + (x or 0)
    target_y = current_y + (y or 0)

    _move_wind(target_x, target_y)
    random_delay()


def move_and_click(x: Optional[int] = None, y: Optional[int] = None) -> None:
    """Переміщує курсор та клікає лівою кнопкою."""
    move(x, y)
    random_delay()
    click()


def drag(x: int, y: int, button: str = 'left') -> None:
    """Затискає кнопку, переміщує курсор в (x, y) та відпускає."""
    down = win_api.MOUSEEVENTF_LEFTDOWN if button == "left" else win_api.MOUSEEVENTF_RIGHTDOWN
    up = win_api.MOUSEEVENTF_LEFTUP if button == "left" else win_api.MOUSEEVENTF_RIGHTUP

    win_api.send_mouse_event(down)
    _move_wind(x, y)
    win_api.send_mouse_event(up)

    random_delay()


def click(x: Optional[int] = None, y: Optional[int] = None) -> None:
    """Клікає лівою кнопкою миші, за бажанням попередньо переміщуючись у (x, y)."""
    if x is not None or y is not None:
        move(x, y)

    random_delay()
    win_api.send_mouse_event(win_api.MOUSEEVENTF_LEFTDOWN)
    win_api.send_mouse_event(win_api.MOUSEEVENTF_LEFTUP)
    random_delay()


def long_click(t: float = 0.2) -> None:
    """Затискає ліву кнопку миші на t секунд."""
    win_api.send_mouse_event(win_api.MOUSEEVENTF_LEFTDOWN)
    time.sleep(t)
    win_api.send_mouse_event(win_api.MOUSEEVENTF_LEFTUP)
    random_delay()


def scroll(px: int) -> None:
    """Прокручує на px пікселів (утримуючи праву кнопку, як у грі)."""
    pyautogui.mouseDown(button='right')
    pyautogui.move(0, -px, duration=1)
    pyautogui.mouseUp(button='right')
    pyautogui.move(0, px, duration=1)
    random_delay()
