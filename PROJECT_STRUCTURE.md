# 📁 Структура проекта xFusion AI

## 📂 Основные файлы

### Главные файлы
- **`bot.py`** - основной файл Telegram бота
- **`requirements.txt`** - зависимости Python
- **`install.sh`** - скрипт установки
- **`.gitignore`** - игнорируемые файлы для git

### Конфигурация
- **`ai api keys/`** - скриншоты для настройки API ключей
  - `hugg.png` - инструкция Hugging Face
  - `yandex.png` - инструкция Yandex
- **`API_KEYS_SETUP.md`** - подробная инструкция по настройке API ключей

### Документация
- **`README.md`** - основная документация проекта
- **`QUICK_START.txt`** - быстрый старт
- **`ADMIN_GUIDE.md`** - руководство администратора
- **`CHAT_SYSTEM_GUIDE.md`** - руководство по системе чатов
- **`MODELS_INFO.md`** - информация о доступных AI моделях
- **`TARIFFS_FINAL.md`** - информация о тарифах и подписках

### Веб-приложение
- **`deploy/`** - папка с веб-приложением для выбора моделей
  - `index.html` - интерфейс выбора моделей
- **`xfusion-webapp-latest.zip`** - архив для деплоя на Netlify
- **`NETLIFY_DEPLOYMENT.md`** - инструкция по развертыванию

### Ресурсы
- **`logo/`** - логотипы проекта
  - `logo 3.png` - основной логотип (1380x1380)
  - `logo xF.png` - альтернативный логотип (139x139)

### Данные
- **`user_chats/`** - сохраненные чаты пользователей (JSON)
- **`group_chats/`** - групповые чаты (JSON)

## 🗂️ Полная структура

```
xFusion AI/
├── bot.py                      # Главный файл бота
├── requirements.txt            # Зависимости
├── install.sh                  # Установка
├── .gitignore                  # Git ignore
│
├── 📖 Документация
│   ├── README.md
│   ├── QUICK_START.txt
│   ├── ADMIN_GUIDE.md
│   ├── CHAT_SYSTEM_GUIDE.md
│   ├── MODELS_INFO.md
│   ├── TARIFFS_FINAL.md
│   ├── API_KEYS_SETUP.md
│   └── NETLIFY_DEPLOYMENT.md
│
├── 🌐 Веб-приложение
│   ├── deploy/
│   │   └── index.html
│   └── xfusion-webapp-latest.zip
│
├── 🎨 Ресурсы
│   ├── logo/
│   │   ├── logo 3.png
│   │   └── logo xF.png
│   └── ai api keys/
│       ├── hugg.png
│       └── yandex.png
│
└── 💾 Данные
    ├── user_chats/
    │   └── *.json
    └── group_chats/
        └── *.json
```

## 🧹 Очищенные файлы

Удалены следующие временные и дублирующие файлы:
- Старые архивы веб-приложений
- Дублирующие инструкции по развертыванию
- Временные файлы анализа моделей и подписок
- Неиспользуемые конфигурационные файлы
- Тестовые файлы
- Кэш Python (`__pycache__`)
- Git история из папки deploy
- Лишние логотипы

## 📝 Рекомендации

1. **Backup данных**: Регулярно делайте backup папок `user_chats/` и `group_chats/`
2. **API ключи**: Храните ключи в безопасности, не коммитьте их в git
3. **Логи**: Файл `bot.log` автоматически создается при запуске бота
4. **Обновления**: При обновлении веб-приложения пересоздавайте `xfusion-webapp-latest.zip`


