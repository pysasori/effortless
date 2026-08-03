"""
Відправка повідомлень у Telegram.

Приклад:
    from effortless.telegram import send_message

    send_message(api_token="...", chat_id=123456789, text="Hello!")
"""
import logging

import requests

logger = logging.getLogger(__name__)

__all__ = ["send_message"]


def send_message(api_token: str, chat_id: int, text: str) -> None:
    """
    Відправляє повідомлення в Telegram-чат (синхронно, без асинхронності).

    Args:
        api_token (str): Токен Telegram-бота.
        chat_id (int): ID чату, куди відправити повідомлення.
        text (str): Текст повідомлення.

    Raises:
        requests.exceptions.RequestException: Якщо виникла помилка під час відправки повідомлення.
    """
    url = f"https://api.telegram.org/bot{api_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"Повідомлення надіслано в Telegram: {text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Помилка при відправці повідомлення в Telegram: {e}")
