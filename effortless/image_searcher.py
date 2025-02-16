import time
import pyautogui
import cv2
import numpy as np
import logging
from typing import Optional, Tuple, List, Union

# Налаштування логування
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class ImageSearcher:
    """Клас для пошуку зображення на екрані."""

    def __init__(self, threshold: float = 0.87, save_screens: bool = False) -> None:
        """
        Ініціалізація класу.

        Args:
            threshold (float): Поріг збігу для пошуку зображення (за замовчуванням 0.87).
            save_screens (bool): Чи зберігати скріншоти під час пошуку (за замовчуванням False).
        """
        self.threshold = threshold
        self.save_screens = save_screens

    def search_image(
        self,
        img: str,
        cords: Optional[List[int]] = None,
        search_time: Optional[float] = 15
    ) -> Union[bool, Tuple[int, int]]:
        """
        Шукає зображення на екрані.

        Args:
            img (str): Шлях до зображення, яке потрібно знайти.
            cords (Optional[List[int]]): Координати області пошуку [x1, y1, x2, y2]. Якщо None, пошук на всьому екрані.
            search_time (Optional[float]): Час пошуку в секундах. Якщо None, пошук відбувається один раз.

        Returns:
            Union[bool, Tuple[int, int]]: Координати знайденого зображення (x, y) або False, якщо зображення не знайдено.
        """
        img_gray = self._load_image(img)
        if img_gray is None:
            return False

        start_time = time.time()
        logging.info(f"Зображення {img} почали шукати")
        while True:
            screen_gray = self._take_screenshot(cords)
            result = self._find_image_on_screen(img_gray, screen_gray, cords)

            if result:
                if self.save_screens:
                    self._save_screenshot(screen_gray, 'logs_screen/search_on_screen_found.png')
                return result

            if search_time and time.time() - start_time > search_time:
                if self.save_screens:
                    self._save_screenshot(screen_gray, 'logs_screen/search_on_screen_errors.png')
                logging.info(f"Зображення {img} не знайдено за {search_time} секунд.")
                return False

            time.sleep(0.5)

    def checking_image(
        self,
        img: str,
        cords: Optional[List[int]] = None
    ) -> Union[bool, Tuple[int, int]]:
        """
        Шукає зображення на екрані один раз (без очікування).

        Args:
            img (str): Шлях до зображення, яке потрібно знайти.
            cords (Optional[List[int]]): Координати області пошуку [x1, y1, x2, y2]. Якщо None, пошук на всьому екрані.

        Returns:
            Union[bool, Tuple[int, int]]: Координати знайденого зображення (x, y) або False, якщо зображення не знайдено.
        """
        return self.search_image(img, cords, search_time=0)

    def _load_image(self, img: str) -> Optional[np.ndarray]:
        """
        Завантажує зображення та конвертує його у відтінки сірого.

        Args:
            img (str): Шлях до зображення.

        Returns:
            Optional[np.ndarray]: Зображення у відтінках сірого або None, якщо зображення не знайдено.
        """
        img_rgb = cv2.imread(img)
        if img_rgb is None:
            logging.error(f"Зображення {img} не знайдено.")
            return None
        return cv2.cvtColor(img_rgb, cv2.COLOR_BGR2GRAY)

    def _take_screenshot(self, cords: Optional[List[int]] = None) -> np.ndarray:
        """
        Робить скріншот вказаної області або всього екрану.

        Args:
            cords (Optional[List[int]]): Координати області [x1, y1, x2, y2]. Якщо None, робить скріншот усього екрану.

        Returns:
            np.ndarray: Скріншот у вигляді масиву NumPy.
        """
        if cords:
            screen = pyautogui.screenshot(region=(int(cords[0]), int(cords[1]), int(cords[2]), int(cords[3])))
        else:
            screen = pyautogui.screenshot()
        return cv2.cvtColor(np.array(screen), cv2.COLOR_RGB2GRAY)

    def _find_image_on_screen(
        self,
        img_gray: np.ndarray,
        screen_gray: np.ndarray,
        cords: Optional[List[int]] = None
    ) -> Optional[Tuple[int, int]]:
        """
        Шукає зображення на скріншоті.

        Args:
            img_gray (np.ndarray): Зображення, яке потрібно знайти (у відтінках сірого).
            screen_gray (np.ndarray): Скріншот екрану (у відтінках сірого).
            cords (Optional[List[int]]): Координати області пошуку [x1, y1, x2, y2].

        Returns:
            Optional[Tuple[int, int]]: Координати знайденого зображення (x, y) або None, якщо зображення не знайдено.
        """
        res = cv2.matchTemplate(screen_gray, img_gray, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= self.threshold)

        if len(loc[0]) > 0:
            x = loc[1][0] + cords[0] if cords else loc[1][0]
            y = loc[0][0] + cords[1] if cords else loc[0][0]
            logging.info(f"Зображення знайдено: координати: ({x}, {y})")
            return x, y
        return None

    def _save_screenshot(self, screen_gray: np.ndarray, path: str) -> None:
        """
        Зберігає скріншот на диск.

        Args:
            screen_gray (np.ndarray): Скріншот у вигляді масиву NumPy.
            path (str): Шлях для збереження скріншоту.
        """
        cv2.imwrite(path, screen_gray)
        logging.info(f"Скріншот збережено за шляхом: {path}")