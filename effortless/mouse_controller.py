import time
import numpy as np
import ctypes
import pyautogui
from typing import Optional
from .utils.random_delay import random_delay

pyautogui.FAILSAFE = False


# =========================
# WIND MOUSE (1:1)
# =========================

sqrt3 = np.sqrt(3)
sqrt5 = np.sqrt(5)


def wind_mouse(
    start_x,
    start_y,
    dest_x,
    dest_y,
    G_0=9,
    W_0=3,
    M_0=15,
    D_0=12,
    move_mouse=lambda x, y: None,
):
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


# =========================
# WINAPI
# =========================

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("mi", MOUSEINPUT),
    ]


def _get_cursor_pos():
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _send_mouse_move(x, y):
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)

    abs_x = int(x * 65535 / (screen_w - 1))
    abs_y = int(y * 65535 / (screen_h - 1))

    mi = MOUSEINPUT(
        dx=abs_x,
        dy=abs_y,
        mouseData=0,
        dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
        time=0,
        dwExtraInfo=None,
    )

    inp = INPUT(type=INPUT_MOUSE, mi=mi)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _mouse_event(flag):
    inp = INPUT(
        type=INPUT_MOUSE,
        mi=MOUSEINPUT(0, 0, 0, flag, 0, None),
    )
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# =========================
# CONTROLLER (DROP-IN)
# =========================

class MouseController:

    @staticmethod
    def _move_wind(x, y):
        start_x, start_y = _get_cursor_pos()

        def callback(mx, my):
            _send_mouse_move(mx, my)
            time.sleep(np.random.uniform(0.006, 0.012))

        wind_mouse(start_x, start_y, x, y, move_mouse=callback)

    @staticmethod
    def move(x: Optional[int] = None, y: Optional[int] = None, t: float = 0.5) -> None:
        current_x, current_y = _get_cursor_pos()
        target_x = x if x is not None else current_x
        target_y = y if y is not None else current_y

        MouseController._move_wind(target_x, target_y)
        random_delay()

    @staticmethod
    def move_from_point(x: Optional[int] = None, y: Optional[int] = None, t: float = 0.5) -> None:
        current_x, current_y = _get_cursor_pos()
        target_x = current_x + (x or 0)
        target_y = current_y + (y or 0)

        MouseController._move_wind(target_x, target_y)
        random_delay()

    @staticmethod
    def move_and_click(x: Optional[int] = None, y: Optional[int] = None, t: float = 0.2) -> None:
        MouseController.move(x, y, t)
        MouseController.click()

    @staticmethod
    def drag(x: int, y: int, t: float = 0.5, button: str = 'left') -> None:
        down = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
        up = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP

        _mouse_event(down)
        MouseController._move_wind(x, y)
        _mouse_event(up)

        random_delay()

    @staticmethod
    def click(x: Optional[int] = None, y: Optional[int] = None) -> None:
        if x is not None or y is not None:
            MouseController.move(x, y)

        random_delay()
        _mouse_event(MOUSEEVENTF_LEFTDOWN)
        _mouse_event(MOUSEEVENTF_LEFTUP)
        random_delay()

    @staticmethod
    def long_click(t: float = 0.2) -> None:
        _mouse_event(MOUSEEVENTF_LEFTDOWN)
        time.sleep(t)
        _mouse_event(MOUSEEVENTF_LEFTUP)
        random_delay()

    @staticmethod
    def scroll(px: int) -> None:
        # залишив як було (бо це специфічна логіка)
        pyautogui.mouseDown(button='right')
        pyautogui.move(0, -px, duration=1)
        pyautogui.mouseUp(button='right')
        pyautogui.move(0, px, duration=1)
        random_delay()