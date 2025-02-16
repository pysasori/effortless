"""
Модуль для розпізнавання тексту з екрану за допомогою Tesseract OCR.

Цей модуль надає клас `TextExtractor`, який дозволяє:
- Робити скріншоти екрану або його частини.
- Обробляти зображення для покращення розпізнавання тексту.
- Розпізнавати текст за допомогою Tesseract OCR.
- Зберігати оброблені зображення та скріншоти (за бажанням).
"""
import os
import logging
import numpy as np
import cv2
import pyautogui
import pytesseract
from PIL import Image
from typing import Optional, List, Tuple

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TextExtractor:
    """Клас для захоплення та розпізнавання тексту/цифр з екрану."""

    def __init__(
        self,
        tesseract_cmd: str = r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        save_images: bool = False,
        save_images_path: str = 'logs_screen',
        save_screens: bool = False
    ) -> None:
        """Ініціалізація класу.

        Args:
            tesseract_cmd (str): Шлях до виконуваного файлу Tesseract.
            save_images (bool): Чи зберігати оброблені зображення.
            save_images_path (str): Шлях до папки для збереження зображень.
            save_screens (bool): Чи зберігати скріншоти.
        """
        self.tesseract_cmd = tesseract_cmd
        self.save_images = save_images
        self.save_images_path = save_images_path
        self.save_screens = save_screens
        pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd

    def _capture_screen(self, cords: Optional[List[int]] = None) -> Image.Image:
        """Робить скріншот екрану або його частини.

        Args:
            cords (Optional[List[int]]): Координати області скріншоту [x1, y1, x2, y2].
                                         Якщо None, робиться скріншот усього екрану.

        Returns:
            Image.Image: Зображення у форматі PIL.Image.
        """
        if cords:
            screen = pyautogui.screenshot(region=(int(cords[0]), int(cords[1]), int(cords[2]), int(cords[3])))
        else:
            screen = pyautogui.screenshot()

        return screen

    def _process_image(
        self,
        image: Image.Image,
        resize_scale_x: float,
        resize_scale_y: float,
        clahe_clip_limit: float,
        clahe_tile_grid_size: Tuple[int, int]
    ) -> np.ndarray:
        """Обробляє зображення для покращення розпізнавання тексту.

        Args:
            image (Image.Image): Зображення у форматі PIL.Image.
            resize_scale_x (float): Використовується для зміни роздільної здатності.
            resize_scale_y (float): Використовується для зміни роздільної здатності.
            clahe_clip_limit (float): Параметр CLAHE для покращення контрасту.
            clahe_tile_grid_size (Tuple[int, int]): Розмір сітки для CLAHE.

        Returns:
            np.ndarray: Оброблене зображення у форматі NumPy array.
        """
        image = np.array(image)
        image = cv2.resize(image, None, fx=resize_scale_x, fy=resize_scale_y, interpolation=cv2.INTER_CUBIC)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=clahe_clip_limit, tileGridSize=clahe_tile_grid_size)
        return clahe.apply(image)

    def _save_image(self, image: np.ndarray, filename: str) -> None:
        """Зберігає зображення на диск, якщо включено збереження.

        Args:
            image (np.ndarray): Зображення у форматі NumPy array.
            filename (str): Ім'я файлу для збереження.
        """
        if self.save_images:
            os.makedirs(self.save_images_path, exist_ok=True)
            path = os.path.join(self.save_images_path, filename)
            cv2.imwrite(path, image)
            logger.info(f"Зображення збережено за шляхом: {path}")

    def extract_text(
        self,
        cords: Optional[List[int]] = None,
        resize_scale_x: float = 2.2,
        resize_scale_y: float = 2.2,
        clahe_clip_limit: float = 1.3,
        clahe_tile_grid_size: Tuple[int, int] = (2, 2),
        tesseract_config: str = '--psm 12 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789',
        image_filename: str = 'processed_image.png'
    ) -> str:
        """Основний метод для розпізнавання тексту з екрану.

        Args:
            cords (Optional[List[int]]): Координати області скріншоту [x1, y1, x2, y2].
            resize_scale_x (float): Використовується для зміни роздільної здатності.
            resize_scale_y (float): Використовується для зміни роздільної здатності.
            clahe_clip_limit (float): Параметр CLAHE для покращення контрасту.
            clahe_tile_grid_size (Tuple[int, int]): Розмір сітки для CLAHE.
            tesseract_config (str): Конфігурація Tesseract.
            image_filename (str): Ім'я файлу для збереження обробленого зображення.

        Returns:
            str: Розпізнаний текст.
        """
        try:
            # Робимо скріншот
            screen = self._capture_screen(cords)
            # Обробляємо зображення
            processed_image = self._process_image(screen, resize_scale_x, resize_scale_y, clahe_clip_limit, clahe_tile_grid_size)
            # Зберігаємо зображення (якщо включено)
            self._save_image(processed_image, image_filename)
            # Розпізнаємо текст
            return pytesseract.image_to_string(processed_image, config=tesseract_config)
        except Exception as e:
            logger.error(f"Помилка при розпізнаванні тексту: {e}")
            raise

    def read_text(self, cords: Optional[List[int]] = None) -> str:
        """Розпізнає текст з екрану з налаштуваннями за замовчуванням.

        Args:
            cords (Optional[List[int]]): Координати області скріншоту.

        Returns:
            str: Розпізнаний текст.
        """
        return self.extract_text(
            cords=cords,
            tesseract_config='--psm 12 --oem 3 -c tessedit_char_whitelist=KkMm0123456789',
        )

    def scan_prices(self, cords: Optional[List[int]] = None) -> str:
        """Розпізнає текст з екрану з налаштуваннями для сканування цін.

        Args:
            cords (Optional[List[int]]): Координати області скріншоту.

        Returns:
            str: Розпізнане число.
        """
        return self.extract_text(
            cords=cords,
            tesseract_config='--psm 6 --oem 3 -c tessedit_char_whitelist=0123456789,',
        )