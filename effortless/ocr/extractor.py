"""
Модуль для розпізнавання тексту з екрану за допомогою RapidOCR.

Цей модуль надає клас `TextExtractor`, який дозволяє:
- Робити скріншоти екрану або його частини.
- Розпізнавати текст у вже відомій/обрізаній області (`extract_text`, `read_text`,
  `scan_prices`) — швидкий rec-only сценарій, без детекції тексту.
- Шукати текст у невідомому місці на екрані (`find_text`, `wait_for_text`,
  `checking_text`) — сценарій із детекцією, за аналогією з `ImageSearcher`.
- Зберігати оброблені зображення та скріншоти (за бажанням).
"""
import os
import re
import time
import string
import logging
import numpy as np
import cv2
from PIL import Image
import pyautogui
from rapidocr import RapidOCR
from typing import Optional, List, Tuple, Union

logger = logging.getLogger(__name__)


class TextExtractor:
    """Клас для захоплення та розпізнавання тексту/цифр з екрану."""

    def __init__(
        self,
        save_images: bool = False,
        save_images_path: str = 'logs_screen',
        save_screens: bool = False,
        text_score: float = 0.5,
        ocr_params: Optional[dict] = None,
    ) -> None:
        """Ініціалізація класу.

        Args:
            save_images (bool): Чи зберігати оброблені зображення.
            save_images_path (str): Шлях до папки для збереження зображень.
            save_screens (bool): Чи зберігати скріншоти.
            text_score (float): Мінімальний поріг впевненості розпізнавання (0..1).
            ocr_params (Optional[dict]): Передається напряму в `RapidOCR(params=...)`.
                Дефолтна модель (PP-OCRv6) вже мультимовна і розпізнає латиницю/цифри
                без додаткових налаштувань. Цей параметр — для випадків, коли потрібна
                окрема мова/модель, наприклад:
                `ocr_params={"Rec.lang_type": "en", "Rec.ocr_version": "PP-OCRv5"}`.
                УВАГА: мовні моделі, відмінні від дефолтної PP-OCRv6, не входять у пакет
                і довантажуються з ModelScope (Китай) при першому створенні `TextExtractor` —
                це мережевий виклик, якого немає в поведінці за замовчуванням.
        """
        self.save_images = save_images
        self.save_images_path = save_images_path
        self.save_screens = save_screens
        self.text_score = text_score
        self._ocr = RapidOCR(params=ocr_params)

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
        enhance: bool,
        resize_scale_x: float,
        resize_scale_y: float,
        clahe_clip_limit: float,
        clahe_tile_grid_size: Tuple[int, int]
    ) -> np.ndarray:
        """Готує зображення для RapidOCR.

        RapidOCR — нейронна модель, а не класичний алгоритм Tesseract: власна
        нормалізація розміру вже вшита в rec-модель, тому апскейл/CLAHE тут за
        замовчуванням вимкнені (`enhance=False`) — вимірювання показали, що вони
        не покращують точність, лише додають CPU/пам'ять на кожен виклик. Вмикай
        `enhance=True`, якщо конкретний реальний скріншот (шумний фон, низький
        контраст) розпізнається гірше без підсилення.

        Args:
            image (Image.Image): Зображення у форматі PIL.Image.
            enhance (bool): Чи застосовувати апскейл + CLAHE (для складних скріншотів).
            resize_scale_x (float): Використовується для зміни роздільної здатності.
            resize_scale_y (float): Використовується для зміни роздільної здатності.
            clahe_clip_limit (float): Параметр CLAHE для покращення контрасту.
            clahe_tile_grid_size (Tuple[int, int]): Розмір сітки для CLAHE.

        Returns:
            np.ndarray: Зображення у форматі NumPy array (3-канальне, BGR).
        """
        image = np.array(image)
        if not enhance:
            return image

        image = cv2.resize(image, None, fx=resize_scale_x, fy=resize_scale_y, interpolation=cv2.INTER_CUBIC)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=clahe_clip_limit, tileGridSize=clahe_tile_grid_size)
        image = clahe.apply(image)
        # RapidOCR очікує 3-канальне зображення, тому повертаємо CLAHE-результат назад у BGR.
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

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

    @staticmethod
    def _filter_text(text: str, allowed_chars: Optional[str]) -> str:
        """Залишає в тексті лише дозволені символи.

        Args:
            text (str): Розпізнаний текст.
            allowed_chars (Optional[str]): Набір дозволених символів. Якщо None — без фільтрації.

        Returns:
            str: Відфільтрований текст.
        """
        if not allowed_chars:
            return text
        pattern = f"[^{re.escape(allowed_chars)}]"
        return re.sub(pattern, '', text)

    def _run_ocr(
        self,
        cords: Optional[List[int]],
        detect: bool,
        enhance: bool,
        resize_scale_x: float,
        resize_scale_y: float,
        clahe_clip_limit: float,
        clahe_tile_grid_size: Tuple[int, int],
        image_filename: str,
    ):
        """Спільний конвеєр: скріншот -> підготовка зображення -> виклик RapidOCR.

        Returns:
            RapidOCROutput: сирий результат RapidOCR (`.txts`, `.boxes`, `.scores`).
        """
        screen = self._capture_screen(cords)
        processed_image = self._process_image(screen, enhance, resize_scale_x, resize_scale_y, clahe_clip_limit, clahe_tile_grid_size)
        self._save_image(processed_image, image_filename)
        return self._ocr(processed_image, use_det=detect, use_cls=False, use_rec=True, text_score=self.text_score)

    def extract_text(
        self,
        cords: Optional[List[int]] = None,
        enhance: bool = False,
        resize_scale_x: float = 2.2,
        resize_scale_y: float = 2.2,
        clahe_clip_limit: float = 1.3,
        clahe_tile_grid_size: Tuple[int, int] = (2, 2),
        allowed_chars: Optional[str] = None,
        detect: bool = False,
        image_filename: str = 'processed_image.png'
    ) -> str:
        """Основний метод для розпізнавання тексту з екрану.

        Args:
            cords (Optional[List[int]]): Координати області скріншоту [x1, y1, x2, y2].
            enhance (bool): Чи застосовувати апскейл + CLAHE перед розпізнаванням.
                            За замовчуванням False — RapidOCR цього не потребує (див. `_process_image`).
            resize_scale_x (float): Використовується для зміни роздільної здатності (лише якщо enhance=True).
            resize_scale_y (float): Використовується для зміни роздільної здатності (лише якщо enhance=True).
            clahe_clip_limit (float): Параметр CLAHE для покращення контрасту (лише якщо enhance=True).
            clahe_tile_grid_size (Tuple[int, int]): Розмір сітки для CLAHE (лише якщо enhance=True).
            allowed_chars (Optional[str]): Якщо задано, з результату прибираються всі символи,
                                            яких немає в цьому наборі (заміна tesseract_config whitelist).
            detect (bool): Чи запускати повну детекцію тексту (пошук блоків/рядків на зображенні).
                           За замовчуванням False — область скріншоту (cords) вже є обрізаним
                           текстом, тому детекція зайва: без неї розпізнавання ~5x менше споживає
                           пам'яті і на порядок швидше. Вмикай True, якщо в області може бути
                           кілька рядків/блоків тексту в невідомих місцях.
            image_filename (str): Ім'я файлу для збереження обробленого зображення.

        Returns:
            str: Розпізнаний текст.
        """
        try:
            result = self._run_ocr(cords, detect, enhance, resize_scale_x, resize_scale_y, clahe_clip_limit, clahe_tile_grid_size, image_filename)
            text = " ".join(result.txts) if result and result.txts else ""
            return self._filter_text(text, allowed_chars)
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
            allowed_chars="KkMm0123456789",
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
            allowed_chars="0123456789,",
        )

    def read_digits(self, cords: Optional[List[int]] = None, allow_separators: bool = True) -> str:
        """Розпізнає лише цифри з відомої області екрану (без прив'язки до "ціни").

        Найсуворіший з готових пресетів: з результату прибирається геть усе, що не
        цифра (і, за бажанням, роздільник тисяч/десяткових). Корисно для дрібних
        одноцифрових/двоцифрових полів (наприклад, колонка "Amount"), де навіть
        випадковий сторонній символ (іконка, розпізнана як ієрогліф) зіпсує парсинг.

        Args:
            cords (Optional[List[int]]): Координати області скріншоту.
            allow_separators (bool): Чи залишати кому/крапку (роздільники тисяч/десяткових).

        Returns:
            str: Рядок, що містить лише цифри (і роздільники, якщо allow_separators=True).
        """
        return self.extract_text(
            cords=cords,
            allowed_chars="0123456789,." if allow_separators else "0123456789",
        )

    def read_english(self, cords: Optional[List[int]] = None) -> str:
        """Розпізнає текст з відомої області екрану, залишаючи лише латиницю, цифри
        та базову пунктуацію.

        Дефолтна модель RapidOCR — мультимовна: іконки чи артефакти зображення іноді
        розпізнаються як китайські ієрогліфи (наприклад, "品13,984" замість "13,984" —
        коли в кроп потрапляє іконка поруч із текстом). Цей метод відсікає все, що
        не належить до латиниці/цифр/пунктуації, тому такі символи ніколи не
        потраплять у результат.

        Args:
            cords (Optional[List[int]]): Координати області скріншоту.

        Returns:
            str: Розпізнаний текст, обмежений латиницею, цифрами та пунктуацією.
        """
        return self.extract_text(
            cords=cords,
            allowed_chars=string.ascii_letters + string.digits + " .,'\"-:!?%$/()",
        )

    def find_text(
        self,
        target: str,
        cords: Optional[List[int]] = None,
        enhance: bool = False,
        case_sensitive: bool = False,
        image_filename: str = 'processed_image.png',
    ) -> Union[bool, Tuple[int, int]]:
        """Шукає рядок `target` на екрані один раз (без очікування).

        На відміну від `extract_text`/`read_text`/`scan_prices` (де `cords` вже
        задає обрізаний текст, тому детекція вимкнена), тут позиція тексту
        наперед невідома — детекція (`use_det=True`) вмикається завжди.

        Args:
            target (str): Текст, який шукаємо (підрядок, без урахування регістру за замовчуванням).
            cords (Optional[List[int]]): Область пошуку [x1, y1, x2, y2]. Якщо None — весь екран.
            enhance (bool): Апскейл + CLAHE перед пошуком (для складних скріншотів).
            case_sensitive (bool): Порівнювати з урахуванням регістру.
            image_filename (str): Ім'я файлу для збереження обробленого зображення.

        Returns:
            Union[bool, Tuple[int, int]]: Координати центру знайденого тексту на екрані,
            або False, якщо не знайдено.
        """
        try:
            result = self._run_ocr(cords, True, enhance, 2.2, 2.2, 1.3, (2, 2), image_filename)
            if not result or not result.txts:
                return False

            needle = target if case_sensitive else target.lower()
            offset_x, offset_y = (cords[0], cords[1]) if cords else (0, 0)

            for box, text in zip(result.boxes, result.txts):
                haystack = text if case_sensitive else text.lower()
                if needle in haystack:
                    center_x = int(box[:, 0].mean()) + offset_x
                    center_y = int(box[:, 1].mean()) + offset_y
                    return center_x, center_y
            return False
        except Exception as e:
            logger.error(f"Помилка при пошуку тексту '{target}': {e}")
            raise

    def wait_for_text(
        self,
        target: str,
        cords: Optional[List[int]] = None,
        search_time: Optional[float] = 15,
        enhance: bool = False,
        case_sensitive: bool = False,
    ) -> Union[bool, Tuple[int, int]]:
        """Періодично шукає `target` на екрані, поки не знайде або не мине `search_time`.

        Args:
            target (str): Текст, який шукаємо.
            cords (Optional[List[int]]): Область пошуку [x1, y1, x2, y2].
            search_time (Optional[float]): Час очікування в секундах. Якщо None — шукає, поки не знайде.
            enhance (bool): Апскейл + CLAHE перед пошуком.
            case_sensitive (bool): Порівнювати з урахуванням регістру.

        Returns:
            Union[bool, Tuple[int, int]]: Координати центру знайденого тексту, або False за таймаутом.
        """
        start_time = time.time()
        while True:
            result = self.find_text(target, cords=cords, enhance=enhance, case_sensitive=case_sensitive)
            if result:
                return result

            if search_time is not None and time.time() - start_time > search_time:
                return False

            time.sleep(0.5)

    def checking_text(
        self,
        target: str,
        cords: Optional[List[int]] = None,
        enhance: bool = False,
        case_sensitive: bool = False,
    ) -> Union[bool, Tuple[int, int]]:
        """Шукає `target` на екрані один раз (без очікування) — аліас `wait_for_text(search_time=0)`."""
        return self.wait_for_text(target, cords=cords, search_time=0, enhance=enhance, case_sensitive=case_sensitive)
