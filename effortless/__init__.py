"""
Effortless — це Python-бібліотека для автоматизації різних завдань.

Основні функції:
- Відправка повідомлень у Telegram.
- Робота з мишею.
- Пошук зображень на екрані.
- Автоматичне оновлення коду.
"""

from .text_extractor import TextExtractor
from .mouse_controller import MouseController
from .image_searcher import ImageSearcher
from .autoupdater import AutoUpdater, GitUpdater
from .utils import random_delay, kill_process_by_window_name, send_telegram_message

__version__ = "0.1.0"
