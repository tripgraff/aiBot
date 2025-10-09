# Telegram AI Bot

Telegram бот, использующий Yandex GPT API для ответов на сообщения пользователей.

## Установка

1. Установите зависимости:
```bash
pip3 install -r requirements.txt
```

2. Настройте переменные окружения (опционально):
```bash
export TELEGRAM_TOKEN="your_telegram_bot_token"
export YANDEX_API_KEY="your_yandex_api_key"
export YANDEX_FOLDER_ID="your_yandex_folder_id"
```

Или отредактируйте значения в файле `bot.py` напрямую.

## Запуск

```bash
python3 bot.py
```

## Проверка статуса

Для проверки статуса бота используйте:

```bash
python3 check_bot.py
```

## Использование

1. Найдите вашего бота в Telegram: [@myaiaggregator_bot](https://t.me/myaiaggregator_bot)
2. Отправьте команду `/start`
3. Напишите любой текст для получения ответа от ИИ

## Остановка бота

Для остановки бота используйте:

```bash
pkill -f "python3 bot.py"
```

## Возможные проблемы

- Убедитесь, что у вас установлен Python 3.7+
- Проверьте правильность токенов и API ключей
- Убедитесь, что у вас есть интернет-соединение
