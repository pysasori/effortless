"""
Effortless — це Python-бібліотека для автоматизації різних завдань.

Кожен домен — окремий підмодуль, який імпортується явно:
    import effortless.mouse as mouse
    import effortless.keyboard as keyboard
    from effortless.ocr import TextExtractor
    from effortless.vision import ImageSearcher
    from effortless.updater import AutoUpdater, GitUpdater
    from effortless.telegram import send_message
    from effortless.utils import random_delay, kill_process_by_window_name

Пакет навмисно не імпортує підмодулі тут: наприклад, effortless.ocr тягне
за собою RapidOCR + ONNX Runtime, і `import effortless` не повинен платити
за це, якщо потрібна лише миша.
"""

__version__ = "0.6.0"
