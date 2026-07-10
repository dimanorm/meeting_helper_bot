import asyncio
import os
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
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
    welcome_text = (
        "<b>👋 Приветствую! Я ИИ-ассистент для управления календарем встреч компании.</b>\n\n"
        "Я умею понимать обычный текст и автоматически управлять расписанием, "
        "а также слежу, чтобы никто из участников не был занят в одно и то же время.\n\n"
        "<b>📋 Памятка по командам:</b>\n"
        "1️⃣ <b>Регистрация:</b> <code>/register</code>\n"
        "<i>По нажатию команды бот выведет интерактивные кнопки со списком свободных профилей сотрудников для быстрой привязки аккаунта.</i>\n\n"
        "2️⃣ <b>Просмотр расписания:</b> <code>/schedule</code>\n"
        "<i>Выводит список всех запланированных встреч с уникальными цветовыми маркерами для каждого сотрудника.</i>\n\n"
        "<b>🤖 Как управлять встречами через ИИ (просто пишите в чат):</b>\n"
        "• <b>Создание:</b> <i>'Создай встречу со мной, Анной и Михаилом завтра с 14 до 15:30'</i>\n"
        "• <b>Изменение:</b> <i>'Перенеси встречу ID 2 на 18:00'</i> или <i>'Добавь Антона во встречу ID 1'</i>.\n"
        "• <b>Удаление:</b> <i>'Отмени встречу номер 3'</i>.\n\n"
        "<i>💡 Начните с отправки команды /register, чтобы привязать свой профиль!</i>"
    )
    await message.answer(welcome_text, parse_mode="HTML")

@dp.message(Command("register"))
async def cmd_register(message: types.Message):
    unregistered = db.get_unregistered_users()
    
    if not unregistered:
        await message.answer("Все доступные профили сотрудников уже зарегистрированы.")
        return
        
    builder = InlineKeyboardBuilder()
    for name in unregistered:
        builder.button(text=name, callback_data=f"reg_{name}")
        
    builder.adjust(2) # Разметка: по 2 кнопки в ряд
    
    await message.answer(
        "<b>Выбор профиля:</b>\n"
        "Пожалуйста, выберите ваше имя из списка ниже для завершения регистрации:", 
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("reg_"))
async def process_register_callback(callback: types.CallbackQuery):
    # Извлекаем имя из callback_data (например, из "reg_Иван" получим "Иван")
    name = callback.data.split("_")[1]
    
    success = db.register_user(callback.from_user.id, name)
    if success:
        await callback.message.edit_text(
            f"✅ <b>Регистрация успешна!</b>\n"
            f"Ваш аккаунт привязан к корпоративному профилю: <b>{name}</b>.\n"
            f"Теперь при планировании встреч ИИ будет распознавать контекст 'со мной'.",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("❌ Произошла ошибка при регистрации. Попробуйте снова.")
        
    await callback.answer()

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
            model="meta-llama/llama-3.3-70b-instruct:free",
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
