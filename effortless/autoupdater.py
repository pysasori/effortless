"""
Модуль для автоматичного оновлення програмного забезпечення.

Цей модуль містить абстрактний клас `UpdaterBase`, який можна розширювати
для різних методів оновлення (наприклад, через Webhooks або API), а також
реалізацію `GitUpdater` для оновлення коду через Git.

Модуль також містить `AutoUpdater`, який дозволяє автоматично перевіряти
наявність оновлень, застосовувати їх та, за потреби, перезапускати програму.

Приклад використання:
    ```python
    def after_update():
        logging.info("Оновлення завершено! Виконуємо додаткові дії...")

    updater = AutoUpdater(updater=GitUpdater(), restart_on_update=True, on_update_callback=after_update)
    updater.update_and_restart()
    ```
"""
import subprocess
import sys
import os
import logging
from abc import ABC, abstractmethod
from typing import Optional, Callable

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class UpdaterBase(ABC):
    """Абстрактний клас оновлення, який можна розширювати під різні методи (Git, API, Webhooks тощо)."""

    @abstractmethod
    def check_for_updates(self) -> bool:
        """
        Перевіряє наявність оновлень.

        Returns:
            bool: True, якщо є оновлення, інакше False.
        """
        pass

    @abstractmethod
    def apply_updates(self) -> bool:
        """
        Завантажує та застосовує оновлення.

        Returns:
            bool: True, якщо оновлення пройшло успішно, інакше False.
        """
        pass


class GitUpdater(UpdaterBase):
    """Оновлення через Git."""

    def __init__(self, branch: str = "main") -> None:
        """
        Ініціалізація GitUpdater.

        Args:
            branch (str): Гілка для оновлення (за замовчуванням "main").
        """
        self.branch = branch

    def check_for_updates(self) -> bool:
        """
        Перевіряє наявність оновлень.
        У цій реалізації метод завжди повертає True, оскільки оновлення виконується одразу.

        Returns:
            bool: Завжди True.
        """
        return True

    def apply_updates(self) -> bool:
        """
        Виконує `git pull` для отримання останніх змін.

        Returns:
            bool: True, якщо код оновлено, False, якщо оновлень не було.
        """
        try:
            result = subprocess.run(["git", "pull", "origin", self.branch], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            if result.returncode == 0:
                output = result.stdout
                if "Already up to date." in output:
                    logging.info("Оновлень немає.")
                    return False
                logging.info("Код успішно оновлено.")
                return True
            else:
                logging.error(f"Помилка під час оновлення: {result.stderr}")
                return False
        except Exception as e:
            logging.error(f"Помилка виконання git pull: {e}")
            return False


class AutoUpdater:
    """Клас для автоматичного оновлення коду та перезапуску програми, якщо потрібно."""

    def __init__(
        self,
        updater: UpdaterBase,
        restart_on_update: bool = True,
        on_update_callback: Optional[Callable] = None
    ) -> None:
        """
        Ініціалізація AutoUpdater.

        Args:
            updater (UpdaterBase): Екземпляр класу UpdaterBase (наприклад, GitUpdater).
            restart_on_update (bool): Чи потрібно перезапускати програму після оновлення.
            on_update_callback (Optional[Callable]): Функція, що викликається після успішного оновлення.
        """
        self.updater = updater
        self.restart_on_update = restart_on_update
        self.on_update_callback = on_update_callback

    def update_and_restart(self) -> None:
        """
        Перевіряє наявність оновлень, застосовує їх
        і перезапускає програму, якщо це налаштовано.
        """
        if self.updater.check_for_updates():
            if self.updater.apply_updates():
                if self.on_update_callback:
                    self.on_update_callback()
                if self.restart_on_update:
                    self.restart_program()

    @staticmethod
    def restart_program() -> None:
        """
        Перезапускає поточний скрипт, використовуючи той самий інтерпретатор Python.
        """
        logging.info("Перезапуск програми...")
        python = sys.executable
        os.execl(python, python, *sys.argv)



