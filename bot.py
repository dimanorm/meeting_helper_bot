import asyncio
import os
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from openai import AsyncOpenAI
from dotenv import load_dotenv
import database as db

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
LLM_API_KEY = os.getenv("LLM_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Инициализация клиента для OpenRouter
llm_client = AsyncOpenAI(
    api_key=LLM_API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

# Создание БД при запуске скрипта
db.init_db()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "Система инициализирована. База данных готова.\n"
        "Для работы требуется привязать аккаунт к профилю сотрудника.\n"
        "Формат команды: /register [Имя]\n"
        "Доступные тестовые профили: Иван, Анна, Петр, Семен, Андрей."
    )
    await message.answer(text)

@dp.message(Command("register"))
async def cmd_register(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Ошибка формата. Используйте: /register [Имя]")
        return
    name = args[1]
    success = db.register_user(message.from_user.id, name)
    if success:
        await message.answer(f"Аккаунт успешно привязан к профилю '{name}'.")
    else:
        await message.answer(f"Сотрудник '{name}' не найден в базе данных.")

@dp.message(F.text & ~F.text.startswith('/'))
async def handle_meeting_request(message: types.Message):
    user_name = db.get_user_by_tg(message.from_user.id)
    
    # Формирование динамического контекста
    context_prompt = ""
    if user_name:
        context_prompt = f"Текущий автор сообщения: {user_name}. Если автор использует местоимения 'со мной', 'мне', 'у меня', обязательно добавь имя '{user_name}' в массив participants."
    else:
        context_prompt = "Автор не зарегистрирован. Игнорировать контекстные местоимения первого лица."

    system_prompt = f"""Действуй как строгий парсер расписания. 
    {context_prompt}
    Извлеки из текста параметры встречи.
    Верни ТОЛЬКО валидный JSON без маркдауна и комментариев.
    Структура:
    {{
      "participants": ["Имя1", "Имя2"],
      "start_dt": "YYYY-MM-DD HH:MM:SS",
      "end_dt": "YYYY-MM-DD HH:MM:SS"
    }}
    Текущий год 2026. Месяц 07. Если время конца не указано, автоматически прибавляй 1 час к времени начала."""

    processing_msg = await message.answer("Обработка запроса нейросетью...")

    try:
        # Использование бесплатной и мощной модели Qwen
        response = await llm_client.chat.completions.create(
            model="qwen/qwen-2.5-7b-instruct",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message.text}
            ],
            temperature=0.0
        )
        
        # Очистка ответа от возможных артефактов маркдауна (```json ... ```)
        raw_json = response.choices[0].message.content.strip()
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:-3].strip()
        elif raw_json.startswith("```"):
            raw_json = raw_json[3:-3].strip()
            
        data = json.loads(raw_json)
        participants = data.get("participants", [])
        start_dt = data.get("start_dt")
        end_dt = data.get("end_dt")
        
        if not participants or not start_dt or not end_dt:
            await processing_msg.edit_text("Ошибка извлечения данных. Пропущены участники или время.")
            return

        # Проверка существования сотрудников
        users_map = db.get_users_ids_by_names(participants)
        missing = [p for p in participants if p not in users_map]
        if missing:
            await processing_msg.edit_text(f"Сотрудники не найдены в базе: {', '.join(missing)}")
            return
            
        # Запуск валидации пересечений
        user_ids = list(users_map.values())
        success, conflicts = db.check_collision_and_book(user_ids, start_dt, end_dt)
        
        if success:
            await processing_msg.edit_text(f"✅ Встреча забронирована!\nУчастники: {', '.join(participants)}\nВремя: {start_dt} - {end_dt}")
        else:
            conflicts_unique = list(set(conflicts))
            await processing_msg.edit_text(f"❌ Ошибка бронирования: пересечение времени.\nЗанятые сотрудники: {', '.join(conflicts_unique)}")
            
    except json.JSONDecodeError:
        await processing_msg.edit_text("Ошибка: LLM вернула невалидный JSON.")
    except Exception as e:
        await processing_msg.edit_text(f"Критическая ошибка системы: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())