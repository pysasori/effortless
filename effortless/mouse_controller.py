import time
import pyautogui
from typing import Optional
from .utils.random_delay import random_delay

pyautogui.FAILSAFE = False


class MouseController:
    """Клас для керування мишею з зручним інтерфейсом."""

    @staticmethod
    def move(x: Optional[int] = None, y: Optional[int] = None, t: float = 0.5) -> None:
        """Переміщує курсор у вказану точку із заданою затримкою.

        Args:
            x (Optional[int]): Координата X. Якщо None, використовується поточна позиція.
            y (Optional[int]): Координата Y. Якщо None, використовується поточна позиція.
            t (float): Час переміщення курсора (у секундах).
        """
        current_x, current_y = pyautogui.position()
        pyautogui.moveTo(x if x is not None else current_x, y if y is not None else current_y, duration=t)
        random_delay()

    @staticmethod
    def move_from_point(x: Optional[int] = None, y: Optional[int] = None, t: float = 0.5) -> None:
        """Переміщує курсор відносно поточної позиції.

        Args:
            x (Optional[int]): Зміщення по осі X. Якщо None, зміщення не відбувається.
            y (Optional[int]): Зміщення по осі Y. Якщо None, зміщення не відбувається.
            t (float): Час переміщення курсора (у секундах).
        """
        current_x, current_y = pyautogui.position()
        pyautogui.moveTo(current_x + (x or 0), current_y + (y or 0), duration=t)
        random_delay()

    @staticmethod
    def move_and_click(x: Optional[int] = None, y: Optional[int] = None, t: float = 0.2) -> None:
        """Переміщує курсор і виконує клік.

        Args:
            x (Optional[int]): Координата X. Якщо None, використовується поточна позиція.
            y (Optional[int]): Координата Y. Якщо None, використовується поточна позиція.
            t (float): Час переміщення курсора (у секундах).
        """
        MouseController.move(x, y, t)
        pyautogui.click()
        random_delay()

    @staticmethod
    def drag(x: int, y: int, t: float = 0.5, button: str = 'left') -> None:
        """Перетягує курсор миші.

        Args:
            x (int): Координата X для перетягування.
            y (int): Координата Y для перетягування.
            t (float): Час перетягування (у секундах).
            button (str): Кнопка миші для перетягування ('left' або 'right').
        """
        pyautogui.dragTo(x, y, duration=t, button=button)
        random_delay()

    @staticmethod
    def click(x: Optional[int] = None, y: Optional[int] = None) -> None:
        """Виконує клік за вказаними координатами або поточною позицією.

        Args:
            x (Optional[int]): Координата X. Якщо None, використовується поточна позиція.
            y (Optional[int]): Координата Y. Якщо None, використовується поточна позиція.
        """
        random_delay()
        pyautogui.click(x, y)
        random_delay()

    @staticmethod
    def long_click(t: float = 0.2) -> None:
        """Довге натискання лівої кнопки миші на поточній позиції.

        Args:
            t (float): Час утримання кнопки миші (у секундах).
        """
        x, y = pyautogui.position()
        pyautogui.mouseDown()
        time.sleep(t)
        pyautogui.mouseUp()
        random_delay()

    @staticmethod
    def scroll(px: int) -> None:
        """Імітує прокрутку екрану через рух миші.

        Args:
            px (int): Кількість пікселів для прокрутки.
        """
        pyautogui.mouseDown(button='right')
        pyautogui.move(0, -px, duration=1)
        pyautogui.mouseUp(button='right')
        pyautogui.move(0, px, duration=1)
        random_delay()