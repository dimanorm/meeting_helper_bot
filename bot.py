import asyncio
import os
import json
import sqlite3 # Добавили для точечного чтения данных при обновлении
from datetime import datetime
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

# Инициализация клиента
llm_client = AsyncOpenAI(
    api_key=LLM_API_KEY,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/" # Официальный эндпоинт Google
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
        lines.append(f"<code>[ID:{m_id}]</code> {data['time']}\nУчастники: {participants_str}\n")
        
    await message.answer("\n".join(lines), parse_mode="HTML")

@dp.message(F.text & ~F.text.startswith('/'))
async def handle_meeting_request(message: types.Message):
    user_name = db.get_user_by_tg(message.from_user.id)
    
    context_prompt = ""
    if user_name:
        context_prompt = f"Текущий автор сообщения: {user_name}. Если автор использует местоимения 'со мной', 'мне', 'у меня', обязательно добавь имя '{user_name}' в массив."
    else:
        context_prompt = "Автор неизвестен."

    # Динамически получаем текущую дату в формате YYYY-MM-DD
    current_date = datetime.now().strftime("%Y-%m-%d")

    system_prompt = f"""Ты — ИИ-менеджер расписания. {context_prompt}
Текущая дата: {current_date}.
Определи намерение пользователя (action) и извлеки данные.
Верни ТОЛЬКО JSON.

Форматы ответов:
1. Создание новой встречи (action: "create"):
{{"action": "create", "participants": ["Имя1", "Имя2"], "start_dt": "YYYY-MM-DD HH:MM:00", "end_dt": "YYYY-MM-DD HH:MM:00"}}
ВАЖНОЕ ПРАВИЛО: Если автор НЕ использует слова 'со мной', 'мне', 'у меня', НЕ добавляй автора в participants. Добавляй только явно упомянутых в тексте лиц.

2. Удаление встречи (action: "delete"):
{{"action": "delete", "meeting_id": 123}}

3. Обновление встречи (action: "update"):
{{"action": "update", "meeting_id": 123, "add_participants": ["Имя"], "remove_participants": ["Имя"], "start_dt": "YYYY-MM-DD HH:MM:00", "end_dt": "YYYY-MM-DD HH:MM:00"}}
ПРАВИЛА ДЛЯ UPDATE:
- Если пользователь просит ДОБАВИТЬ кого-то, укажи его имя в "add_participants".
- Если пользователь просит УДАЛИТЬ/УБРАТЬ кого-то, укажи его имя в "remove_participants".
- Если меняется только время, оставь массивы add_participants и remove_participants пустыми.
- Если в тексте указано новое время, заполни start_dt и end_dt. Если время не меняется, оставь их null.

Общее правило: Если время конца встречи не указано явно, автоматически прибавляй 1 час к времени начала.
"""

    processing_msg = await message.answer("🔄 Обработка...")

    try:

        response = await llm_client.chat.completions.create(

            model="gemini-3.1-flash-lite",

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



        # Действие: УДАЛЕНИЕ (оставляем как было)

        if action == "delete":

            m_id = data.get("meeting_id")

            if not m_id:

                return await processing_msg.edit_text("❌ Укажите ID встречи для удаления.")

            

            if db.delete_meeting(m_id):

                await processing_msg.edit_text(f"🗑 Встреча [ID:{m_id}] успешно отменена.")

            else:

                await processing_msg.edit_text(f"❌ Встреча [ID:{m_id}] не найдена.")



        # Действие: ОБНОВЛЕНИЕ (с уведомлениями)
        elif action == "update":
            m_id = data.get("meeting_id")
            if not m_id:
                return await processing_msg.edit_text("❌ Укажите ID встречи для изменения.")
            
            # 1. Запрашиваем текущее состояние встречи из базы данных
            conn = sqlite3.connect(db.DB_PATH)
            c = conn.cursor()
            c.execute("SELECT start_dt, end_dt FROM meetings WHERE id = ?", (m_id,))
            meeting_row = c.fetchone()
            
            if not meeting_row:
                conn.close()
                return await processing_msg.edit_text(f"❌ Встреча [ID:{m_id}] не найдена.")
                
            current_start, current_end = meeting_row
            
            c.execute("""
                SELECT u.name FROM meeting_participants mp 
                JOIN users u ON mp.user_id = u.id 
                WHERE mp.meeting_id = ?
            """, (m_id,))
            current_participants = [row[0] for row in c.fetchall()]
            conn.close()
            
            # 2. Формируем финальное время (новое или оставляем текущее)
            start_dt = data.get("start_dt") or current_start
            end_dt = data.get("end_dt") or current_end
            
            # 3. Корректируем список участников
            add_parts = data.get("add_participants", [])
            remove_parts = data.get("remove_participants", [])
            
            # Обрабатываем контекст "со мной" для операции добавления
            if "со мной" in message.text.lower() and user_name and user_name not in add_parts:
                add_parts.append(user_name)
            
            updated_participants = list(current_participants)
            for p in add_parts:
                if p not in updated_participants:
                    updated_participants.append(p)
            for p in remove_parts:
                if p in updated_participants:
                    updated_participants.remove(p)
                    
            if not updated_participants:
                return await processing_msg.edit_text("❌ Ошибка: во встрече должен остаться хотя бы один участник.")
            
            users_map = db.get_users_ids_by_names(updated_participants)
            missing = [p for p in updated_participants if p not in users_map]
            if missing:
                return await processing_msg.edit_text(f"Сотрудники не найдены в базе: {', '.join(missing)}")
            
            # 4. Проверка пересечений и перезапись встречи
            db.delete_meeting(m_id) # Удаляем старую для чистоты проверки коллизий
            
            user_ids = list(users_map.values())
            success, conflicts = db.check_collision_and_book(user_ids, start_dt, end_dt)
            
            if success:
                # Получаем новый ID пересозданной встречи
                conn = sqlite3.connect(db.DB_PATH)
                c = conn.cursor()
                c.execute("SELECT max(id) FROM meetings")
                new_id = c.fetchone()[0]
                conn.close()
                
                await processing_msg.edit_text(f"✅ Встреча обновлена! Новый <code>[ID:{new_id}]</code>\nВремя: {start_dt} - {end_dt}\nУчастники: {', '.join(updated_participants)}")
                
                # Рассылка уведомлений участникам
                tg_ids = db.get_tg_ids_by_names(updated_participants)
                for p_name, p_tg_id in tg_ids.items():
                    if p_tg_id == message.from_user.id:
                        continue
                    try:
                        await bot.send_message(
                            chat_id=p_tg_id,
                            text=f"🔄 <b>Встреча [ID:{new_id}] изменена!</b>\n\n"
                                 f"<b>Новое время:</b> {start_dt} - {end_dt}\n"
                                 f"<b>Состав участников:</b> {', '.join(updated_participants)}",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        print(f"Не удалось отправить уведомление для {p_name}: {e}")
            else:
                # Откат транзакции: возвращаем старую встречу, если новые слоты заняты
                old_users_map = db.get_users_ids_by_names(current_participants)
                db.check_collision_and_book(list(old_users_map.values()), current_start, current_end)
                await processing_msg.edit_text(f"❌ Не удалось изменить встречу: пересечение времени у {', '.join(set(conflicts))}. Изменения отменены.")


# Действие: СОЗДАНИЕ (с уведомлениями)



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






# РАССЫЛКА УВЕДОМЛЕНИЙ О НОВОЙ ВСТРЕЧЕ



tg_ids = db.get_tg_ids_by_names(participants)



for p_name, p_tg_id in tg_ids.items():



if p_tg_id == message.from_user.id:



continue # Пропускаем создателя встречи



try:



await bot.send_message(



chat_id=p_tg_id,



text=f"📅 <b>Вам назначена новая встреча!</b>\n\n"



f"<b>Время:</b> {start_dt} - {end_dt}\n"



f"<b>Состав участников:</b> {', '.join(participants)}",



parse_mode="HTML"



)



except Exception as e:



print(f"Не удалось отправить уведомление для {p_name}: {e}")



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
