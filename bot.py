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

@dp.message(Command("schedule"))
async def cmd_schedule(message: types.Message):
    schedule_data = db.get_upcoming_meetings()
    
    if not schedule_data:
        await message.answer("📅 На ближайшее время встреч не запланировано.")
        return
        
    lines = ["<b>📅 Расписание встреч:</b>\n"]
    for m_id, data in schedule_data.items():
        participants_str = ", ".join(data['participants'])
        # Формат: ID: 1 | 09.07 14:00-15:00 | 🔴 Иван, 🔵 Анна
        lines.append(f"<code>[ID:{m_id}]</code> {data['time']}\nУчастники: {participants_str}\n")
        
    await message.answer("\n".join(lines), parse_mode="HTML")

@dp.message(F.text & ~F.text.startswith('/'))
async def handle_meeting_request(message: types.Message):
    user_name = db.get_user_by_tg(message.from_user.id)
    
    context_prompt = ""
    if user_name:
        context_prompt = f"Текущий автор: {user_name}. 'Со мной' = '{user_name}'."
    else:
        context_prompt = "Автор неизвестен."

    # Мощный промпт маршрутизатор
    system_prompt = f"""Ты — ИИ-менеджер расписания. {context_prompt}
Текущая дата: 2026-07-09.
Определи намерение пользователя (action) и извлеки данные.
Верни ТОЛЬКО JSON.

Форматы ответов:
1. Создание новой встречи:
{{"action": "create", "participants": ["Имя1", "Имя2"], "start_dt": "YYYY-MM-DD HH:MM:00", "end_dt": "YYYY-MM-DD HH:MM:00"}}

2. Удаление встречи (если юзер указал номер/ID):
{{"action": "delete", "meeting_id": 123}}

3. Обновление встречи (замена времени или списка участников):
{{"action": "update", "meeting_id": 123, "participants": ["Имя1", "Имя2"], "start_dt": "YYYY-MM-DD HH:MM:00", "end_dt": "YYYY-MM-DD HH:MM:00"}}

Правила:
- Если время конца не указано, прибавляй 1 час к началу.
- Если юзер просит перенести встречу или добавить/убрать кого-то, он должен указать ID встречи. Используй action "update".
"""

    processing_msg = await message.answer("🔄 Обработка...")

    try:
        response = await llm_client.chat.completions.create(
            model="qwen/qwen-2.5-7b-instruct:free",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message.text}
            ],
            temperature=0.0
        )
        
        raw_json = response.choices[0].message.content.strip()
        if raw_json.startswith("```json"): raw_json = raw_json[7:-3].strip()
        elif raw_json.startswith("```"): raw_json = raw_json[3:-3].strip()
            
        data = json.loads(raw_json)
        action = data.get("action")

        # --- РОУТИНГ ДЕЙСТВИЙ ---

        # Действие: УДАЛЕНИЕ
        if action == "delete":
            m_id = data.get("meeting_id")
            if not m_id:
                return await processing_msg.edit_text("❌ Укажите ID встречи для удаления.")
            
            # Вызываем функцию удаления (ее нужно добавить в database.py, см. ниже)
            if db.delete_meeting(m_id):
                await processing_msg.edit_text(f"🗑 Встреча [ID:{m_id}] успешно отменена.")
            else:
                await processing_msg.edit_text(f"❌ Встреча [ID:{m_id}] не найдена.")

        # Действие: ОБНОВЛЕНИЕ
        elif action == "update":
            m_id = data.get("meeting_id")
            if not m_id:
                return await processing_msg.edit_text("❌ Укажите ID встречи для изменения.")
            
            # Для простоты логики, обновление = удаление старой + создание новой
            # На продакшене так делать не стоит, но для прототипа это самый надежный способ проверки коллизий
            participants = data.get("participants", [])
            start_dt = data.get("start_dt")
            end_dt = data.get("end_dt")
            
            users_map = db.get_users_ids_by_names(participants)
            
            # Удаляем старую
            db.delete_meeting(m_id)
            
            # Пробуем создать новую
            user_ids = list(users_map.values())
            success, conflicts = db.check_collision_and_book(user_ids, start_dt, end_dt)
            
            if success:
                await processing_msg.edit_text(f"✅ Встреча [ID:{m_id}] обновлена!\nНовое время: {start_dt} - {end_dt}\nУчастники: {', '.join(participants)}")
            else:
                await processing_msg.edit_text(f"❌ Не удалось перенести: пересечение времени у {', '.join(set(conflicts))}. (Встреча отменена, создайте заново).")

        # Действие: СОЗДАНИЕ (твой старый код)
        elif action == "create":
            participants = data.get("participants", [])
            start_dt = data.get("start_dt")
            end_dt = data.get("end_dt")
            users_map = db.get_users_ids_by_names(participants)
            missing = [p for p in participants if p not in users_map]
            
            if missing:
                return await processing_msg.edit_text(f"Не найдены в базе: {', '.join(missing)}")
                
            user_ids = list(users_map.values())
            success, conflicts = db.check_collision_and_book(user_ids, start_dt, end_dt)
            
            if success:
                await processing_msg.edit_text(f"✅ Встреча забронирована!\nУчастники: {', '.join(participants)}\nВремя: {start_dt} - {end_dt}")
            else:
                await processing_msg.edit_text(f"❌ Ошибка: пересечение времени.\nЗанятые сотрудники: {', '.join(set(conflicts))}")
        else:
            await processing_msg.edit_text("🤔 Не совсем понял, что нужно сделать. Попробуйте сформулировать иначе.")

    except Exception as e:
        await processing_msg.edit_text(f"Ошибка системы: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
