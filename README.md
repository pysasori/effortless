# Effortless

Effortless — це Python-бібліотека для автоматизації різних завдань, таких як робота з мишею, пошук зображень на екрані, відправка повідомлень у Telegram, автоматичне оновлення коду та інше. Бібліотека спрощує виконання рутинних завдань і дозволяє зосередитися на головному.

## Встановлення

1. Клонуйте репозиторій:
   ```bash
   git clone [https://github.com/username/effortless.git](https://github.com/username/effortless.git)
   cd effortless


2. Встановіть залежності:
   ```bash
   pip install -r requirements.txt
   ```

## Використання

### Відправка повідомлень у Telegram

Функція `send_telegram_message` відправляє повідомлення в Telegram-чат.

```python
from effortless import send_telegram_message

send_telegram_message(
    api_token="your_telegram_bot_token",
    chat_id=123456789,
    text="Hello, this is a test message!"
)
```

Параметри:

*   `api_token` (str): Токен вашого Telegram-бота.
*   `chat_id` (int): ID чату, куди відправити повідомлення.
*   `text` (str): Текст повідомлення.

### Робота з мишею

Клас `MouseController` дозволяє керувати мишею: переміщувати курсор, робити кліки, прокручувати сторінку тощо.

```python
from effortless import MouseController

# Переміщення курсора
MouseController.move(x=100, y=200)

# Клік за координатами
MouseController.click(x=150, y=250)

# Довгий клік
MouseController.long_click(t=0.5)

# Прокрутка сторінки
MouseController.scroll(px=100)
```

Основні методи:

*   `move(x, y, t)`: Переміщує курсор до координат `(x, y)` з затримкою `t`.
*   `click(x, y)`: Робить клік за координатами `(x, y)`.
*   `long_click(t)`: Довгий клік на поточній позиції з тривалістю `t`.
*   `scroll(px)`: Прокручує сторінку на `px` пікселів.

### Пошук зображень на екрані

Клас `ImageSearcher` дозволяє шукати зображення на екрані за допомогою OpenCV.

```python
from effortless import ImageSearcher

searcher = ImageSearcher()
result = searcher.search_image("template.png", cords=[100, 100, 500, 500])

if result:
    print(f"Зображення знайдено: {result}")
else:
    print("Зображення не знайдено.")
```

Параметри:

*   `template` (str): Шлях до зображення, яке потрібно знайти.
*   `cords` (list): Координати області пошуку `[x1, y1, x2, y2]`. Якщо `None`, пошук на всьому екрані.
*   `search_time` (float): Час пошуку в секундах. Якщо `None`, пошук відбувається один раз.

Методи:

*   `search_image(template, cords, search_time)`: Шукає зображення на екрані.
*   `checking_image(template, cords)`: Шукає зображення один раз (без очікування).

### Автоматичне оновлення коду

Клас `AutoUpdater` дозволяє автоматично перевіряти та застосовувати оновлення коду через Git.

```python
from effortless import AutoUpdater, GitUpdater

def after_update():
    print("Оновлення завершено!")

updater = AutoUpdater(
    updater=GitUpdater(branch="main"),
    restart_on_update=True,
    on_update_callback=after_update
)

updater.update_and_restart()
```

Параметри:

*   `updater` (`UpdaterBase`): Екземпляр класу для оновлення (наприклад, `GitUpdater`).
*   `restart_on_update` (bool): Чи потрібно перезапускати програму після оновлення.
*   `on_update_callback` (`Callable`): Функція, яка викликається після успішного оновлення.

### Генерація випадкової затримки

Функція `random_delay` дозволяє створювати випадкові затримки для імітації людської взаємодії.

```python
from effortless import random_delay

random_delay(min_delay=0.1, max_delay=0.5)
```

Параметри:

*   `min_delay` (float): Мінімальна затримка у секундах.
*   `max_delay` (float): Максимальна затримка у секундах.

### Завершення процесу за іменем вікна

Функція `kill_process_by_window_name` завершує процес за іменем його вікна.

```python
from effortless import kill_process_by_window_name

success = kill_process_by_window_name("notepad.exe")
```

Параметри:

*   `window_name` (str): Ім'я вікна процесу.

## Ліцензія

Цей проект ліцензовано за MIT License. Див. `LICENSE` для деталей.

## Автор

pysasori – pysasori@gmail.com
```
