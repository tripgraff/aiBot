from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.error import Conflict, NetworkError
from telegram.request import HTTPXRequest
import requests
import os
import logging
import asyncio
import json
from datetime import datetime
from pathlib import Path
import uuid
import hashlib

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Загрузка переменных окружения из файла .env
def load_env_file():
    """Загружает переменные окружения из файла .env"""
    env_path = Path('.env')
    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

# Загружаем переменные окружения
load_env_file()

# Получение токенов из переменных окружения
TOKEN = os.getenv('TELEGRAM_TOKEN')
YANDEX_API_KEY = os.getenv('YANDEX_API_KEY')
YANDEX_FOLDER_ID = os.getenv('YANDEX_FOLDER_ID')

# API ключи для дополнительных моделей
GROQ_API_KEY = os.getenv('GROQ_API_KEY')  # Получи на https://console.groq.com
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', '')  # Получи на https://aistudio.google.com/app/apikey
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')  # Получи на https://openrouter.ai/keys

# ID администраторов (замени на свой Telegram ID)
# Чтобы узнать свой ID, напиши боту @userinfobot
ADMIN_IDS = [376978188, 483731479]  # Замени на свой ID!

# Текущая модель (None = не выбрана)
current_model = None

# Хранилище выбранных моделей для каждого пользователя
user_models = {}

# Хранилище состояний пользователей
user_states = {}

# Хранилище истории диалогов для каждого пользователя
user_conversations = {}

# Хранилище ID последних служебных сообщений для удаления
last_service_messages = {}

# Хранилище ID сообщений о выбранной модели (не удаляем при общении)
model_status_messages = {}

# Хранилище информации о пользователях для админа
user_info = {}

# Хранилище активных чатов: {user_id: current_chat_id}
user_current_chat = {}

# Хранилище всех чатов: {user_id: {chat_id: {...}, chat_id2: {...}}}
user_all_chats = {}

# Анти-спам защита
user_message_times = {}  # {user_id: [timestamp1, timestamp2, ...]}
user_warnings = {}  # {user_id: warning_count}
user_blocked = {}  # {user_id: block_until_timestamp}

# Хранилище состояния ожидания ID чата: {user_id: True/False}
awaiting_chat_id = {}

# 🤝 ГРУППОВЫЕ ЧАТЫ
# Хранилище групповых чатов: {group_id: {creator_id, model_id, members: [user_ids], created_at, title}}
group_chats = {}

# Хранилище текущего группового чата пользователя: {user_id: group_id}
user_current_group = {}

# Хранилище истории групповых чатов: {group_id: [{user_id, username, message, response, timestamp}]}
group_conversations = {}

# Хранилище состояния создания группы: {user_id: True/False}
creating_group = {}

# Создаем директорию для логов диалогов
LOGS_DIR = Path("user_chats")
LOGS_DIR.mkdir(exist_ok=True)

# Создаем директорию для групповых чатов
GROUP_LOGS_DIR = Path("group_chats")
GROUP_LOGS_DIR.mkdir(exist_ok=True)

def is_admin(user_id):
    """Проверка, является ли пользователь администратором"""
    return user_id in ADMIN_IDS

def generate_chat_id():
    """Генерация уникального ID для чата"""
    return str(uuid.uuid4())[:8]

def generate_chat_title(first_message):
    """Генерация названия чата на основе первого сообщения"""
    # Берем первые 5 слов из сообщения
    words = first_message.split()[:5]
    if len(words) == 0:
        return "Новый диалог"
    
    title = " ".join(words)
    # Обрезаем до 40 символов
    if len(title) > 40:
        title = title[:37] + "..."
    
    return title

def create_new_chat(user_id, username, model_id=None):
    """Создание нового чата при выборе модели"""
    chat_id = generate_chat_id()
    
    # Получаем название модели
    model_name = "Неизвестно"
    if model_id and model_id in MODELS:
        model_name = MODELS[model_id]['name']
    
    chat_data = {
        "chat_id": chat_id,
        "title": None,  # Название будет установлено при первом сообщении
        "created_at": datetime.now().isoformat(),
        "first_message": None,
        "message_count": 0,
        "model_id": model_id,
        "model_name": model_name,
        "messages": []
    }
    
    # Инициализируем структуру для пользователя
    if user_id not in user_all_chats:
        user_all_chats[user_id] = {}
    
    user_all_chats[user_id][chat_id] = chat_data
    user_current_chat[user_id] = chat_id
    
    # Сохраняем в файл
    save_user_chats(user_id, username)
    
    logging.info(f"Создан новый чат {chat_id} для пользователя {user_id} (модель: {model_name})")
    return chat_id

def save_user_chats(user_id, username):
    """Сохранение всех чатов пользователя в файл"""
    try:
        log_file = LOGS_DIR / f"user_{user_id}.json"
        
        data = {
            "user_id": user_id,
            "username": username,
            "chats": user_all_chats.get(user_id, {})
        }
        
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
    except Exception as e:
        logging.error(f"Ошибка при сохранении чатов: {e}")

def load_user_chats(user_id):
    """Загрузка чатов пользователя из файла"""
    try:
        log_file = LOGS_DIR / f"user_{user_id}.json"
        
        if log_file.exists():
            with open(log_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                user_all_chats[user_id] = data.get("chats", {})
                
                # Если есть чаты, устанавливаем последний как активный
                if user_all_chats[user_id]:
                    last_chat_id = max(
                        user_all_chats[user_id].keys(),
                        key=lambda k: user_all_chats[user_id][k].get("created_at", "")
                    )
                    user_current_chat[user_id] = last_chat_id
                    
    except Exception as e:
        logging.error(f"Ошибка при загрузке чатов: {e}")

def log_user_message(user_id, username, message_text, is_bot=False, model_id=None):
    """Логирование сообщений в текущий активный чат"""
    try:
        # Загружаем чаты пользователя, если еще не загружены
        if user_id not in user_all_chats:
            load_user_chats(user_id)
        
        # Получаем текущий чат
        if user_id not in user_current_chat:
            return
            
        chat_id = user_current_chat[user_id]
        
        # Проверяем, что чат существует
        if user_id not in user_all_chats or chat_id not in user_all_chats[user_id]:
            return
        
        chat_data = user_all_chats[user_id][chat_id]
        
        # Если это первое сообщение пользователя, устанавливаем название
        if not is_bot and chat_data["title"] is None:
            chat_data["title"] = generate_chat_title(message_text)
            chat_data["first_message"] = message_text
        
        # Добавляем сообщение в чат
        message = {
            "timestamp": datetime.now().isoformat(),
            "role": "bot" if is_bot else "user",
            "text": message_text
        }
        
        chat_data["messages"].append(message)
        chat_data["message_count"] += 1
        
        # Сохраняем в файл
        save_user_chats(user_id, username)
            
    except Exception as e:
        logging.error(f"Ошибка при логировании сообщения: {e}")

# Анти-спам функции
def check_spam(user_id):
    """
    Проверка на спам. Возвращает (is_spam, message)
    
    Лимиты:
    - 20 сообщений в минуту
    - При превышении: 3 предупреждения, затем блокировка на 5 минут
    """
    import time
    current_time = time.time()
    
    # Проверяем блокировку
    if user_id in user_blocked:
        block_until = user_blocked[user_id]
        if current_time < block_until:
            remaining_time = int((block_until - current_time) / 60)
            return True, f"🚫 **Вы заблокированы за спам!**\n\nПопробуйте снова через {remaining_time + 1} минут."
        else:
            # Блокировка истекла
            del user_blocked[user_id]
            user_warnings[user_id] = 0
            user_message_times[user_id] = []
    
    # Инициализируем список времени сообщений
    if user_id not in user_message_times:
        user_message_times[user_id] = []
    
    # Удаляем старые метки времени (старше 1 минуты)
    user_message_times[user_id] = [
        t for t in user_message_times[user_id]
        if current_time - t < 60
    ]
    
    # Добавляем текущее время
    user_message_times[user_id].append(current_time)
    
    # Проверяем лимит
    message_count = len(user_message_times[user_id])
    
    if message_count > 20:
        # Превышен лимит
        if user_id not in user_warnings:
            user_warnings[user_id] = 0
        
        user_warnings[user_id] += 1
        
        if user_warnings[user_id] >= 3:
            # Блокируем на 5 минут
            user_blocked[user_id] = current_time + 300  # 5 минут
            logging.warning(f"Пользователь {user_id} заблокирован за спам на 5 минут")
            return True, "🚫 **Вы заблокированы за спам на 5 минут!**\n\nСлишком много сообщений."
        else:
            # Предупреждение
            warnings_left = 3 - user_warnings[user_id]
            return True, f"⚠️ **Внимание! Слишком много сообщений.**\n\nЛимит: 20 сообщений в минуту.\n\nПредупреждений осталось: {warnings_left}\nПосле 3 предупреждений вы будете заблокированы на 5 минут."
    
    return False, None

def update_user_info(user_id, username, first_name, last_name):
    """Обновление информации о пользователе"""
    if user_id not in user_info:
        user_info[user_id] = {
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "first_seen": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
            "message_count": 0
        }
    else:
        user_info[user_id]["last_seen"] = datetime.now().isoformat()
        user_info[user_id]["message_count"] += 1

def format_ai_response(text):
    """Улучшенное форматирование ответов от AI для Telegram"""
    import re
    
    # Экранируем специальные символы Telegram Markdown V2 только вне блоков кода
    def escape_markdown(text):
        # Не трогаем текст внутри ``` блоков
        parts = []
        in_code_block = False
        current = ""
        
        i = 0
        while i < len(text):
            if i + 2 < len(text) and text[i:i+3] == '```':
                if current:
                    if not in_code_block:
                        # Экранируем обычный текст
                        current = current.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]')
                    parts.append(current)
                    current = ""
                parts.append('```')
                in_code_block = not in_code_block
                i += 3
            elif i < len(text) and text[i] == '`':
                if current:
                    if not in_code_block:
                        current = current.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]')
                    parts.append(current)
                    current = ""
                # Находим закрывающий `
                end = text.find('`', i + 1)
                if end != -1:
                    parts.append(text[i:end+1])
                    i = end + 1
                else:
                    current += text[i]
                    i += 1
            else:
                current += text[i]
                i += 1
        
        if current:
            if not in_code_block:
                current = current.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]')
            parts.append(current)
        
        return ''.join(parts)
    
    # Применяем форматирование
    formatted = text
    
    # Улучшаем форматирование списков
    formatted = re.sub(r'^(\d+)\.\s+', r'*\1\.* ', formatted, flags=re.MULTILINE)
    formatted = re.sub(r'^[•·]\s+', r'• ', formatted, flags=re.MULTILINE)
    
    # Добавляем разделители для лучшей читаемости
    formatted = re.sub(r'\n\n\n+', r'\n\n', formatted)
    
    return formatted

async def load_chat_by_id(user_id, username, chat_id_to_load):
    """Загрузка чата по ID для продолжения"""
    # Загружаем чаты пользователя, если еще не загружены
    if user_id not in user_all_chats:
        load_user_chats(user_id)
    
    # Проверяем, что чат существует
    if user_id not in user_all_chats or chat_id_to_load not in user_all_chats[user_id]:
        return None
    
    chat_data = user_all_chats[user_id][chat_id_to_load]
    
    # Устанавливаем этот чат как активный
    user_current_chat[user_id] = chat_id_to_load
    
    # Возобновляем чат - удаляем флаг завершения
    if 'completed_at' in chat_data:
        del chat_data['completed_at']
    
    # Загружаем последние 40 сообщений в контекст
    messages = chat_data.get("messages", [])
    user_conversations[user_id] = []
    
    # Берем последние 40 сообщений для контекста
    for msg in messages[-40:]:
        role = msg.get("role", "user")
        text = msg.get("text", "")
        
        # Преобразуем в формат для AI
        if role == "bot":
            user_conversations[user_id].append({'role': 'assistant', 'text': text})
        else:
            user_conversations[user_id].append({'role': 'user', 'text': text})
    
    # Устанавливаем модель из чата
    model_id = chat_data.get("model_id")
    if model_id and model_id in MODELS:
        user_models[user_id] = model_id
    
    # Сохраняем изменения (удаление completed_at)
    save_user_chats(user_id, username)
    
    logging.info(f"Загружен чат {chat_id_to_load} для пользователя {user_id}, контекст: {len(user_conversations[user_id])} сообщений")
    
    return chat_data

async def finalize_chat(user_id, username, context, chat_id_to_send):
    """Завершение текущего чата и отправка информации пользователю"""
    if user_id not in user_current_chat or user_id not in user_all_chats:
        return None
    
    current_chat_id = user_current_chat[user_id]
    
    if current_chat_id not in user_all_chats[user_id]:
        return None
    
    chat_data = user_all_chats[user_id][current_chat_id]
    message_count = chat_data.get("message_count", 0)
    
    # Если чат пустой (нет сообщений) - удаляем его полностью
    if message_count == 0:
        # Удаляем сообщение о выбранной модели
        if user_id in model_status_messages:
            try:
                await context.bot.delete_message(
                    chat_id=chat_id_to_send,
                    message_id=model_status_messages[user_id]
                )
                del model_status_messages[user_id]
            except Exception as e:
                logging.error(f"Ошибка при удалении сообщения о модели: {e}")
        
        del user_all_chats[user_id][current_chat_id]
        save_user_chats(user_id, username)
        del user_current_chat[user_id]
        if user_id in user_conversations:
            user_conversations[user_id] = []
        logging.info(f"Пустой чат {current_chat_id} пользователя {user_id} удален")
        return None
    
    # Сохраняем финальную информацию о чате
    chat_data["completed_at"] = datetime.now().isoformat()
    save_user_chats(user_id, username)
    
    # Удаляем текущий чат из активных
    del user_current_chat[user_id]
    
    # Очищаем историю разговора
    if user_id in user_conversations:
        user_conversations[user_id] = []
    
    logging.info(f"Чат {current_chat_id} пользователя {user_id} завершен")
    
    return chat_data

def get_yandex_response(text, conversation_history=None):
    """Получение ответа от Yandex GPT API с поддержкой истории диалога"""
    try:
        url = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'
        headers = {
            'Authorization': f'Api-Key {YANDEX_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        # Формируем историю сообщений
        messages = []
        if conversation_history:
            # Добавляем предыдущие сообщения (последние 10 для экономии токенов)
            for msg in conversation_history[-10:]:
                messages.append(msg)
        
        # Добавляем текущее сообщение пользователя
        messages.append({'role': 'user', 'text': text})
        
        data = {
            'modelUri': f'gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite',
            'completionOptions': {
                'stream': False,
                'temperature': 0.6,
                'maxTokens': 2000
            },
            'messages': messages
        }
        
        logging.info(f"Отправляем запрос к Yandex API: {text[:50]}... (история: {len(messages)-1} сообщений)")
        response = requests.post(url, json=data, headers=headers, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            if 'result' in result and 'alternatives' in result['result']:
                return result['result']['alternatives'][0]['message']['text']
            else:
                return "Извините, не удалось получить ответ от Yandex GPT."
        else:
            logging.error(f"Yandex API ошибка {response.status_code}: {response.text}")
            return f"Ошибка Yandex API ({response.status_code}). Попробуйте позже."
            
    except Exception as e:
        logging.error(f"Ошибка при запросе к Yandex API: {e}")
        return "Произошла ошибка при обработке запроса Yandex. Попробуйте позже."

def get_available_groq_models():
    """Получение списка доступных моделей Groq"""
    if not GROQ_API_KEY:
        return []
    
    try:
        url = 'https://api.groq.com/openai/v1/models'
        headers = {
            'Authorization': f'Bearer {GROQ_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return [model['id'] for model in data.get('data', [])]
        else:
            logging.error(f"Ошибка получения моделей Groq: {response.status_code}")
            return []
    except Exception as e:
        logging.error(f"Ошибка при получении моделей Groq: {e}")
        return []

def get_groq_response(text, conversation_history=None, model="llama-3.1-8b-instant"):
    """Получение ответа от Groq API с retry логикой (быстрые бесплатные модели)"""
    import time
    
    # Проверяем наличие API ключа
    if not GROQ_API_KEY:
        return "❌ API ключ Groq не настроен. Проверьте файл .env"
    
    try:
        url = 'https://api.groq.com/openai/v1/chat/completions'
        headers = {
            'Authorization': f'Bearer {GROQ_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        # Формируем историю сообщений в формате OpenAI
        messages = []
        if conversation_history:
            for msg in conversation_history[-10:]:
                role = 'assistant' if msg['role'] == 'assistant' else 'user'
                messages.append({'role': role, 'content': msg['text']})
        
        messages.append({'role': 'user', 'content': text})
        
        data = {
            'model': model,
            'messages': messages,
            'temperature': 0.7,
            'max_tokens': 2000
        }
        
        # Retry логика для обхода временных ошибок (503, 429, 500)
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                logging.info(f"Отправляем запрос к Groq API ({model}): {text[:50]}... [попытка {attempt + 1}/{max_retries}]")
                response = requests.post(url, json=data, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    result = response.json()
                    content = result['choices'][0]['message']['content']
                    
                    # Удаляем теги <think> из ответов Qwen
                    import re
                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.IGNORECASE)
                    content = content.strip()
                    
                    return content
                elif response.status_code in [503, 429, 500]:
                    # Временные ошибки - пробуем еще раз
                    logging.warning(f"Groq API временная ошибка {response.status_code}, повтор через {retry_delay}с...")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Экспоненциальная задержка
                        continue
                    else:
                        return f"⚠️ Groq API перегружен ({response.status_code}). Попробуйте другую модель или подождите минуту."
                else:
                    error_text = response.text
                    logging.error(f"Groq API ошибка {response.status_code}: {error_text}")
                    
                    # Детальная обработка ошибок
                    if response.status_code == 400:
                        try:
                            error_data = response.json()
                            if 'error' in error_data and 'message' in error_data['error']:
                                return f"❌ Ошибка Groq API: {error_data['error']['message']}"
                        except:
                            pass
                        return "❌ Неверный запрос к Groq API. Проверьте модель и параметры."
                    elif response.status_code == 401:
                        return "❌ Неверный API ключ Groq. Проверьте настройки."
                    elif response.status_code == 403:
                        return "❌ Доступ запрещен к Groq API. Проверьте права доступа."
                    elif response.status_code == 404:
                        return "❌ Модель не найдена в Groq API."
                    else:
                        return f"❌ Ошибка Groq API ({response.status_code}). Попробуйте позже."
            except requests.exceptions.Timeout:
                logging.warning(f"Timeout при запросе к Groq API, повтор...")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    return "⚠️ Groq API не отвечает. Попробуйте другую модель."
            
    except Exception as e:
        logging.error(f"Ошибка при запросе к Groq API: {e}")
        return "Произошла ошибка при обработке запроса Groq. Попробуйте позже."

async def groq_models_command(update: Update, context):
    """Команда для проверки доступных моделей Groq"""
    user_id = update.effective_user.id
    
    # Проверяем права администратора
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Эта команда доступна только администраторам.")
        return
    
    try:
        models = get_available_groq_models()
        if models:
            models_text = "🤖 **Доступные модели Groq:**\n\n"
            for model in sorted(models):
                models_text += f"• `{model}`\n"
            
            models_text += f"\n📊 **Всего моделей:** {len(models)}"
            await update.message.reply_text(models_text, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Не удалось получить список моделей Groq. Проверьте API ключ.")
    except Exception as e:
        logging.error(f"Ошибка в команде groq_models: {e}")
        await update.message.reply_text("❌ Ошибка при получении списка моделей.")

def get_gemini_response(text, conversation_history=None, model="gemini-1.5-flash"):
    """Получение ответа от Google Gemini API (бесплатный)"""
    try:
        if not GOOGLE_API_KEY or GOOGLE_API_KEY == '':
            return "⚠️ Google API ключ не настроен. Получите его на https://aistudio.google.com/app/apikey"
        
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GOOGLE_API_KEY}'
        headers = {'Content-Type': 'application/json'}
        
        # Формируем историю
        contents = []
        if conversation_history:
            for msg in conversation_history[-10:]:
                role = 'model' if msg['role'] == 'assistant' else 'user'
                contents.append({'role': role, 'parts': [{'text': msg['text']}]})
        
        contents.append({'role': 'user', 'parts': [{'text': text}]})
        
        data = {
            'contents': contents,
            'generationConfig': {
                'temperature': 0.7,
                'maxOutputTokens': 2000
            }
        }
        
        logging.info(f"Отправляем запрос к Gemini API ({model}): {text[:50]}...")
        response = requests.post(url, json=data, headers=headers, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            return result['candidates'][0]['content']['parts'][0]['text']
        else:
            logging.error(f"Gemini API ошибка {response.status_code}: {response.text}")
            return f"Ошибка Gemini API ({response.status_code}). Проверьте API ключ."
            
    except Exception as e:
        logging.error(f"Ошибка при запросе к Gemini API: {e}")
        return "Произошла ошибка при обработке запроса Gemini. Попробуйте позже."

def get_openrouter_response(text, conversation_history=None, model="openai/gpt-3.5-turbo"):
    """Получение ответа от OpenRouter API (доступ к множеству моделей)"""
    import time
    
    try:
        if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == '':
            return "⚠️ OpenRouter API ключ не настроен. Получите его на https://openrouter.ai/keys"
        
        url = 'https://openrouter.ai/api/v1/chat/completions'
        headers = {
            'Authorization': f'Bearer {OPENROUTER_API_KEY}',
            'Content-Type': 'application/json',
            'HTTP-Referer': 'https://github.com/xfusion-ai',  # Опционально
            'X-Title': 'xFusion AI Bot'  # Опционально
        }
        
        # Формируем историю сообщений в формате OpenAI
        messages = []
        if conversation_history:
            for msg in conversation_history[-10:]:
                role = 'assistant' if msg['role'] == 'assistant' else 'user'
                messages.append({'role': role, 'content': msg['text']})
        
        messages.append({'role': 'user', 'content': text})
        
        data = {
            'model': model,
            'messages': messages,
            'temperature': 0.7,
            'max_tokens': 2000
        }
        
        # Retry логика для обхода временных ошибок
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                logging.info(f"Отправляем запрос к OpenRouter API ({model}): {text[:50]}... [попытка {attempt + 1}/{max_retries}]")
                response = requests.post(url, json=data, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    result = response.json()
                    return result['choices'][0]['message']['content']
                elif response.status_code in [503, 429, 500]:
                    logging.warning(f"OpenRouter API временная ошибка {response.status_code}, повтор через {retry_delay}с...")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    else:
                        return f"⚠️ OpenRouter API перегружен ({response.status_code}). Попробуйте другую модель."
                else:
                    logging.error(f"OpenRouter API ошибка {response.status_code}: {response.text}")
                    return f"Ошибка OpenRouter API ({response.status_code}). Проверьте API ключ."
            except requests.exceptions.Timeout:
                logging.warning(f"Timeout при запросе к OpenRouter API, повтор...")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    return "⚠️ OpenRouter API не отвечает. Попробуйте другую модель."
            
    except Exception as e:
        logging.error(f"Ошибка при запросе к OpenRouter API: {e}")
        return "Произошла ошибка при обработке запроса OpenRouter. Попробуйте позже."

def auto_select_model(text, conversation_history=None):
    """🤖 Умный выбор модели в зависимости от типа запроса"""
    text_lower = text.lower()
    
    # Определяем тип запроса и выбираем лучшую модель
    
    # 1. КОД И ПРОГРАММИРОВАНИЕ (максимальное покрытие)
    code_keywords = [
        # Языки программирования
        'код', 'code', 'python', 'javascript', 'typescript', 'java', 'c++', 'c#', 'csharp', 'ruby', 
        'php', 'go', 'golang', 'rust', 'kotlin', 'swift', 'scala', 'dart', 'r', 'matlab', 'perl',
        # Веб-технологии
        'html', 'css', 'sass', 'scss', 'react', 'vue', 'angular', 'node', 'nodejs', 'express',
        'django', 'flask', 'fastapi', 'spring', 'nextjs', 'nuxt', 'svelte', 'jquery',
        # Базы данных
        'sql', 'mysql', 'postgresql', 'postgres', 'mongodb', 'redis', 'sqlite', 'database',
        'nosql', 'orm', 'query', 'запрос', 'таблица', 'table',
        # Разработка
        'программ', 'функция', 'function', 'class', 'метод', 'method', 'переменная', 'variable',
        'массив', 'array', 'объект', 'object', 'json', 'xml', 'api', 'rest', 'graphql',
        'debug', 'отладка', 'ошибка', 'error', 'exception', 'баг', 'bug', 'тест', 'test',
        'юнит-тест', 'unittest', 'pytest', 'jest',
        # Инструменты
        'git', 'github', 'gitlab', 'docker', 'kubernetes', 'ci/cd', 'jenkins', 'webpack',
        'npm', 'yarn', 'pip', 'composer', 'maven', 'gradle',
        # Алгоритмы
        'алгоритм', 'algorithm', 'сортировка', 'sorting', 'поиск', 'search', 'рекурсия',
        'recursion', 'структура данных', 'data structure', 'дерево', 'tree', 'граф', 'graph',
        # Паттерны и архитектура
        'паттерн', 'pattern', 'mvc', 'mvvm', 'singleton', 'factory', 'observer', 'архитектура',
        'architecture', 'микросервис', 'microservice', 'monolith',
        # Прочее
        'синтаксис', 'syntax', 'компиляция', 'compile', 'интерпрет', 'interpreter',
        'рефакторинг', 'refactor', 'оптимизация кода', 'code optimization'
    ]
    if any(keyword in text_lower for keyword in code_keywords):
        selected_model = 'grok-code-fast'
        selected_name = 'Grok Code Fast'
        response = get_openrouter_response(text, conversation_history, "x-ai/grok-code-fast-1")
        return f"🤖 *Auto\\-Select:* {selected_name} 💻\n\n{response}"
    
    # 2. МАТЕМАТИКА, ЛОГИКА И СЛОЖНЫЕ РАССУЖДЕНИЯ
    reasoning_keywords = [
        # Математика
        'математика', 'math', 'арифметика', 'arithmetic', 'алгебра', 'algebra', 'геометрия',
        'geometry', 'тригонометрия', 'trigonometry', 'calculus', 'исчисление', 'интеграл',
        'integral', 'производная', 'derivative', 'уравнение', 'equation', 'формула', 'formula',
        'теорема', 'theorem', 'доказательство', 'proof',
        # Вычисления
        'вычисли', 'calculate', 'посчитай', 'compute', 'реши', 'solve', 'найди корни',
        'roots', 'система уравнений', 'equations system',
        # Логика и рассуждения
        'логика', 'logic', 'доказать', 'prove', 'reasoning', 'рассужд', 'рассмотр',
        'силлогизм', 'syllogism', 'дедукция', 'deduction', 'индукция', 'induction',
        # Анализ
        'анализ', 'analysis', 'синтез', 'synthesis', 'почему', 'why', 'как так', 'how come',
        'объясни', 'explain', 'причина', 'reason', 'следствие', 'consequence',
        # Задачи
        'задача', 'problem', 'решение', 'solution', 'ответ на задачу', 'word problem',
        'головоломка', 'puzzle', 'загадка', 'riddle',
        # Статистика и вероятность
        'статистика', 'statistics', 'вероятность', 'probability', 'распределение',
        'distribution', 'корреляция', 'correlation', 'регрессия', 'regression',
        # Физика и науки
        'физика', 'physics', 'механика', 'mechanics', 'термодинамика', 'thermodynamics',
        'квантовая', 'quantum', 'теория относительности', 'relativity'
    ]
    if any(keyword in text_lower for keyword in reasoning_keywords):
        selected_model = 'deepseek-r1'
        selected_name = 'DeepSeek R1'
        response = get_openrouter_response(text, conversation_history, "deepseek/deepseek-r1")
        return f"🤖 *Auto\\-Select:* {selected_name} 🧠\n\n{response}"
    
    # 3. КРЕАТИВНОЕ ПИСЬМО И КОНТЕНТ
    creative_keywords = [
        # Письмо
        'напиши', 'write', 'сочин', 'compose', 'создай текст', 'generate text',
        'письмо', 'letter', 'email', 'имейл',
        # Литература
        'история', 'story', 'рассказ', 'tale', 'роман', 'novel', 'повесть', 'novella',
        'стих', 'poem', 'поэзия', 'poetry', 'стихотворение', 'verse', 'рифма', 'rhyme',
        'сказка', 'fairy tale', 'фантастика', 'fiction', 'фэнтези', 'fantasy',
        # Контент
        'статья', 'article', 'пост', 'post', 'блог', 'blog', 'контент', 'content',
        'копирайт', 'copywriting', 'текст для сайта', 'website copy', 'слоган', 'slogan',
        'заголовок', 'headline', 'описание', 'description',
        # Академическое письмо
        'эссе', 'essay', 'реферат', 'paper', 'диссертация', 'dissertation', 'тезис', 'thesis',
        'аннотация', 'abstract', 'обзор литературы', 'literature review',
        # Креатив
        'креатив', 'creative', 'идея', 'idea', 'концепт', 'concept', 'brainstorm',
        'мозговой штурм', 'сценарий', 'script', 'диалог', 'dialogue', 'монолог', 'monologue',
        # Маркетинг
        'рекламный текст', 'ad copy', 'продающий текст', 'sales copy', 'призыв к действию',
        'call to action', 'cta', 'landing page', 'лендинг'
    ]
    if any(keyword in text_lower for keyword in creative_keywords):
        selected_model = 'claude-4.5-sonnet'
        selected_name = 'Claude 4.5 Sonnet'
        response = get_openrouter_response(text, conversation_history, "anthropic/claude-4.5-sonnet")
        return f"🤖 *Auto\\-Select:* {selected_name} 🎭\n\n{response}"
    
    # 4. ПЕРЕВОДЫ И ЯЗЫКИ
    translation_keywords = [
        'перевед', 'translate', 'translation', 'переводчик', 'translator',
        'на английский', 'на русский', 'на испанский', 'на французский', 'на немецкий',
        'на китайский', 'на японский', 'на корейский', 'на итальянский', 'на португальский',
        'to english', 'to russian', 'to spanish', 'to french', 'to german', 'to chinese',
        'язык', 'language', 'иностранный', 'foreign', 'грамматика', 'grammar',
        'произношение', 'pronunciation', 'словарь', 'dictionary', 'фраза на', 'phrase in'
    ]
    if any(keyword in text_lower for keyword in translation_keywords):
        selected_model = 'gpt-5'
        selected_name = 'GPT-5'
        response = get_openrouter_response(text, conversation_history, "openai/gpt-5")
        return f"🤖 *Auto\\-Select:* {selected_name} 🌍\n\n{response}"
    
    # 5. ДАННЫЕ, АНАЛИТИКА, ML/AI
    data_keywords = [
        'данные', 'data', 'аналитика', 'analytics', 'анализ данных', 'data analysis',
        'датасет', 'dataset', 'csv', 'excel', 'таблица данных', 'dataframe', 'pandas',
        'numpy', 'matplotlib', 'visualiz', 'визуализация', 'график', 'chart', 'plot',
        'machine learning', 'ml', 'машинное обучение', 'нейросеть', 'neural network',
        'deep learning', 'глубокое обучение', 'ai', 'искусственный интеллект',
        'tensorflow', 'pytorch', 'keras', 'sklearn', 'scikit-learn',
        'модель', 'model', 'обучение', 'training', 'предсказание', 'prediction',
        'классификация', 'classification', 'кластеризация', 'clustering',
        'регрессия', 'regression', 'feature engineering', 'признаки',
        'bigdata', 'большие данные', 'hadoop', 'spark', 'etl'
    ]
    if any(keyword in text_lower for keyword in data_keywords):
        selected_model = 'qwen-3-thinking'
        selected_name = 'Qwen 3 Thinking'
        response = get_openrouter_response(text, conversation_history, "qwen/qwen-3-thinking")
        return f"🤖 *Auto\\-Select:* {selected_name} 📊\n\n{response}"
    
    # 6. БИЗНЕС И СТРАТЕГИЯ
    business_keywords = [
        'бизнес', 'business', 'стратегия', 'strategy', 'маркетинг', 'marketing',
        'продажи', 'sales', 'roi', 'окупаемость', 'прибыль', 'profit', 'revenue',
        'выручка', 'бюджет', 'budget', 'инвестиции', 'investment', 'стартап', 'startup',
        'бизнес-план', 'business plan', 'swot', 'конкуренты', 'competitors',
        'целевая аудитория', 'target audience', 'сегмент', 'segment', 'ниша', 'niche',
        'позиционирование', 'positioning', 'брендинг', 'branding', 'бренд', 'brand',
        'кастдев', 'custdev', 'customer development', 'unit economics', 'юнит-экономика',
        'метрики', 'metrics', 'kpi', 'conversion', 'конверсия', 'воронка', 'funnel',
        'монетизация', 'monetization', 'ценообразование', 'pricing'
    ]
    if any(keyword in text_lower for keyword in business_keywords):
        selected_model = 'gpt-4o'
        selected_name = 'GPT-4o'
        response = get_openrouter_response(text, conversation_history, "openai/gpt-4o")
        return f"🤖 *Auto\\-Select:* {selected_name} 💼\n\n{response}"
    
    # 7. МЕДИЦИНА И ЗДОРОВЬЕ
    medical_keywords = [
        'здоровье', 'health', 'медицина', 'medicine', 'болезнь', 'disease', 'illness',
        'симптом', 'symptom', 'лечение', 'treatment', 'терапия', 'therapy',
        'диагноз', 'diagnosis', 'врач', 'doctor', 'доктор', 'physician',
        'анализ крови', 'blood test', 'витамин', 'vitamin', 'препарат', 'drug', 'medication',
        'фармакология', 'pharmacology', 'аптека', 'pharmacy', 'рецепт', 'prescription',
        'питание', 'nutrition', 'диета', 'diet', 'калории', 'calories',
        'фитнес', 'fitness', 'тренировка', 'workout', 'упражнение', 'exercise',
        'психология', 'psychology', 'стресс', 'stress', 'депрессия', 'depression',
        'тревога', 'anxiety', 'сон', 'sleep', 'бессонница', 'insomnia'
    ]
    if any(keyword in text_lower for keyword in medical_keywords):
        selected_model = 'gemini-2.5-pro'
        selected_name = 'Gemini 2.5 Pro'
        response = get_openrouter_response(text, conversation_history, "google/gemini-2.5-pro")
        return f"🤖 *Auto\\-Select:* {selected_name} 🏥\n\n{response}"
    
    # 8. ОБРАЗОВАНИЕ И ОБУЧЕНИЕ
    education_keywords = [
        'учеба', 'study', 'обучение', 'learning', 'образование', 'education',
        'школа', 'school', 'универ', 'university', 'колледж', 'college',
        'экзамен', 'exam', 'тест', 'quiz', 'домашка', 'homework', 'задание', 'assignment',
        'курс', 'course', 'лекция', 'lecture', 'семинар', 'seminar', 'вебинар', 'webinar',
        'учебник', 'textbook', 'конспект', 'notes', 'шпаргалка', 'cheat sheet',
        'объясни простыми словами', 'explain simply', 'eli5', 'как понять', 'how to understand',
        'разбери тему', 'break down', 'примеры', 'examples', 'практика', 'practice'
    ]
    if any(keyword in text_lower for keyword in education_keywords):
        selected_model = 'claude-3.5-haiku'
        selected_name = 'Claude 3.5 Haiku'
        response = get_openrouter_response(text, conversation_history, "anthropic/claude-3.5-haiku")
        return f"🤖 *Auto\\-Select:* {selected_name} 📚\n\n{response}"
    
    # 9. ЮРИДИЧЕСКИЕ ВОПРОСЫ
    legal_keywords = [
        'юридический', 'legal', 'закон', 'law', 'право', 'rights', 'правовой', 'lawful',
        'договор', 'contract', 'соглашение', 'agreement', 'контракт', 'deal',
        'иск', 'lawsuit', 'суд', 'court', 'судебный', 'judicial',
        'адвокат', 'lawyer', 'attorney', 'юрист', 'юр.лицо', 'legal entity',
        'ип', 'ооо', 'llc', 'регистрация', 'registration', 'лицензия', 'license',
        'налог', 'tax', 'налоговая', 'налогообложение', 'taxation',
        'интеллектуальная собственность', 'intellectual property', 'патент', 'patent',
        'авторское право', 'copyright', 'товарный знак', 'trademark'
    ]
    if any(keyword in text_lower for keyword in legal_keywords):
        selected_model = 'gpt-4.1'
        selected_name = 'GPT-4.1'
        response = get_openrouter_response(text, conversation_history, "openai/gpt-4.1")
        return f"🤖 *Auto\\-Select:* {selected_name} ⚖️\n\n{response}"
    
    # 10. КРАТКИЕ БЫСТРЫЕ ВОПРОСЫ (< 50 символов)
    if len(text) < 50 and ('?' in text or any(word in text_lower for word in ['что', 'как', 'где', 'когда', 'кто', 'сколько', 'what', 'how', 'where', 'when', 'who'])):
        selected_model = 'gemini-2.5-flash'
        selected_name = 'Gemini 2.5 Flash'
        response = get_openrouter_response(text, conversation_history, "google/gemini-2.5-flash")
        return f"🤖 *Auto\\-Select:* {selected_name} ⚡\n\n{response}"
    
    # 11. ДЛИННЫЕ СЛОЖНЫЕ ЗАПРОСЫ (> 500 символов) - мощная модель с большим контекстом
    if len(text) > 500:
        selected_model = 'gpt-5'
        selected_name = 'GPT-5'
        response = get_openrouter_response(text, conversation_history, "openai/gpt-5")
        return f"🤖 *Auto\\-Select:* {selected_name} 🌟\n\n{response}"
    
    # 12. ПО УМОЛЧАНИЮ - универсальная мощная модель
    selected_model = 'gpt-4o'
    selected_name = 'GPT-4o'
    response = get_openrouter_response(text, conversation_history, "openai/gpt-4o")
    return f"🤖 *Auto\\-Select:* {selected_name} 💫\n\n{response}"

# Словарь моделей с метаданными (24 модели: 23 обычных + Auto-Select)
MODELS = {
    # 🤖 AUTO-SELECT - умный выбор модели
    'auto-select': {
        'name': '🤖 Auto-Select',
        'emoji': '🎯',
        'description': 'Умный выбор лучшей модели для вашего запроса',
        'category': 'auto',
        'function': auto_select_model
    },
    
    # OpenAI GPT модели
    'gpt-5': {
        'name': 'GPT 5',
        'emoji': '🌟',
        'description': 'Новейшая GPT-5 от OpenAI',
        'category': 'openai',
        'function': lambda text, history: get_openrouter_response(text, history, "openai/gpt-5")
    },
    'gpt-5-nano': {
        'name': 'GPT 5 Nano',
        'emoji': '⚡',
        'description': 'Компактная версия GPT-5',
        'category': 'openai',
        'function': lambda text, history: get_openrouter_response(text, history, "openai/gpt-5-nano")
    },
    'gpt-4.1': {
        'name': 'GPT-4.1',
        'emoji': '🤖',
        'description': 'Улучшенная GPT-4',
        'category': 'openai',
        'function': lambda text, history: get_openrouter_response(text, history, "openai/gpt-4.1")
    },
    'gpt-4.1-mini': {
        'name': 'GPT-4.1 Mini',
        'emoji': '⚙️',
        'description': 'Быстрая GPT-4.1 Mini',
        'category': 'openai',
        'function': lambda text, history: get_openrouter_response(text, history, "openai/gpt-4.1-mini")
    },
    'gpt-4o': {
        'name': 'GPT-4o',
        'emoji': '💫',
        'description': 'Оптимизированная GPT-4o',
        'category': 'openai',
        'function': lambda text, history: get_openrouter_response(text, history, "openai/gpt-4o")
    },
    'gpt-4o-mini': {
        'name': 'GPT-4o Mini',
        'emoji': '🔷',
        'description': 'Быстрая GPT-4o Mini',
        'category': 'openai',
        'function': lambda text, history: get_openrouter_response(text, history, "openai/gpt-4o-mini")
    },
    'gpt-4-turbo': {
        'name': 'GPT-4 Turbo',
        'emoji': '💨',
        'description': 'Быстрая GPT-4 Turbo',
        'category': 'openai',
        'function': lambda text, history: get_openrouter_response(text, history, "openai/gpt-4-turbo")
    },
    
    # DeepSeek модели
    'deepseek-r1': {
        'name': 'DeepSeek R1',
        'emoji': '🧠',
        'description': 'Reasoning модель от DeepSeek',
        'category': 'deepseek',
        'function': lambda text, history: get_openrouter_response(text, history, "deepseek/deepseek-r1")
    },
    'deepseek-v3': {
        'name': 'DeepSeek V3',
        'emoji': '🔮',
        'description': 'Мощная модель DeepSeek V3',
        'category': 'deepseek',
        'function': lambda text, history: get_openrouter_response(text, history, "deepseek/deepseek-chat")
    },
    
    # Google Gemini модели
    'gemini-2.5-pro': {
        'name': 'Gemini 2.5 Pro',
        'emoji': '💎',
        'description': 'Топовая Gemini с большим контекстом',
        'category': 'google',
        'function': lambda text, history: get_openrouter_response(text, history, "google/gemini-2.5-pro")
    },
    'gemini-2.5-flash': {
        'name': 'Gemini 2.5 Flash',
        'emoji': '✨',
        'description': 'Быстрая Gemini 2.5 Flash',
        'category': 'google',
        'function': lambda text, history: get_openrouter_response(text, history, "google/gemini-2.5-flash")
    },
    
    # Qwen модели
    'qwen-3': {
        'name': 'Qwen 3',
        'emoji': '🐉',
        'description': 'Мощная китайская модель Qwen 3',
        'category': 'qwen',
        'function': lambda text, history: get_openrouter_response(text, history, "qwen/qwen-3")
    },
    'qwen-3-thinking': {
        'name': 'Qwen 3 Thinking',
        'emoji': '🧩',
        'description': 'Reasoning версия Qwen 3',
        'category': 'qwen',
        'function': lambda text, history: get_openrouter_response(text, history, "qwen/qwen-3-thinking")
    },
    
    # Anthropic Claude модели
    'claude-4.5-sonnet': {
        'name': 'Claude 4.5 Sonnet',
        'emoji': '🎭',
        'description': 'Топовый Claude 4.5 Sonnet от Anthropic',
        'category': 'claude',
        'function': lambda text, history: get_openrouter_response(text, history, "anthropic/claude-4.5-sonnet")
    },
    'claude-3.5-haiku': {
        'name': 'Claude 3.5 Haiku',
        'emoji': '🎨',
        'description': 'Быстрый Claude 3.5 Haiku',
        'category': 'claude',
        'function': lambda text, history: get_openrouter_response(text, history, "anthropic/claude-3.5-haiku")
    },
    
    # xAI Grok модели
    'grok-4': {
        'name': 'Grok 4',
        'emoji': '🚀',
        'description': 'Новейший Grok 4 от xAI',
        'category': 'grok',
        'function': lambda text, history: get_openrouter_response(text, history, "x-ai/grok-4")
    },
    'grok-3': {
        'name': 'Grok 3',
        'emoji': '🎯',
        'description': 'Grok 3 от Илона Маска',
        'category': 'grok',
        'function': lambda text, history: get_openrouter_response(text, history, "x-ai/grok-3")
    },
    'grok-4-fast': {
        'name': 'Grok 4 Fast',
        'emoji': '⚡',
        'description': 'Быстрый Grok 4 (2M контекст!)',
        'category': 'grok',
        'function': lambda text, history: get_openrouter_response(text, history, "x-ai/grok-4-fast")
    },
    'grok-code-fast': {
        'name': 'Grok Code Fast',
        'emoji': '💻',
        'description': 'Grok для программирования',
        'category': 'grok',
        'function': lambda text, history: get_openrouter_response(text, history, "x-ai/grok-code-fast-1")
    },
    
    # Groq модели - только актуальные
    'llama-3.1-8b': {
        'name': 'Llama 3.1 8B',
        'emoji': '🦙',
        'description': 'Быстрая Llama 3.1 8B через Groq',
        'category': 'llama',
        'function': lambda text, history: get_groq_response(text, history, "llama-3.1-8b-instant")
    },
    'gpt-oss-120b': {
        'name': 'GPT OSS 120B',
        'emoji': '🚀',
        'description': 'Мощная модель GPT OSS 120B через Groq',
        'category': 'gpt',
        'function': lambda text, history: get_groq_response(text, history, "openai/gpt-oss-120b")
    },
    'qwen3-32b': {
        'name': 'Qwen 3 32B',
        'emoji': '🐉',
        'description': 'Мощная модель Qwen 3 32B через Groq',
        'category': 'qwen',
        'function': lambda text, history: get_groq_response(text, history, "qwen/qwen3-32b")
    },
    
    # Yandex
    'yandex-gpt': {
        'name': 'Yandex GPT',
        'emoji': '🟣',
        'description': 'Русский ИИ от Яндекса',
        'category': 'yandex',
        'function': get_yandex_response
    }
}

def get_main_keyboard():
    """Создает основную клавиатуру с командами"""
    keyboard = [
        [KeyboardButton("💬 Создать новый чат"), KeyboardButton("📂 Ваши чаты")],
        [KeyboardButton("👥 Группы"), KeyboardButton("📊 Статус")],
        [KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_chat_keyboard():
    """Создает клавиатуру для режима общения с ИИ"""
    webapp_url = "https://xfusionai.netlify.app/"
    keyboard = [
        [
            KeyboardButton("◀️ Главное меню"), 
            KeyboardButton("🤖 Модели", web_app=WebAppInfo(url=webapp_url))
    ]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_back_keyboard():
    """Клавиатура с кнопкой возврата в главное меню"""
    keyboard = [
        [KeyboardButton("◀️ Главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_models_keyboard(user_id):
    """Создает клавиатуру для выбора моделей"""
    keyboard = []
    
    user_current_model = user_models.get(user_id)
    
    for model_id, model_info in MODELS.items():
        # Добавляем галочку для текущей модели пользователя
        button_text = f"{model_info['emoji']} {model_info['name']}"
        
        # Выделяем Auto-Select серым обрамлением
        if model_id == 'auto-select':
            button_text = f"▪️ {button_text} ▪️"
        
        if user_current_model and model_id == user_current_model:
            button_text += " ✓"
        keyboard.append([KeyboardButton(button_text)])
    
    # Кнопка возврата в главное меню (только если модель уже выбрана)
    if user_current_model:
        keyboard.append([KeyboardButton("◀️ Главное меню")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def delete_service_messages(context, chat_id, user_id):
    """Удаляет все сохраненные служебные сообщения пользователя"""
    if user_id in last_service_messages:
        for msg_id in last_service_messages[user_id]:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except:
                pass
        last_service_messages[user_id] = []

# ========================================
# 🤝 ФУНКЦИИ ДЛЯ ГРУППОВЫХ ЧАТОВ
# ========================================

def create_group_chat(creator_id, creator_username, model_id, title=None):
    """Создает новый групповой чат"""
    group_id = "grp_" + str(uuid.uuid4())[:8]
    
    group_chats[group_id] = {
        'creator_id': creator_id,
        'creator_username': creator_username,
        'model_id': model_id,
        'members': [creator_id],
        'member_usernames': {creator_id: creator_username},
        'created_at': datetime.now().isoformat(),
        'title': title or f"Группа #{group_id[-4:]}",
        'message_count': 0
    }
    
    group_conversations[group_id] = []
    
    # Сохраняем в файл
    save_group_chat(group_id)
    
    return group_id

def add_member_to_group(group_id, user_id, username):
    """Добавляет участника в группу"""
    if group_id not in group_chats:
        return False, "Группа не найдена"
    
    group = group_chats[group_id]
    
    if len(group['members']) >= 5:
        return False, "В группе уже максимальное количество участников (5)"
    
    if user_id in group['members']:
        return False, "Пользователь уже в группе"
    
    group['members'].append(user_id)
    group['member_usernames'][user_id] = username
    save_group_chat(group_id)
    
    return True, "Участник добавлен"

def remove_member_from_group(group_id, user_id):
    """Удаляет участника из группы"""
    if group_id not in group_chats:
        return False, "Группа не найдена"
    
    group = group_chats[group_id]
    
    if user_id not in group['members']:
        return False, "Пользователь не в группе"
    
    # Если это создатель и он не последний участник
    if user_id == group['creator_id'] and len(group['members']) > 1:
        return False, "Создатель не может покинуть группу, пока в ней есть другие участники"
    
    group['members'].remove(user_id)
    if user_id in group['member_usernames']:
        del group['member_usernames'][user_id]
    
    # Если это был последний участник, удаляем группу
    if len(group['members']) == 0:
        delete_group_chat(group_id)
        return True, "Группа удалена (последний участник вышел)"
    
    save_group_chat(group_id)
    return True, "Вы покинули группу"

def get_group_info(group_id):
    """Получает информацию о группе"""
    if group_id not in group_chats:
        return None
    
    return group_chats[group_id]

def is_group_member(group_id, user_id):
    """Проверяет, является ли пользователь участником группы"""
    if group_id not in group_chats:
        return False
    return user_id in group_chats[group_id]['members']

def save_group_chat(group_id):
    """Сохраняет групповой чат в файл"""
    if group_id not in group_chats:
        return
    
    group_file = GROUP_LOGS_DIR / f"{group_id}.json"
    
    data = {
        'info': group_chats[group_id],
        'history': group_conversations.get(group_id, [])
    }
    
    with open(group_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_group_chats():
    """Загружает все групповые чаты из файлов"""
    global group_chats, group_conversations
    
    for group_file in GROUP_LOGS_DIR.glob("grp_*.json"):
        try:
            with open(group_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                group_id = group_file.stem
                group_chats[group_id] = data.get('info', {})
                group_conversations[group_id] = data.get('history', [])
        except Exception as e:
            logging.error(f"Ошибка загрузки группового чата {group_file}: {e}")

def delete_group_chat(group_id):
    """Удаляет групповой чат"""
    if group_id in group_chats:
        del group_chats[group_id]
    
    if group_id in group_conversations:
        del group_conversations[group_id]
    
    # Удаляем файл
    group_file = GROUP_LOGS_DIR / f"{group_id}.json"
    if group_file.exists():
        group_file.unlink()
    
    # Удаляем у всех пользователей ссылку на эту группу
    for user_id in list(user_current_group.keys()):
        if user_current_group[user_id] == group_id:
            del user_current_group[user_id]

def get_user_groups(user_id):
    """Получает список групп пользователя"""
    user_groups = []
    for group_id, group_info in group_chats.items():
        if user_id in group_info['members']:
            user_groups.append({
                'group_id': group_id,
                'title': group_info['title'],
                'model': MODELS[group_info['model_id']]['name'] if group_info['model_id'] in MODELS else 'Unknown',
                'members_count': len(group_info['members']),
                'is_creator': user_id == group_info['creator_id']
            })
    return user_groups

async def start(update: Update, context):
    """Команда /start"""
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user{user_id}"
    first_name = update.effective_user.first_name or "друг"
    
    # 🔗 Обработка Deep Link для присоединения к группе
    if context.args and context.args[0].startswith('join_'):
        group_id = context.args[0].replace('join_', '')
        
        if group_id not in group_chats:
            await update.message.reply_text("❌ Группа не найдена или была удалена")
            return
        
        group_info = get_group_info(group_id)
        
        # Проверяем, может ли пользователь присоединиться
        if user_id in group_info['members']:
            # Активируем группу
            user_current_group[user_id] = group_id
            
            is_creator = user_id == group_info['creator_id']
            
            # Получаем ссылку-приглашение
            bot_username = (await context.bot.get_me()).username
            invite_link = f"https://t.me/{bot_username}?start=join_{group_id}"
            
            menu_text = f"""✅ **Вы уже участник группы!**

💬 **Групповой чат: {group_info['title']}**

🤖 **Модель:** {MODELS[group_info['model_id']]['name']}
👥 **Участников:** {len(group_info['members'])}/5

Теперь можете писать сообщения - все участники увидят их!

Выберите действие:"""
            
            # Inline-кнопки управления группой
            if is_creator:
                inline_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Инфо о группе", callback_data=f"groupinfo_{group_id}")],
                    [InlineKeyboardButton("🔗 Поделиться ссылкой", switch_inline_query=invite_link)],
                    [InlineKeyboardButton("❌ Завершить группу", callback_data=f"deletegroup_{group_id}")]
                ])
            else:
                inline_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Инфо о группе", callback_data=f"groupinfo_{group_id}")],
                    [InlineKeyboardButton("🚪 Выйти из группы", callback_data=f"confirmleave_{group_id}")]
                ])
            
            # Только одна Reply-кнопка "Главное меню"
            reply_keyboard = ReplyKeyboardMarkup([
                [KeyboardButton("◀️ Главное меню")]
            ], resize_keyboard=True)
            
            await update.message.reply_text(menu_text, reply_markup=inline_keyboard)
            
            await context.bot.send_message(
                chat_id=user_id,
                text="Выберите действие:",
                reply_markup=reply_keyboard
            )
            return
        
        success, message = add_member_to_group(group_id, user_id, username)
        
        if success:
            user_current_group[user_id] = group_id
            
            join_text = f"""✅ **Вы присоединились к группе!**

📝 **Название:** {group_info['title']}
🤖 **Модель:** {MODELS[group_info['model_id']]['name']}
👥 **Участников:** {len(group_info['members'])}/5

Теперь можете писать сообщения - все участники группы увидят их и ответы AI! 🚀"""
            
            # Inline-кнопки для управления группой
            inline_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Инфо о группе", callback_data=f"groupinfo_{group_id}")],
                [InlineKeyboardButton("🚪 Выйти из группы", callback_data=f"confirmleave_{group_id}")]
            ])
            
            # Только одна Reply-кнопка "Главное меню"
            reply_keyboard = ReplyKeyboardMarkup([
                [KeyboardButton("◀️ Главное меню")]
            ], resize_keyboard=True)
            
            await update.message.reply_text(join_text, reply_markup=inline_keyboard)
            
            await context.bot.send_message(
                chat_id=user_id,
                text="Выберите действие:",
                reply_markup=reply_keyboard
            )
            
            # Уведомляем других участников
            for member_id in group_info['members']:
                if member_id != user_id:
                    try:
                        await context.bot.send_message(
                            chat_id=member_id,
                            text=f"👤 **@{username}** присоединился к группе **{group_info['title']}**\n\n👥 Теперь участников: {len(group_info['members'])}/5"
                        )
                    except:
                        pass
        else:
            await update.message.reply_text(f"❌ {message}")
        
        return
    
    # Отправляем логотип
    logo_path = Path("logo/logo xF.png")
    try:
        if logo_path.exists():
            with open(logo_path, 'rb') as logo:
                await update.message.reply_photo(
                    photo=logo,
                    caption="⚡️ **xFusion AI** - Мощный агрегатор AI-моделей"
                )
    except Exception as e:
        logging.error(f"Ошибка при отправке логотипа: {e}")
    
    # Проверяем, выбрана ли модель
    if user_id not in user_models:
        # Пользователь новый - показываем приветствие с главным меню
        welcome_text = f"""👋 **Привет, {first_name}!**

🚀 **Добро пожаловать в xFusion AI!**

Я - умный агрегатор AI-моделей, который объединяет лучшие языковые модели в одном месте!

✨ **Что я умею:**
• 🤖 Множество AI-моделей (Yandex GPT, Gemini, Claude и другие)
• 💬 Запоминаю контекст разговора (до 40 сообщений)
• 💾 Сохраняю все ваши чаты с возможностью продолжения
• 🔄 Быстрое переключение между моделями
• ⏱️ Показываю статус "печатает..."
• 📊 Личная статистика и история диалогов

🎯 **Начните работу:**
1. Нажмите "🤖 Модели"
2. Выберите AI-модель
3. Начните общение!

💡 **Подсказка:** Нажмите "❓ Помощь" для подробной инструкции"""
        
        keyboard = get_main_keyboard()
        sent_msg = await update.message.reply_text(welcome_text, reply_markup=keyboard)
        
        # Сохраняем ID приветственного сообщения для последующего удаления
        if user_id not in last_service_messages:
            last_service_messages[user_id] = []
        last_service_messages[user_id].append(sent_msg.message_id)
    else:
        # Пользователь уже выбрал модель - показываем статус
        current_model_info = MODELS[user_models[user_id]]
        
        # Загружаем чаты для статистики
        if user_id not in user_all_chats:
            load_user_chats(user_id)
        
        total_chats = sum(1 for chat in user_all_chats.get(user_id, {}).values() if chat.get('message_count', 0) > 0)
        
        welcome_text = f"""👋 **С возвращением, {first_name}!**

⚡️ **xFusion AI готов к работе!**

🧠 **Текущая модель:**
{current_model_info['emoji']} **{current_model_info['name']}**
_{current_model_info['description']}_

📊 **Ваша статистика:**
• 💬 Сохранено чатов: {total_chats}
• 🎯 Активная модель: {current_model_info['name']}

💬 **Просто напишите сообщение для общения!**
🎯 Используйте кнопки меню ниже! 👇"""
        
        keyboard = get_main_keyboard()
        sent_msg = await update.message.reply_text(welcome_text, reply_markup=keyboard)
        
        # Сохраняем ID приветственного сообщения для последующего удаления
        if user_id not in last_service_messages:
            last_service_messages[user_id] = []
        last_service_messages[user_id].append(sent_msg.message_id)

async def help_command(update: Update, context):
    """Команда /help"""
    user_id = update.effective_user.id
    
    # Удаляем сообщение пользователя с нажатием кнопки
    try:
        await update.message.delete()
    except:
        pass
    
    # Удаляем все предыдущие служебные сообщения
    await delete_service_messages(context, update.effective_chat.id, user_id)
    
    help_text = """📚 **Справка:**

💬 **Как использовать:**
• Просто пишите сообщения - бот запомнит контекст
• Бот помнит последние 40 сообщений (20 пар вопрос-ответ)
• Используйте кнопки меню для команд

🎮 **Кнопки меню:**
💬 Создать новый чат - создать новый чат с ИИ
📊 Статус - показать статус и статистику
📂 Ваши чаты - список всех чатов (можно продолжить или удалить)
👥 Группы - создание и управление групповыми чатами
❓ Помощь - показать эту справку

🤖 **Переключение моделей:**
• В чате нажмите "🤖 Модели"
• Выберите нужную модель из списка
• Модель с ✓ - текущая активная

💡 Начните писать боту - служебные сообщения исчезнут!"""
    
    keyboard = get_back_keyboard()
    sent_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=help_text,
        reply_markup=keyboard
    )
    
    # Сохраняем ID служебного сообщения
    if user_id not in last_service_messages:
        last_service_messages[user_id] = []
    last_service_messages[user_id].append(sent_msg.message_id)

async def status_command(update: Update, context):
    """Команда /status"""
    user_id = update.effective_user.id
    
    # Удаляем сообщение пользователя с нажатием кнопки
    try:
        await update.message.delete()
    except:
        pass
    
    # Удаляем все предыдущие служебные сообщения
    await delete_service_messages(context, update.effective_chat.id, user_id)
    
    # Проверяем, выбрана ли модель
    if user_id not in user_models:
        keyboard = get_models_keyboard(user_id)
        sent_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ **Сначала выберите модель!**\n\nВыберите ИИ-модель для начала работы:",
            reply_markup=keyboard
        )
        # Сохраняем ID служебного сообщения
        if user_id not in last_service_messages:
            last_service_messages[user_id] = []
        last_service_messages[user_id].append(sent_msg.message_id)
        return
    
    message_count = len(user_conversations.get(user_id, [])) // 2
    
    current_model_info = MODELS[user_models[user_id]]
    
    status_text = f"""📊 **Статус бота:**

🤖 **Текущая модель:**
{current_model_info['emoji']} **{current_model_info['name']}**
_{current_model_info['description']}_

💬 **Сообщений в истории:** {message_count}

📚 **Доступно моделей:** {len(MODELS)}
🌐 **Сеть:** ✅ Подключен
🔄 **Статус:** ✅ Активен

Бот готов к работе! 🚀"""
    
    keyboard = get_back_keyboard()
    sent_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=status_text,
        reply_markup=keyboard
    )
    
    # Сохраняем ID служебного сообщения
    if user_id not in last_service_messages:
        last_service_messages[user_id] = []
    last_service_messages[user_id].append(sent_msg.message_id)

async def my_chats_command(update: Update, context):
    """Команда для просмотра своих чатов"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    
    # Удаляем сообщение пользователя
    try:
        await update.message.delete()
    except:
        pass
    
    # Удаляем все предыдущие служебные сообщения
    await delete_service_messages(context, update.effective_chat.id, user_id)
    
    # Загружаем чаты пользователя
    if user_id not in user_all_chats:
        load_user_chats(user_id)
    
    user_chats = user_all_chats.get(user_id, {})
    
    if not user_chats:
        keyboard = get_main_keyboard()
        sent_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📭 **У вас пока нет сохраненных чатов.**\n\nНачните общение с ботом, чтобы создать первый чат!",
            reply_markup=keyboard
        )
        if user_id not in last_service_messages:
            last_service_messages[user_id] = []
        last_service_messages[user_id].append(sent_msg.message_id)
        return
    
    # Фильтруем пустые чаты (без сообщений)
    non_empty_chats = {
        chat_id: chat_data 
        for chat_id, chat_data in user_chats.items() 
        if (chat_data.get('message_count', 0) > 0 or len(chat_data.get('messages', [])) > 0)
    }
    
    # Проверяем, есть ли непустые чаты
    if not non_empty_chats:
        keyboard = get_main_keyboard()
        sent_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📭 **У вас пока нет сохраненных чатов.**\n\nНачните общение с ботом, чтобы создать первый чат!",
            reply_markup=keyboard
        )
        if user_id not in last_service_messages:
            last_service_messages[user_id] = []
        last_service_messages[user_id].append(sent_msg.message_id)
        return
    
    # Формируем список чатов (показываем последние 10 непустых)
    sorted_chats = sorted(
        non_empty_chats.items(), 
        key=lambda x: x[1].get('created_at', ''), 
        reverse=True
    )[:10]
    
    # Отправляем каждый чат отдельным сообщением с inline-кнопками
    for idx, (chat_id, chat_data) in enumerate(sorted_chats, 1):
        title = chat_data.get('title', 'Новый диалог')
        if not title:
            title = "Новый диалог"
        model_name = chat_data.get('model_name', 'Неизвестно')
        msg_count = chat_data.get('message_count', 0)
        created = chat_data.get('created_at', '')[:10]
        
        chat_text = f"**{title}**\n\n"
        chat_text += f"🆔 `{chat_id}`\n"
        chat_text += f"🤖 {model_name}\n"
        chat_text += f"💬 {msg_count} сообщений\n"
        chat_text += f"📅 {created}"
        
        # Создаем inline-кнопки
        inline_keyboard = [
            [
                InlineKeyboardButton("💬 Перейти к чату", callback_data=f"open_{chat_id}"),
                InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{chat_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        
        sent_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=chat_text,
            reply_markup=reply_markup
        )
        
        # Сохраняем ID сообщения для удаления
        if user_id not in last_service_messages:
            last_service_messages[user_id] = []
        last_service_messages[user_id].append(sent_msg.message_id)
    
    # Отправляем итоговое сообщение с кнопкой "Главное меню"
    if len(non_empty_chats) > 10:
        footer_text = f"\n\n_Показано 10 последних чатов из {len(non_empty_chats)}_"
    else:
        footer_text = ""
    
    keyboard = get_back_keyboard()
    sent_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"💬 **Ваши чаты** ({len(non_empty_chats)} шт.){footer_text}",
        reply_markup=keyboard
    )
    
    # Сохраняем ID служебного сообщения
    if user_id not in last_service_messages:
        last_service_messages[user_id] = []
    last_service_messages[user_id].append(sent_msg.message_id)

async def clear_command(update: Update, context):
    """Команда /clear - очистить контекст разговора (история для AI)"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    
    # Удаляем сообщение пользователя
    try:
        await update.message.delete()
    except:
        pass
    
    # Удаляем все предыдущие служебные сообщения
    await delete_service_messages(context, update.effective_chat.id, user_id)
    
    # Очищаем только историю контекста для AI (НЕ завершаем чат!)
    if user_id in user_conversations:
        user_conversations[user_id] = []
    
    keyboard = get_chat_keyboard()
    sent_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🧹 **Контекст очищен!**\n\nБот забыл предыдущие сообщения.\nЧат продолжается, но AI не помнит историю.",
        reply_markup=keyboard
    )
    
    # Сохраняем ID служебного сообщения
    if user_id not in last_service_messages:
        last_service_messages[user_id] = []
    last_service_messages[user_id].append(sent_msg.message_id)

async def admin_stats(update: Update, context):
    """Админ-команда: общая статистика бота"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    total_users = len(user_info)
    total_conversations = sum(len(conv) for conv in user_conversations.values())
    active_users = len([u for u in user_info.values() if (datetime.now() - datetime.fromisoformat(u['last_seen'])).days < 1])
    
    stats_text = f"""📊 **Статистика бота (Админ)**

👥 **Пользователи:**
• Всего: {total_users}
• Активных за 24ч: {active_users}

💬 **Диалоги:**
• Всего сообщений: {total_conversations}
• Активных диалогов: {len(user_conversations)}

🤖 **Модели:**
• Доступно: {len(MODELS)}
• Пользователей с выбранной моделью: {len(user_models)}

📁 **Логи:**
• Файлов диалогов: {len(list(LOGS_DIR.glob('user_*.json')))}

🕐 **Время сервера:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
    
    await update.message.reply_text(stats_text)

async def admin_users(update: Update, context):
    """Админ-команда: список пользователей"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    if not user_info:
        await update.message.reply_text("📭 Пока нет пользователей.")
        return
    
    users_text = "👥 **Список пользователей:**\n\n"
    
    for uid, info in list(user_info.items())[:20]:  # Показываем первых 20
        username = info.get('username', 'Нет username')
        first_name = info.get('first_name', 'Неизвестно')
        msg_count = info.get('message_count', 0)
        model = user_models.get(uid, 'Не выбрана')
        if model != 'Не выбрана':
            model = MODELS.get(model, {}).get('name', model)
        
        # Загружаем чаты пользователя для подсчета (только непустые)
        if uid not in user_all_chats:
            load_user_chats(uid)
        all_chats = user_all_chats.get(uid, {})
        chats_count = sum(1 for chat in all_chats.values() if chat.get('message_count', 0) > 0)
        
        users_text += f"🆔 `{uid}`\n"
        users_text += f"   👤 {first_name} (@{username})\n"
        users_text += f"   💬 Сообщений: {msg_count}\n"
        users_text += f"   📚 Чатов: {chats_count}\n"
        users_text += f"   🤖 Модель: {model}\n\n"
    
    if len(user_info) > 20:
        users_text += f"\n_...и еще {len(user_info) - 20} пользователей_"
    
    await update.message.reply_text(users_text)

async def admin_chats(update: Update, context):
    """Админ-команда: список всех чатов пользователя"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    if not context.args:
        await update.message.reply_text("ℹ️ Использование: `/admin_chats <user_id>`\n\nПример: `/admin_chats 123456789`")
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Неверный формат ID. Используйте число.")
        return
    
    # Загружаем чаты пользователя
    load_user_chats(target_user_id)
    
    if target_user_id not in user_all_chats or not user_all_chats[target_user_id]:
        await update.message.reply_text(f"❌ Чаты пользователя `{target_user_id}` не найдены.")
        return
    
    chats_text = f"📚 **Чаты пользователя {target_user_id}:**\n\n"
    
    # Фильтруем только непустые чаты
    all_chats = user_all_chats[target_user_id]
    non_empty_chats = {
        chat_id: chat_data 
        for chat_id, chat_data in all_chats.items() 
        if chat_data.get('message_count', 0) > 0
    }
    
    if not non_empty_chats:
        await update.message.reply_text(f"📭 У пользователя `{target_user_id}` нет чатов с сообщениями.")
        return
    
    for idx, (chat_id, chat_data) in enumerate(sorted(non_empty_chats.items(), key=lambda x: x[1].get('created_at', ''), reverse=True)[:20], 1):
        title = chat_data.get('title', 'Без названия')
        created = chat_data.get('created_at', '')[:10]
        msg_count = chat_data.get('message_count', 0)
        
        chats_text += f"{idx}. 🆔 `{chat_id}`\n"
        chats_text += f"   📝 {title}\n"
        chats_text += f"   📅 {created} | 💬 {msg_count} сообщений\n\n"
    
    if len(non_empty_chats) > 20:
        chats_text += f"\n_...и еще {len(non_empty_chats) - 20} чатов_\n\n"
    
    chats_text += "\n💡 Для просмотра чата: `/admin_chat {user_id} {chat_id}`"
    
    await update.message.reply_text(chats_text)

async def admin_chat(update: Update, context):
    """Админ-команда: просмотр конкретного чата"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    # Проверяем аргументы команды
    if len(context.args) < 2:
        await update.message.reply_text(
            "ℹ️ Использование:\n"
            "`/admin_chat <user_id> <chat_id>`\n\n"
            "Примеры:\n"
            "`/admin_chat 123456789 a1b2c3d4`\n\n"
            "Или используйте `/admin_chats <user_id>` для списка чатов"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        target_chat_id = context.args[1]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Неверный формат. Используйте: `/admin_chat <user_id> <chat_id>`")
        return
    
    # Загружаем чаты пользователя
    load_user_chats(target_user_id)
    
    # Проверяем наличие чата
    if target_user_id not in user_all_chats or target_chat_id not in user_all_chats[target_user_id]:
        await update.message.reply_text(
            f"❌ Чат `{target_chat_id}` пользователя `{target_user_id}` не найден.\n\n"
            f"Используйте `/admin_chats {target_user_id}` для списка чатов."
        )
        return
    
    try:
        chat_data = user_all_chats[target_user_id][target_chat_id]
        
        title = chat_data.get('title', 'Без названия')
        created = chat_data.get('created_at', 'Неизвестно')[:16].replace('T', ' ')
        msg_count = chat_data.get('message_count', 0)
        messages = chat_data.get('messages', [])
        
        chat_text = f"💬 **Чат {target_chat_id}**\n\n"
        chat_text += f"👤 Пользователь: `{target_user_id}`\n"
        chat_text += f"📝 Название: {title}\n"
        chat_text += f"📅 Создан: {created}\n"
        chat_text += f"💬 Сообщений: {msg_count}\n\n"
        chat_text += "📝 **Последние 20 сообщений:**\n\n"
        
        # Показываем последние 20 сообщений
        for msg in messages[-20:]:
            timestamp = msg.get('timestamp', '')[:16].replace('T', ' ')
            role = "🤖" if msg.get('role') == 'bot' else "👤"
            text = msg.get('text', '')[:150]
            if len(msg.get('text', '')) > 150:
                text += "..."
            
            chat_text += f"{timestamp} {role}: {text}\n\n"
        
        # Telegram имеет лимит 4096 символов
        if len(chat_text) > 4000:
            parts = [chat_text[i:i+4000] for i in range(0, len(chat_text), 4000)]
            for part in parts:
                await update.message.reply_text(part)
        else:
            await update.message.reply_text(chat_text)
            
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при чтении чата: {e}")

async def models_command(update: Update, context):
    """Команда /models - показать доступные модели"""
    user_id = update.effective_user.id
    
    # Удаляем сообщение пользователя
    try:
        await update.message.delete()
    except:
        pass
    
    # Удаляем все предыдущие служебные сообщения
    await delete_service_messages(context, update.effective_chat.id, user_id)
    
    models_text = "🤖 **Доступные ИИ-модели:**\n\n"
    
    user_current_model = user_models.get(user_id)
    
    for model_id, model_info in MODELS.items():
        status = "✅ Активна" if user_current_model and model_id == user_current_model else "⚪️ Доступна"
        models_text += f"{model_info['emoji']} **{model_info['name']}** - {status}\n"
        models_text += f"   _{model_info['description']}_\n\n"
    
    models_text += "💡 Выберите модель из меню ниже 👇"
    
    keyboard = get_models_keyboard(user_id)
    sent_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=models_text,
        reply_markup=keyboard
    )
    
    # Сохраняем ID служебного сообщения
    if user_id not in last_service_messages:
        last_service_messages[user_id] = []
    last_service_messages[user_id].append(sent_msg.message_id)

# ========================================
# 👥 КОМАНДЫ ДЛЯ РАБОТЫ С ГРУППАМИ
# ========================================

async def groups_command(update: Update, context):
    """Команда для отображения меню групповых чатов"""
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user{user_id}"
    
    try:
        await update.message.delete()
    except:
        pass
    
    # Удаляем все предыдущие служебные сообщения
    await delete_service_messages(context, update.effective_chat.id, user_id)
    
    user_groups = get_user_groups(user_id)
    groups_count = len(user_groups)
    
    groups_text = f"""👥 **Групповые чаты**

🎯 **Групповой чат** - это возможность общаться с AI совместно с другими пользователями (до 5 человек).

💡 **Преимущества:**
• Общая история диалога
• Все видят сообщения и ответы AI
• Совместная работа над задачами
• До 5 участников в одной группе

📊 **У вас:** {groups_count} {"группа" if groups_count == 1 else "групп" if groups_count < 5 else "групп"}

👇 Выберите действие:"""
    
    # Обычная клавиатура с кнопками
    buttons = []
    buttons.append([KeyboardButton("➕ Создать группу")])
    if groups_count > 0:
        buttons.append([KeyboardButton("📂 Мои группы")])
    buttons.append([KeyboardButton("◀️ Главное меню")])
    
    keyboard = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    
    sent_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=groups_text,
        reply_markup=keyboard
    )
    
    # Сохраняем ID служебного сообщения
    if user_id not in last_service_messages:
        last_service_messages[user_id] = []
    last_service_messages[user_id].append(sent_msg.message_id)

async def newgroup_command(update: Update, context):
    """Создание нового группового чата"""
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user{user_id}"
    
    # Удаляем сообщение
    try:
        await update.message.delete()
    except:
        pass
    
    # Показываем выбор модели для группы
    creating_group[user_id] = True
    
    models_text = """🎯 **Создание группового чата**

Выберите модель AI для группы:

Все участники будут использовать эту модель для общения.

👇 Выберите модель:"""
    
    # Формируем inline-кнопки с моделями (все модели, кроме auto-select)
    inline_keyboard = []
    
    # Группируем модели по 2 в ряд
    row = []
    for model_id, model_info in MODELS.items():
        # Пропускаем auto-select, так как для группы нужна конкретная модель
        if model_id == 'auto-select':
            continue
        
        button_text = f"{model_info['emoji']} {model_info['name']}"
        row.append(InlineKeyboardButton(button_text, callback_data=f"selectgroupmodel_{model_id}"))
        
        if len(row) == 2:
            inline_keyboard.append(row)
            row = []
    
    # Добавляем последний ряд, если есть
    if row:
        inline_keyboard.append(row)
    
    # Кнопка отмены
    inline_keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_creategroup")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard)
    
    sent_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=models_text,
        reply_markup=keyboard
    )
    
    # Сохраняем ID служебного сообщения
    if user_id not in last_service_messages:
        last_service_messages[user_id] = []
    last_service_messages[user_id].append(sent_msg.message_id)

async def joingroup_command(update: Update, context):
    """Команда /joingroup - присоединение к групповому чату"""
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user{user_id}"
    
    if not context.args:
        await update.message.reply_text("❌ Укажите ID группы: `/joingroup ID`")
        return
    
    group_id = context.args[0]
    
    if group_id not in group_chats:
        await update.message.reply_text("❌ Группа с таким ID не найдена")
        return
    
    success, message = add_member_to_group(group_id, user_id, username)
    
    if success:
        user_current_group[user_id] = group_id
        group_info = get_group_info(group_id)
        
        join_text = f"""✅ Вы присоединились к группе!

📝 **Название:** {group_info['title']}
🤖 **Модель:** {MODELS[group_info['model_id']]['name']}
👥 **Участников:** {len(group_info['members'])}/5

Теперь вы можете писать сообщения - все участники группы увидят их и ответы AI! 🚀"""
        
        await update.message.reply_text(join_text)
        
        # Уведомляем других участников
        for member_id in group_info['members']:
            if member_id != user_id:
                try:
                    await context.bot.send_message(
                        chat_id=member_id,
                        text=f"👤 **{username}** присоединился к группе **{group_info['title']}**"
                    )
                except:
                    pass
    else:
        await update.message.reply_text(f"❌ {message}")

async def leavegroup_command(update: Update, context):
    """Выход из группового чата"""
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user{user_id}"
    
    if user_id not in user_current_group:
        await update.message.reply_text("❌ Вы не находитесь ни в одной группе")
        return
    
    group_id = user_current_group[user_id]
    group_info = get_group_info(group_id)
    
    if not group_info:
        await update.message.reply_text("❌ Группа не найдена")
        return
    
    # Если это создатель - предлагаем завершить группу
    if user_id == group_info['creator_id']:
        confirm_text = f"""⚠️ **Вы создатель группы "{group_info['title']}"**

👥 В группе сейчас {len(group_info['members'])} участников

Что вы хотите сделать?"""
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Завершить группу", callback_data=f"deletegroup_{group_id}")],
            [InlineKeyboardButton("↩️ Отмена", callback_data="cancel_leave")]
        ])
        
        await update.message.reply_text(confirm_text, reply_markup=keyboard)
    else:
        # Обычный участник просто выходит
        success, message = remove_member_from_group(group_id, user_id)
        
        if success:
            del user_current_group[user_id]
            await update.message.reply_text(f"✅ Вы покинули группу **{group_info['title']}**")
            
            # Уведомляем всех остальных участников
            for member_id in group_info['members']:
                if member_id != user_id:
                    try:
                        await context.bot.send_message(
                            chat_id=member_id,
                            text=f"👋 **@{username}** покинул группу **{group_info['title']}**\n\n👥 Осталось участников: {len(group_info['members'])}/5"
                        )
                    except:
                        pass
        else:
            await update.message.reply_text(f"❌ {message}")

async def groupinfo_command(update: Update, context):
    """Команда /groupinfo - информация о текущей группе"""
    user_id = update.effective_user.id
    
    if user_id not in user_current_group:
        await update.message.reply_text("❌ Вы не находитесь ни в одной группе")
        return
    
    group_id = user_current_group[user_id]
    group_info = get_group_info(group_id)
    
    if not group_info:
        await update.message.reply_text("❌ Группа не найдена")
        return
    
    creator_username = group_info['member_usernames'].get(group_info['creator_id'], 'unknown')
    
    info_text = f"""📊 **Информация о группе**

📝 **Название:** {group_info['title']}
🆔 **ID:** `{group_id}`
👑 **Создатель:** @{creator_username}
🤖 **Модель:** {MODELS[group_info['model_id']]['name']}
👥 **Участников:** {len(group_info['members'])}/5
📅 **Создана:** {group_info['created_at'][:10]}
💬 **Сообщений:** {group_info.get('message_count', 0)}

**Участники:**"""
    
    for member_id in group_info['members']:
        member_username = group_info['member_usernames'].get(member_id, f'user{member_id}')
        is_creator = "👑" if member_id == group_info['creator_id'] else "👤"
        info_text += f"\n{is_creator} @{member_username}"
    
    info_text += "\n\n💡 **Команды:**\n"
    info_text += f"• `/invite @username` - пригласить участника\n"
    info_text += f"• `/leavegroup` - покинуть группу\n"
    info_text += f"• Поделитесь ID группы для приглашения: `/joingroup {group_id}`"
    
    await update.message.reply_text(info_text)

async def invite_command(update: Update, context):
    """Команда /invite - приглашение пользователя в группу"""
    user_id = update.effective_user.id
    
    if user_id not in user_current_group:
        await update.message.reply_text("❌ Вы не находитесь ни в одной группе")
        return
    
    group_id = user_current_group[user_id]
    group_info = get_group_info(group_id)
    
    if not group_info:
        await update.message.reply_text("❌ Группа не найдена")
        return
    
    # Только создатель может приглашать
    if user_id != group_info['creator_id']:
        await update.message.reply_text("❌ Только создатель группы может приглашать участников")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Укажите username: `/invite @username`")
        return
    
    invite_text = f"""📨 **Приглашение в группу**

Для приглашения пользователя отправьте ему этот текст:

---
👋 Вас приглашают в групповой чат **{group_info['title']}**!

🤖 Модель: {MODELS[group_info['model_id']]['name']}
👥 Участников: {len(group_info['members'])}/5

Чтобы присоединиться, отправьте команду:
`/joingroup {group_id}`
---

💡 Отправьте это сообщение пользователю {context.args[0]}"""
    
    await update.message.reply_text(invite_text)

async def handle_message(update: Update, context):
    """Обработка текстовых сообщений и кнопок"""
    try:
        user_id = update.effective_user.id
        username = update.effective_user.username or "unknown"
        first_name = update.effective_user.first_name or "Unknown"
        last_name = update.effective_user.last_name or ""
        
        # Обновляем информацию о пользователе
        update_user_info(user_id, username, first_name, last_name)
        
        # Проверка на спам
        is_spam, spam_message = check_spam(user_id)
        if is_spam:
            await update.message.reply_text(spam_message)
            return
        
        # Обработка данных из веб-приложения
        if update.message.web_app_data:
            import json
            try:
                logging.info(f"Получены данные из веб-приложения: {update.message.web_app_data.data}")
                data = json.loads(update.message.web_app_data.data)
                model_id = data.get('model_id')
                logging.info(f"Извлечен model_id: {model_id}")
                
                if model_id and model_id in MODELS:
                    # Сохраняем выбранную модель
                    user_models[user_id] = model_id
                    model_info = MODELS[model_id]
                    
                    # Создаем чат с выбранной моделью
                    keyboard = get_chat_keyboard()
                    
                    welcome_text = f"""✅ **Модель выбрана!**

🤖 **{model_info['emoji']} {model_info['name']}**
📝 **Описание:** {model_info['description']}

💬 **Чат создан!** Начните писать сообщения - бот запомнит контекст!
🔄 Хотите сменить модель? Нажмите "🤖 Модели" """
                    
                    await update.message.reply_text(
                        welcome_text,
                        reply_markup=keyboard
                    )
                    
                    logging.info(f"Пользователь {user_id} выбрал модель {model_id} через веб-приложение")
                    return
                else:
                    await update.message.reply_text("❌ Неверная модель. Попробуйте еще раз.")
                    return
            except Exception as e:
                logging.error(f"Ошибка обработки web_app_data: {e}")
                await update.message.reply_text("❌ Ошибка при выборе модели. Попробуйте еще раз.")
                return
        
        user_text = update.message.text
        logging.info(f"Получено сообщение от пользователя {user_id}: {user_text[:50]}...")
        
        # Обработка кнопок меню
        if user_text == "💬 Создать новый чат":
            # Проверяем, есть ли у пользователя выбранная модель
            if user_id in user_models:
                # Если модель выбрана, сразу создаем чат
                model_id = user_models[user_id]
                model_info = MODELS[model_id]
                
                keyboard = get_chat_keyboard()
                
                welcome_text = f"""💬 **Новый чат создан!**

🤖 **Модель:** {model_info['emoji']} {model_info['name']}
📝 **Описание:** {model_info['description']}

💡 Начните писать сообщения - бот запомнит контекст!
🔄 Хотите сменить модель? Нажмите "🤖 Модели" """
                
                await update.message.reply_text(
                    welcome_text,
                    reply_markup=keyboard
                )
            else:
                # Если модель не выбрана, показываем Reply кнопки с веб-приложением
                chat_keyboard = get_chat_keyboard()
                
                await update.message.reply_text(
                    "💬 **Чат готов к созданию!**\n\n"
                    "⚠️ Сначала выберите модель через кнопку \"🤖 Модели\" ниже.\n\n"
                    "После выбора модели чат будет автоматически создан!",
                    reply_markup=chat_keyboard
                )
            return
        elif user_text == "📂 Ваши чаты":
            # Удаляем все предыдущие служебные сообщения
            await delete_service_messages(context, update.effective_chat.id, user_id)
            await my_chats_command(update, context)
            return
        elif user_text == "👥 Группы":
            # Удаляем все предыдущие служебные сообщения
            await delete_service_messages(context, update.effective_chat.id, user_id)
            await groups_command(update, context)
            return
        elif user_text == "➕ Создать группу":
            # Удаляем все предыдущие служебные сообщения
            await delete_service_messages(context, update.effective_chat.id, user_id)
            await newgroup_command(update, context)
            return
        elif user_text == "📂 Мои группы":
            # Показываем список всех групп пользователя
            await delete_service_messages(context, update.effective_chat.id, user_id)
            
            user_groups = get_user_groups(user_id)
            
            if not user_groups:
                groups_text = """📂 **Мои группы**

У вас пока нет групповых чатов.

👇 Создайте свою первую группу!"""
                
                keyboard = ReplyKeyboardMarkup([
                    [KeyboardButton("➕ Создать группу")],
                    [KeyboardButton("◀️ Главное меню")]
                ], resize_keyboard=True)
                
                await update.message.reply_text(groups_text, reply_markup=keyboard)
            else:
                groups_text = "📂 **Мои группы**\n\n"
                
                # Формируем inline-кнопки для каждой группы
                inline_keyboard = []
                
                for group in user_groups:
                    creator_mark = "👑" if group['is_creator'] else "👤"
                    groups_text += f"{creator_mark} **{group['title']}**\n"
                    groups_text += f"   🤖 {group['model']}\n"
                    groups_text += f"   👥 {group['members_count']}/5 участников\n\n"
                    
                    # Добавляем кнопку для открытия группы
                    inline_keyboard.append([
                        InlineKeyboardButton(
                            f"{creator_mark} {group['title']}",
                            callback_data=f"opengroup_{group['group_id']}"
                        )
                    ])
                
                groups_text += "\n💡 Нажмите на группу чтобы открыть её"
                
                # Inline клавиатура
                inline_markup = InlineKeyboardMarkup(inline_keyboard)
                
                # Обычная клавиатура с кнопками
                keyboard = ReplyKeyboardMarkup([
                    [KeyboardButton("➕ Создать группу")],
                    [KeyboardButton("◀️ Главное меню")]
                ], resize_keyboard=True)
                
                await update.message.reply_text(groups_text, reply_markup=inline_markup)
                
                # Отправляем второе сообщение с обычной клавиатурой
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Используйте кнопки ниже:",
                    reply_markup=keyboard
                )
            return
        
        # ========================================
        # 👥 ОБРАБОТЧИКИ REPLY-КНОПОК ГРУПП
        # ========================================
        elif user_text == "📊 Инфо о группе":
            # Показываем информацию о текущей группе
            if user_id in user_current_group:
                group_id = user_current_group[user_id]
                group_info = get_group_info(group_id)
                
                if group_info:
                    creator_username = group_info['member_usernames'].get(group_info['creator_id'], 'unknown')
                    
                    info_text = f"""📊 **Информация о группе**

📝 **Название:** {group_info['title']}
🆔 **ID:** `{group_id}`
👑 **Создатель:** @{creator_username}
🤖 **Модель:** {MODELS[group_info['model_id']]['name']}
👥 **Участников:** {len(group_info['members'])}/5
📅 **Создана:** {group_info['created_at'][:10]}
💬 **Сообщений:** {group_info.get('message_count', 0)}

**Участники:**"""
                    
                    for member_id in group_info['members']:
                        member_username = group_info['member_usernames'].get(member_id, f'user{member_id}')
                        is_member_creator = "👑" if member_id == group_info['creator_id'] else "👤"
                        info_text += f"\n{is_member_creator} @{member_username}"
                    
                    # Кнопка "Назад"
                    keyboard = ReplyKeyboardMarkup([
                        [KeyboardButton("◀️ Назад")]
                    ], resize_keyboard=True)
                    
                    await update.message.reply_text(info_text, reply_markup=keyboard)
                else:
                    await update.message.reply_text("❌ Группа не найдена")
            else:
                await update.message.reply_text("❌ Вы не находитесь в групповом чате")
            return
        
        elif user_text == "🔗 Поделиться ссылкой":
            # Отправляем ссылку-приглашение
            if user_id in user_current_group:
                group_id = user_current_group[user_id]
                group_info = get_group_info(group_id)
                
                if group_info and user_id == group_info['creator_id']:
                    bot_username = (await context.bot.get_me()).username
                    invite_link = f"https://t.me/{bot_username}?start=join_{group_id}"
                    
                    invite_text = f"""🔗 Ссылка-приглашение в группу

📝 Группа: {group_info['title']}
🤖 Модель: {MODELS[group_info['model_id']]['name']}
👥 Участников: {len(group_info['members'])}/5

Ссылка для приглашения:
{invite_link}

💡 Как пригласить друзей:
1. Нажмите на ссылку выше (долгий тап/клик)
2. Выберите "Копировать"
3. Отправьте ссылку друзьям в Telegram

Когда они перейдут по ссылке, автоматически присоединятся к группе!"""
                    
                    await update.message.reply_text(invite_text)
                else:
                    await update.message.reply_text("❌ Только создатель может получить ссылку-приглашение")
            else:
                await update.message.reply_text("❌ Вы не находитесь в групповом чате")
            return
        
        elif user_text == "❌ Завершить группу":
            # Завершение группы (только для создателя)
            if user_id in user_current_group:
                group_id = user_current_group[user_id]
                group_info = get_group_info(group_id)
                
                if group_info and user_id == group_info['creator_id']:
                    # Уведомляем всех участников
                    for member_id in group_info['members']:
                        try:
                            await context.bot.send_message(
                                chat_id=member_id,
                                text=f"🔴 **Группа \"{group_info['title']}\" завершена**\n\nСоздатель завершил групповой чат."
                            )
                        except:
                            pass
                        
                        # Удаляем группу из активных для всех
                        if member_id in user_current_group and user_current_group[member_id] == group_id:
                            del user_current_group[member_id]
                    
                    # Удаляем группу
                    delete_group_chat(group_id)
                    
                    # Отправляем в главное меню
                    keyboard = get_main_keyboard()
                    await update.message.reply_text("✅ Группа завершена и удалена", reply_markup=keyboard)
                else:
                    await update.message.reply_text("❌ Только создатель может завершить группу")
            else:
                await update.message.reply_text("❌ Вы не находитесь в групповом чате")
            return
        
        elif user_text == "🚪 Выйти из группы":
            # Выход из группы (для участников)
            if user_id in user_current_group:
                group_id = user_current_group[user_id]
                group_info = get_group_info(group_id)
                
                if group_info and user_id != group_info['creator_id']:
                    # Удаляем пользователя из группы
                    success, message = remove_member_from_group(group_id, user_id)
                    
                    if success:
                        # Удаляем группу из активных
                        if user_id in user_current_group:
                            del user_current_group[user_id]
                        
                        # Уведомляем других участников
                        for member_id in group_info['members']:
                            if member_id != user_id:
                                try:
                                    await context.bot.send_message(
                                        chat_id=member_id,
                                        text=f"👤 **@{username}** покинул группу **{group_info['title']}**\n\n👥 Теперь участников: {len(group_info['members'])}/5"
                                    )
                                except:
                                    pass
                        
                        # Отправляем в главное меню
                        keyboard = get_main_keyboard()
                        await update.message.reply_text("✅ Вы покинули группу", reply_markup=keyboard)
                    else:
                        await update.message.reply_text(f"❌ {message}")
                else:
                    await update.message.reply_text("❌ Создатель не может покинуть группу. Используйте 'Завершить группу'")
            else:
                await update.message.reply_text("❌ Вы не находитесь в групповом чате")
            return
        
        elif user_text == "◀️ Главное меню":
            # Выход в главное меню из группы
            if user_id in user_current_group:
                group_id = user_current_group[user_id]
                group_info = get_group_info(group_id)
                
                if group_info:
                    is_creator = user_id == group_info['creator_id']
                    
                    if is_creator:
                        # Для создателя - предложить завершить группу или просто выйти в главное меню
                        confirm_text = f"""⚠️ **Выход в главное меню**

Вы создатель группы **"{group_info['title']}"**

Что вы хотите сделать?"""
                        
                        keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("◀️ Выйти в меню", callback_data=f"exittomenu_{group_id}")],
                            [InlineKeyboardButton("❌ Завершить группу", callback_data=f"deletegroup_{group_id}")]
                        ])
                    else:
                        # Для участника - предложить покинуть группу или просто выйти в главное меню
                        confirm_text = f"""⚠️ **Выход в главное меню**

Вы участник группы **"{group_info['title']}"**

Что вы хотите сделать?"""
                        
                        keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("◀️ Выйти в меню", callback_data=f"exittomenu_{group_id}")],
                            [InlineKeyboardButton("🚪 Покинуть группу", callback_data=f"confirmleave_{group_id}")]
                        ])
                    
                    await update.message.reply_text(confirm_text, reply_markup=keyboard)
                else:
                    await update.message.reply_text("❌ Группа не найдена")
            else:
                # Обычный выход в главное меню (из чата с ИИ)
                # Сохраняем чат, если есть сообщения
                if user_id in user_conversations and len(user_conversations[user_id]) > 0:
                    # Сохраняем чат в user_all_chats для "Мои чаты"
                    import time
                    chat_id = str(int(time.time()))
                    
                    if user_id not in user_all_chats:
                        user_all_chats[user_id] = {}
                    
                    # Получаем название модели
                    model_id = user_models.get(user_id, 'unknown')
                    model_name = MODELS.get(model_id, {}).get('name', 'Unknown')
                    
                    user_all_chats[user_id][chat_id] = {
                        'messages': user_conversations[user_id].copy(),
                        'model': model_id,
                        'created_at': chat_id,
                        'title': f"{model_name} - {len(user_conversations[user_id])} сообщений",
                        'message_count': len(user_conversations[user_id])
                    }
                    
                    # Сохраняем в файл
                    save_user_chats(user_id, username)
                    logging.info(f"Чат {chat_id} сохранен для пользователя {user_id}")
                    
                    # Очищаем текущий чат
                    del user_conversations[user_id]
                    
                    await update.message.reply_text(
                        "💾 **Чат сохранен**\n\n"
                        "Вы можете продолжить его позже через \"📂 Ваши чаты\""
                    )
                else:
                    # Пустой чат не сохраняем
                    if user_id in user_conversations:
                        del user_conversations[user_id]
                
                keyboard = get_main_keyboard()
                await update.message.reply_text("🏠 **Главное меню**", reply_markup=keyboard)
            return
        
        
        elif user_text == "◀️ Назад":
            # Возврат из инфо о группе
            if user_id in user_current_group:
                group_id = user_current_group[user_id]
                group_info = get_group_info(group_id)
                
                if group_info:
                    is_creator = user_id == group_info['creator_id']
                    
                    if is_creator:
                        keyboard = ReplyKeyboardMarkup([
                            [KeyboardButton("📊 Инфо о группе"), KeyboardButton("🔗 Поделиться ссылкой")],
                            [KeyboardButton("❌ Завершить группу"), KeyboardButton("◀️ Главное меню")]
                        ], resize_keyboard=True)
                    else:
                        keyboard = ReplyKeyboardMarkup([
                            [KeyboardButton("📊 Инфо о группе"), KeyboardButton("🚪 Выйти из группы")],
                            [KeyboardButton("◀️ Главное меню")]
                        ], resize_keyboard=True)
                    
                    await update.message.reply_text("💬 **Групповой чат: " + group_info['title'] + "**\n\n🤖 **Модель:** " + MODELS[group_info['model_id']]['name'] + "\n👥 **Участников:** " + str(len(group_info['members'])) + "/5\n\nВыберите действие:", reply_markup=keyboard)
                else:
                    keyboard = get_main_keyboard()
                    await update.message.reply_text("🏠 **Главное меню**", reply_markup=keyboard)
            else:
                keyboard = get_main_keyboard()
                await update.message.reply_text("🏠 **Главное меню**", reply_markup=keyboard)
            return
        
        # 🤖 ОБРАБОТКА ВЫБОРА МОДЕЛИ ЧЕРЕЗ REPLY КНОПКИ
        # Проверяем, соответствует ли текст какой-либо модели
        selected_model_id = None
        for model_id, model_info in MODELS.items():
            # Проверяем оба варианта: с галочкой и без
            button_text = f"{model_info['emoji']} {model_info['name']}"
            button_text_checked = f"{button_text} ✓"
            
            # Для Auto-Select также проверяем вариант с рамкой
            if model_id == 'auto-select':
                button_text_special = f"▪️ {button_text} ▪️"
                if user_text in [button_text, button_text_checked, button_text_special, f"{button_text_special} ✓"]:
                    selected_model_id = model_id
                    break
            elif user_text in [button_text, button_text_checked]:
                selected_model_id = model_id
                break
        
        if selected_model_id:
            # Удаляем сообщение пользователя
            try:
                await update.message.delete()
            except:
                pass
            
            # Удаляем все предыдущие служебные сообщения
            await delete_service_messages(context, update.effective_chat.id, user_id)
            
            # Сохраняем выбранную модель
            user_models[user_id] = selected_model_id
            model_info = MODELS[selected_model_id]
            
            # Создаем новый чат с выбранной моделью
            create_new_chat(user_id, username, selected_model_id)
            
            # Создаем клавиатуру чата
            keyboard = get_chat_keyboard()
            
            # Отправляем сообщение с созданием чата
            welcome_text = f"""✅ **Модель выбрана!**

🤖 **{model_info['emoji']} {model_info['name']}**
📝 **Описание:** {model_info['description']}

💬 **Чат создан!** Начните писать сообщения - бот запомнит контекст!
🔄 Хотите сменить модель? Нажмите "🤖 Модели" """
            
            sent_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=welcome_text,
                reply_markup=keyboard
            )
            
            # НЕ сохраняем ID приветственного сообщения, чтобы оно не удалялось
            # Кнопки останутся видимыми постоянно
            
            logging.info(f"Пользователь {user_id} выбрал модель {selected_model_id} через Reply кнопку")
            return
        
        elif user_text == "📊 Статус":
            # Удаляем все предыдущие служебные сообщения
            await delete_service_messages(context, update.effective_chat.id, user_id)
            await status_command(update, context)
            return
        elif user_text == "🤖 Модели":
            # Открываем веб-приложение для выбора модели
            webapp_url = "https://xfusionai.netlify.app/"
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Открыть выбор моделей", web_app=WebAppInfo(url=webapp_url))]
            ])
            
            await update.message.reply_text(
                "🤖 **Выбор AI модели**\n\n"
                "Нажмите кнопку ниже, чтобы открыть веб-приложение с выбором моделей:",
                reply_markup=keyboard
            )
            return
        elif user_text == "❓ Помощь":
            # Удаляем все предыдущие служебные сообщения
            await delete_service_messages(context, update.effective_chat.id, user_id)
            await help_command(update, context)
            return
        elif user_text == "◀️ Главное меню":
            # Проверяем, является ли пользователь создателем активной группы
            if user_id in user_current_group:
                group_id = user_current_group[user_id]
                group_info = get_group_info(group_id)
                
                if group_info and user_id == group_info['creator_id']:
                    # Создатель группы - спрашиваем о завершении
                    confirm_text = f"""⚠️ **Вы создатель группы "{group_info['title']}"**

👥 В группе сейчас {len(group_info['members'])} участников

Что вы хотите сделать?"""
                    
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Завершить группу", callback_data=f"deletegroup_{group_id}")],
                        [InlineKeyboardButton("↩️ Выйти в главное меню", callback_data="forcemainmenu")]
                    ])
                    
                    await update.message.reply_text(confirm_text, reply_markup=keyboard)
                    return
            
            # Сбрасываем флаг ожидания ID чата
            if user_id in awaiting_chat_id:
                awaiting_chat_id[user_id] = False
            
            # Сбрасываем активную группу
            if user_id in user_current_group:
                del user_current_group[user_id]
            
            # Завершаем текущий чат, если он активен
            await finalize_chat(user_id, username, context, update.effective_chat.id)
            
            # Удаляем сообщение с кнопкой и список моделей
            try:
                await update.message.delete()
            except:
                pass
            
            # Удаляем все служебные сообщения
            await delete_service_messages(context, update.effective_chat.id, user_id)
            
            keyboard = get_main_keyboard()
            sent_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="📋 **Главное меню**\n\nВыберите действие:",
                reply_markup=keyboard
            )
            
            # Сохраняем ID служебного сообщения
            if user_id not in last_service_messages:
                last_service_messages[user_id] = []
            last_service_messages[user_id].append(sent_msg.message_id)
            return
        
        # 🤝 ОБРАБОТКА ГРУППОВЫХ ЧАТОВ
        if user_id in user_current_group:
            group_id = user_current_group[user_id]
            group_info = get_group_info(group_id)
            
            if group_info:
                # Показываем индикатор "печатает..."
                await update.message.chat.send_action(action="typing")
                
                # Инициализируем историю группы
                if group_id not in group_conversations:
                    group_conversations[group_id] = []
                
                # Получаем ответ от модели группы
                model_id = group_info['model_id']
                model_info = MODELS.get(model_id)
                
                # Формируем историю для AI (только последние 40 сообщений)
                conversation_history = []
                for entry in group_conversations[group_id][-40:]:
                    if 'user_message' in entry:
                        conversation_history.append({'role': 'user', 'text': entry['user_message']})
                    if 'ai_response' in entry:
                        conversation_history.append({'role': 'assistant', 'text': entry['ai_response']})
                
                # Получаем ответ от AI
                model_func = model_info['function']
                response = model_func(user_text, conversation_history)
                
                # Сохраняем в историю группы
                group_conversations[group_id].append({
                    'user_id': user_id,
                    'username': username,
                    'user_message': user_text,
                    'ai_response': response,
                    'timestamp': datetime.now().isoformat()
                })
                
                # Обновляем счетчик сообщений
                group_info['message_count'] = group_info.get('message_count', 0) + 1
                save_group_chat(group_id)
                
                # Форматируем ответ
                try:
                    formatted_response = format_ai_response(response)
                    group_response = f"👤 **@{username}:** {user_text}\n\n🤖 **AI:** {formatted_response}"
                except Exception:
                    group_response = f"👤 **@{username}:** {user_text}\n\n🤖 **AI:** {response}"
                
                # Отправляем ответ всем участникам группы
                for member_id in group_info['members']:
                    try:
                        if member_id == user_id:
                            # Отправителю отправляем как ответ на его сообщение
                            await update.message.reply_text(
                                group_response,
                                parse_mode="Markdown"
                            )
                        else:
                            # Остальным участникам отправляем отдельным сообщением
                            await context.bot.send_message(
                                chat_id=member_id,
                                text=f"💬 **Группа: {group_info['title']}**\n\n{group_response}",
                                parse_mode="Markdown"
                            )
                    except Exception as e:
                        logging.error(f"Ошибка отправки сообщения участнику {member_id}: {e}")
                        # Если не удалось с Markdown, пробуем без него
                        try:
                            if member_id == user_id:
                                await update.message.reply_text(group_response)
                            else:
                                await context.bot.send_message(
                                    chat_id=member_id,
                                    text=f"💬 Группа: {group_info['title']}\n\n{group_response}"
                                )
                        except:
                            pass
                
                return
        
        # Обычное сообщение пользователя - общение с ИИ
        # Проверяем, выбрана ли модель
        if user_id not in user_models:
            # Показываем Reply кнопки для выбора модели
            chat_keyboard = get_chat_keyboard()
            
            await update.message.reply_text(
                "⚠️ **Сначала выберите модель!**\n\n"
                "Нажмите кнопку \"🤖 Модели\" внизу, чтобы выбрать ИИ-модель для начала работы.",
                reply_markup=chat_keyboard
            )
            return
        
        # Удаляем только служебные сообщения (НЕ сообщение о модели!)
        await delete_service_messages(context, update.effective_chat.id, user_id)
        
        # Инициализируем историю для нового пользователя
        if user_id not in user_conversations:
            user_conversations[user_id] = []
        
        # Показываем индикатор "печатает..."
        await update.message.chat.send_action(action="typing")
        
        # Логируем сообщение пользователя
        log_user_message(user_id, username, user_text, is_bot=False)
        
        # Получаем ответ от выбранной модели с историей
        model_info = MODELS.get(user_models[user_id])
        model_func = model_info['function']
        response = model_func(user_text, user_conversations[user_id])
        
        # Логируем ответ бота
        log_user_message(user_id, username, response, is_bot=True)
        
        # Сохраняем сообщение пользователя и ответ бота в историю
        user_conversations[user_id].append({'role': 'user', 'text': user_text})
        user_conversations[user_id].append({'role': 'assistant', 'text': response})
        
        # Ограничиваем историю последними 40 сообщениями (20 пар вопрос-ответ)
        if len(user_conversations[user_id]) > 40:
            user_conversations[user_id] = user_conversations[user_id][-40:]
        
        # Форматируем и отправляем ответ с обработкой ошибок форматирования
        # НЕ передаем reply_markup, чтобы клавиатура не открывалась автоматически
        try:
            # Форматируем ответ для лучшей читаемости
            formatted_response = format_ai_response(response)
            
            # Отправляем ответ пользователю с Markdown форматированием (без клавиатуры)
            await update.message.reply_text(
                formatted_response,
                parse_mode="Markdown"
            )
        except Exception as format_error:
            # Если форматирование не удалось, отправляем без форматирования
            logging.warning(f"Ошибка форматирования Markdown: {format_error}")
        await update.message.reply_text(response)
        
    except Exception as e:
        logging.error(f"Ошибка при обработке сообщения: {e}")
        await update.message.reply_text("Произошла ошибка. Попробуйте еще раз.")

async def handle_callback_query(update: Update, context):
    """Обработка нажатий на inline-кнопки"""
    query = update.callback_query
    user_id = query.from_user.id
    username = query.from_user.username or "unknown"
    
    await query.answer()  # Подтверждаем нажатие
    
    # Обработка кнопки возврата в главное меню
    if query.data == "back_to_main":
        keyboard = get_main_keyboard()
        await query.edit_message_text("🏠 **Главное меню**", reply_markup=keyboard)
        return
    
    # Обработка кнопок групп
    if query.data.startswith("selectgroupmodel_"):
        model_id = query.data.replace("selectgroupmodel_", "")
        
        if model_id not in MODELS:
            await query.answer("❌ Модель не найдена", show_alert=True)
            return
        
        # Создаем группу с выбранной моделью
        group_id = create_group_chat(user_id, username, model_id, None)
        
        # Устанавливаем текущую группу для пользователя
        user_current_group[user_id] = group_id
        
        # Сбрасываем флаг создания
        if user_id in creating_group:
            del creating_group[user_id]
        
        group_info = get_group_info(group_id)
        bot_username = (await context.bot.get_me()).username
        
        # Создаем ссылку для приглашения
        invite_link = f"https://t.me/{bot_username}?start=join_{group_id}"
        
        success_text = f"""✅ **Групповой чат создан!**

📝 **Название:** {group_info['title']}
🤖 **Модель:** {MODELS[model_id]['name']}
👥 **Участников:** 1/5

🔗 **Ссылка-приглашение:**
{invite_link}

💡 Отправьте эту ссылку друзьям - они смогут присоединиться одним нажатием!

Теперь можете писать сообщения - все участники группы увидят их и ответы AI! 🚀"""
        
        # Inline-кнопки для управления группой
        inline_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Инфо о группе", callback_data=f"groupinfo_{group_id}")],
            [InlineKeyboardButton("🔗 Поделиться ссылкой", switch_inline_query=invite_link)],
            [InlineKeyboardButton("❌ Завершить группу", callback_data=f"deletegroup_{group_id}")]
        ])
        
        # Только одна Reply-кнопка "Главное меню"
        reply_keyboard = ReplyKeyboardMarkup([
            [KeyboardButton("◀️ Главное меню")]
        ], resize_keyboard=True)
        
        try:
            await query.message.edit_text(success_text, reply_markup=inline_keyboard)
        except:
            pass
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Выберите действие:",
            reply_markup=reply_keyboard
        )
        
        return
    
    elif query.data == "cancel_creategroup":
        # Отмена создания группы
        if user_id in creating_group:
            del creating_group[user_id]
        
        try:
            await query.message.edit_text("❌ Создание группы отменено")
        except:
            await query.message.reply_text("❌ Создание группы отменено")
        
        # Возвращаем в меню групп
        groups_text = """👥 **Групповые чаты**

Создавайте групповые чаты с друзьями и общайтесь с AI вместе!

👇 Выберите действие:"""
        
        # Обычная клавиатура с кнопками
        buttons = []
        buttons.append([KeyboardButton("➕ Создать группу")])
        
        # Получаем группы пользователя для кнопки "Мои группы"
        user_groups = get_user_groups(user_id)
        if user_groups:
            buttons.append([KeyboardButton("📂 Мои группы")])
        
        buttons.append([KeyboardButton("◀️ Главное меню")])
        
        keyboard = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=groups_text,
            reply_markup=keyboard
        )
        return
    
    elif query.data.startswith("opengroup_"):
        group_id = query.data.split('_', 1)[1]
        group_info = get_group_info(group_id)
        
        if not group_info or user_id not in group_info['members']:
            await query.message.edit_text("❌ Группа не найдена или вы не являетесь участником")
            return
        
        # Активируем группу для пользователя
        user_current_group[user_id] = group_id
        
        is_creator = user_id == group_info['creator_id']
        
        # Получаем ссылку-приглашение
        bot_username = (await context.bot.get_me()).username
        invite_link = f"https://t.me/{bot_username}?start=join_{group_id}"
        
        menu_text = f"""✅ **Группа активна!**

💬 **Групповой чат: {group_info['title']}**

🤖 **Модель:** {MODELS[group_info['model_id']]['name']}
👥 **Участников:** {len(group_info['members'])}/5

Теперь можете писать сообщения - все участники увидят их!

Выберите действие:"""
        
        # Inline-кнопки управления группой
        if is_creator:
            inline_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Инфо о группе", callback_data=f"groupinfo_{group_id}")],
                [InlineKeyboardButton("🔗 Поделиться ссылкой", switch_inline_query=invite_link)],
                [InlineKeyboardButton("❌ Завершить группу", callback_data=f"deletegroup_{group_id}")]
            ])
        else:
            inline_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Инфо о группе", callback_data=f"groupinfo_{group_id}")],
                [InlineKeyboardButton("🚪 Выйти из группы", callback_data=f"confirmleave_{group_id}")]
            ])
        
        # Только одна Reply-кнопка "Главное меню"
        reply_keyboard = ReplyKeyboardMarkup([
            [KeyboardButton("◀️ Главное меню")]
        ], resize_keyboard=True)
        
        try:
            await query.message.edit_text(menu_text, reply_markup=inline_keyboard)
        except:
            pass
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Выберите действие:",
            reply_markup=reply_keyboard
        )
        
        return
    
    elif query.data.startswith("deletegroup_"):
        group_id = query.data.split('_', 1)[1]
        group_info = get_group_info(group_id)
        
        if not group_info:
            await query.message.edit_text("❌ Группа не найдена")
            return
        
        # Только создатель может завершить группу
        if user_id != group_info['creator_id']:
            await query.answer("❌ Только создатель может завершить группу", show_alert=True)
            return
        
        # Уведомляем всех участников
        for member_id in group_info['members']:
            try:
                await context.bot.send_message(
                    chat_id=member_id,
                    text=f"🔴 **Группа \"{group_info['title']}\" завершена**\n\nСоздатель завершил групповой чат."
                )
            except:
                pass
            
            # Удаляем группу из активных для всех
            if member_id in user_current_group and user_current_group[member_id] == group_id:
                del user_current_group[member_id]
        
        # Удаляем группу
        delete_group_chat(group_id)
        
        # Уведомление о завершении
        try:
            await query.message.edit_text("✅ Группа завершена и удалена")
        except:
            await query.message.reply_text("✅ Группа завершена и удалена")
        
        # Отправляем создателю главное меню
        keyboard = get_main_keyboard()
        
        welcome_text = """🏠 **Главное меню**

Группа успешно завершена. Выберите действие:"""
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=welcome_text,
                reply_markup=keyboard
            )
        except Exception as e:
            logging.error(f"Ошибка отправки главного меню после завершения группы: {e}")
        
        return
    
    elif query.data.startswith("groupinfo_"):
        group_id = query.data.split('_', 1)[1]
        group_info = get_group_info(group_id)
        
        if not group_info:
            await query.message.edit_text("❌ Группа не найдена")
            return
        
        creator_username = group_info['member_usernames'].get(group_info['creator_id'], 'unknown')
        
        info_text = f"""📊 **Информация о группе**

📝 **Название:** {group_info['title']}
🆔 **ID:** `{group_id}`
👑 **Создатель:** @{creator_username}
🤖 **Модель:** {MODELS[group_info['model_id']]['name']}
👥 **Участников:** {len(group_info['members'])}/5
📅 **Создана:** {group_info['created_at'][:10]}
💬 **Сообщений:** {group_info.get('message_count', 0)}

**Участники:**"""
        
        for member_id in group_info['members']:
            member_username = group_info['member_usernames'].get(member_id, f'user{member_id}')
            is_creator = "👑" if member_id == group_info['creator_id'] else "👤"
            info_text += f"\n{is_creator} @{member_username}"
        
        # Кнопка "Назад"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад", callback_data=f"groupmenu_{group_id}")]
        ])
        
        try:
            await query.message.edit_text(info_text, reply_markup=keyboard)
        except:
            await query.message.reply_text(info_text, reply_markup=keyboard)
        
        return
    
    elif query.data.startswith("groupmenu_"):
        # Возврат из инфо о группе к меню управления
        group_id = query.data.split('_', 1)[1]
        group_info = get_group_info(group_id)
        
        if not group_info:
            await query.message.edit_text("❌ Группа не найдена")
            return
        
        is_creator = user_id == group_info['creator_id']
        
        # Получаем ссылку-приглашение
        bot_username = (await context.bot.get_me()).username
        invite_link = f"https://t.me/{bot_username}?start=join_{group_id}"
        
        menu_text = f"""✅ **Группа активна!**

💬 **Групповой чат: {group_info['title']}**

🤖 **Модель:** {MODELS[group_info['model_id']]['name']}
👥 **Участников:** {len(group_info['members'])}/5

Теперь можете писать сообщения - все участники увидят их!

Выберите действие:"""
        
        # Inline-кнопки управления группой
        if is_creator:
            inline_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Инфо о группе", callback_data=f"groupinfo_{group_id}")],
                [InlineKeyboardButton("🔗 Поделиться ссылкой", switch_inline_query=invite_link)],
                [InlineKeyboardButton("❌ Завершить группу", callback_data=f"deletegroup_{group_id}")]
            ])
        else:
            inline_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Инфо о группе", callback_data=f"groupinfo_{group_id}")],
                [InlineKeyboardButton("🚪 Выйти из группы", callback_data=f"confirmleave_{group_id}")]
            ])
        
        try:
            await query.message.edit_text(menu_text, reply_markup=inline_keyboard)
        except:
            pass
        
        return
    
    elif query.data.startswith("confirmleave_"):
        group_id = query.data.split('_', 1)[1]
        group_info = get_group_info(group_id)
        
        if not group_info:
            await query.message.edit_text("❌ Группа не найдена")
            return
        
        # Участник выходит
        success, message = remove_member_from_group(group_id, user_id)
        
        if success:
            if user_id in user_current_group:
                del user_current_group[user_id]
            
            try:
                await query.message.edit_text(f"✅ Вы покинули группу **{group_info['title']}**")
            except:
                await query.message.reply_text(f"✅ Вы покинули группу **{group_info['title']}**")
            
            # Уведомляем остальных участников
            for member_id in group_info['members']:
                if member_id != user_id:
                    try:
                        await context.bot.send_message(
                            chat_id=member_id,
                            text=f"👋 **@{username}** покинул группу **{group_info['title']}**\n\n👥 Осталось участников: {len(group_info['members'])}/5"
                        )
                    except:
                        pass
        else:
            await query.message.reply_text(f"❌ {message}")
        
        return
    
    elif query.data.startswith("exittomenu_"):
        # Выход в главное меню без удаления/покидания группы
        group_id = query.data.split('_', 1)[1]
        
        if user_id in user_current_group and user_current_group[user_id] == group_id:
            del user_current_group[user_id]
        
        keyboard = get_main_keyboard()
        
        try:
            await query.message.edit_text("🏠 **Главное меню**\n\nВы вышли из группового чата. Группа сохранена и доступна в разделе \"👥 Группы\" → \"Мои группы\".")
        except:
            await query.message.reply_text("🏠 **Главное меню**\n\nВы вышли из группового чата. Группа сохранена и доступна в разделе \"👥 Группы\" → \"Мои группы\".")
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Выберите действие:",
            reply_markup=keyboard
        )
        return
    
    elif query.data == "cancel_leave":
        try:
            await query.message.edit_text("↩️ Отменено")
        except:
            await query.message.reply_text("↩️ Отменено")
        return
    
    elif query.data == "forcemainmenu":
        # Принудительный выход в главное меню (создатель группы остается в группе)
        if user_id in user_current_group:
            del user_current_group[user_id]
        
        keyboard = get_main_keyboard()
        
        try:
            await query.message.edit_text("📋 **Главное меню**\n\nВыберите действие:")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Используйте меню ниже:",
                reply_markup=keyboard
            )
        except:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📋 **Главное меню**\n\nВыберите действие:",
                reply_markup=keyboard
            )
        
        return
    
    # Парсим callback_data для чатов
    action, chat_id = query.data.split('_', 1)
    
    if action == "open":
        # Перейти к чату
        await delete_service_messages(context, query.message.chat_id, user_id)
        
        chat_data = await load_chat_by_id(user_id, username, chat_id)
        
        if chat_data:
            title = chat_data.get('title', 'Новый диалог')
            if not title:
                title = "Новый диалог"
            model_name = chat_data.get('model_name', 'Неизвестно')
            msg_count = chat_data.get('message_count', 0)
            
            keyboard = get_chat_keyboard()
            sent_msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ **Чат загружен!**\n\n"
                     f"📝 {title}\n"
                     f"🤖 Модель: {model_name}\n"
                     f"💬 Сообщений в чате: {msg_count}\n"
                     f"🧠 Загружено в контекст: {len(user_conversations.get(user_id, []))} последних сообщений\n\n"
                     f"Можете продолжить общение!",
                reply_markup=keyboard
            )
            
            # Сохраняем ID сообщения о модели
            model_status_messages[user_id] = sent_msg.message_id
            
            logging.info(f"Пользователь {user_id} загрузил чат {chat_id}")
        else:
            await query.message.reply_text("❌ Чат не найден или был удален.")
    
    elif action == "delete":
        # Удалить чат
        # Загружаем чаты пользователя
        if user_id not in user_all_chats:
            load_user_chats(user_id)
        
        if user_id in user_all_chats and chat_id in user_all_chats[user_id]:
            chat_title = user_all_chats[user_id][chat_id].get('title', 'Новый диалог')
            
            # Удаляем чат
            del user_all_chats[user_id][chat_id]
            save_user_chats(user_id, username)
            
            # Удаляем сообщение с чатом
            try:
                await query.message.delete()
            except:
                pass
            
            # Отправляем уведомление
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"🗑️ **Чат удален:**\n📝 {chat_title}"
            )
            
            logging.info(f"Пользователь {user_id} удалил чат {chat_id}")
        else:
            await query.message.reply_text("❌ Чат не найден.")

async def error_handler(update, context):
    """Обработчик ошибок"""
    error = context.error
    logging.error(f"Ошибка: {error}")
    
    if isinstance(error, Conflict):
        logging.error("Конфликт: Убедитесь, что запущен только один экземпляр бота")
    elif isinstance(error, NetworkError):
        logging.warning("Сетевая ошибка: Переподключение...")

def main():
    """Основная функция запуска бота"""
    try:
        logging.info("🚀 Запуск ИИ-бота на базе Yandex GPT...")
        
        # Создаем HTTPXRequest с настройками
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30,
            read_timeout=30,
            write_timeout=30,
            pool_timeout=10
        )
        
        # Создаем приложение
        app = Application.builder().token(TOKEN).request(request).build()
        
        # Добавляем обработчики команд
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("status", status_command))
        app.add_handler(CommandHandler("clear", clear_command))
        app.add_handler(CommandHandler("models", models_command))
        
        # Добавляем админ-команды
        app.add_handler(CommandHandler("admin_stats", admin_stats))
        app.add_handler(CommandHandler("admin_users", admin_users))
        app.add_handler(CommandHandler("admin_chats", admin_chats))
        app.add_handler(CommandHandler("admin_chat", admin_chat))
        
        # Добавляем команды для групповых чатов
        app.add_handler(CommandHandler("newgroup", newgroup_command))
        app.add_handler(CommandHandler("joingroup", joingroup_command))
        app.add_handler(CommandHandler("leavegroup", leavegroup_command))
        app.add_handler(CommandHandler("groupinfo", groupinfo_command))
        app.add_handler(CommandHandler("invite", invite_command))
        
        # Команда для проверки доступных моделей Groq
        app.add_handler(CommandHandler("groqmodels", groq_models_command))
        
        # Загружаем групповые чаты
        load_group_chats()
        logging.info(f"📂 Загружено групповых чатов: {len(group_chats)}")
        
        # Создаем кастомный фильтр для веб-приложения
        class WebAppFilter(filters.MessageFilter):
            def filter(self, message):
                return message.web_app_data is not None
        
        # Добавляем обработчик веб-приложения (ВАЖНО: ДО текстовых сообщений!)
        app.add_handler(MessageHandler(WebAppFilter(), handle_message))
        
        # Добавляем обработчик текстовых сообщений (включая кнопки)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Добавляем обработчик inline-кнопок
        app.add_handler(CallbackQueryHandler(handle_callback_query))
        
        # Добавляем обработчик ошибок
        app.add_error_handler(error_handler)
        
        logging.info("✅ Бот запущен и готов к работе!")
        app.run_polling(
            drop_pending_updates=True,
            bootstrap_retries=5
        )
        
    except Exception as e:
        logging.error(f"Критическая ошибка при запуске бота: {e}")
        raise

if __name__ == '__main__':
    main()
