import psutil
import logging

# Налаштування логування
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def kill_process_by_window_name(window_name: str) -> bool:
    """
    Завершує процес за його ім'ям вікна.

    Args:
        window_name (str): Ім'я вікна процесу.

    Returns:
        bool: True, якщо процес знайдено і завершено, інакше False.
    """
    for proc in psutil.process_iter(attrs=['pid', 'name']):
        try:
            process_name = proc.info.get('name')
            if process_name and window_name.lower() in process_name.lower():
                logger.info(f"Завершуємо процес: {process_name} (PID: {proc.info['pid']})")
                proc.kill()
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            logger.warning(f"Помилка при спробі завершити процес {proc.info.get('name', 'Unknown')}: {e}")

    logger.warning(f"Процес з іменем '{window_name}' не знайдено.")
    return False