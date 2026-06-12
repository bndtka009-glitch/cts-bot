import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove, WebAppInfo
)

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
BOT_TOKEN = "8643364597:AAE2o2zq4kNuKwVxxRzrRbiTfgI2TJXgwhs"
SUPER_ADMIN_ID = 5967495207

logging.basicConfig(level=logging.INFO)

# ==========================================
# БАЗА ДАННЫХ
# ==========================================
class CTSDatabase:
    def __init__(self, db_path="cts_infrastructure.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.init_tables()

    def init_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id  INTEGER PRIMARY KEY,
                role     TEXT    NOT NULL,
                emp_id   TEXT    UNIQUE,
                points   INTEGER DEFAULT 0
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id   INTEGER NOT NULL,
                location    TEXT,
                photo_id    TEXT,
                drone_req   INTEGER DEFAULT 0,
                status      TEXT    DEFAULT 'pending',
                reject_reason TEXT
            )
        ''')
        self.conn.commit()

    # ---------- пользователи ----------
    def upsert_user(self, user_id: int, role: str):
        self.cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, role) VALUES (?, ?)",
            (user_id, role)
        )
        self.conn.commit()

    def get_user(self, user_id: int):
        return self.cursor.execute(
            "SELECT user_id, role, emp_id, points FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    def set_emp_id(self, user_id: int, emp_id: str):
        self.cursor.execute(
            "UPDATE users SET emp_id = ? WHERE user_id = ?",
            (emp_id, user_id)
        )
        self.conn.commit()

    def get_all_drivers(self):
        return self.cursor.execute(
            "SELECT user_id FROM users WHERE role = 'driver'"
        ).fetchall()

    def get_all_passengers(self):
        return self.cursor.execute(
            "SELECT user_id FROM users WHERE role = 'passenger'"
        ).fetchall()

    # ---------- репорты ----------
    def create_report(self, driver_id: int, location: str, photo_id: str, drone_req: int = 0):
        self.cursor.execute(
            "INSERT INTO reports (driver_id, location, photo_id, drone_req) VALUES (?, ?, ?, ?)",
            (driver_id, location, photo_id, drone_req)
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def get_pending_reports(self):
        return self.cursor.execute(
            "SELECT id, driver_id, location, photo_id, drone_req FROM reports WHERE status = 'pending'"
        ).fetchall()

    def get_driver_reports(self, driver_id: int):
        return self.cursor.execute(
            "SELECT id, location, status, drone_req FROM reports WHERE driver_id = ? ORDER BY id DESC LIMIT 10",
            (driver_id,)
        ).fetchall()

    def approve_report(self, report_id: int):
        self.cursor.execute(
            "UPDATE reports SET status = 'approved' WHERE id = ?",
            (report_id,)
        )
        res = self.cursor.execute(
            "SELECT driver_id FROM reports WHERE id = ?",
            (report_id,)
        ).fetchone()
        if res:
            self.cursor.execute(
                "UPDATE users SET points = points + 1 WHERE user_id = ?",
                (res[0],)
            )
        self.conn.commit()
        return res[0] if res else None

    def reject_report(self, report_id: int, reason: str):
        self.cursor.execute(
            "UPDATE reports SET status = 'rejected', reject_reason = ? WHERE id = ?",
            (reason, report_id)
        )
        res = self.cursor.execute(
            "SELECT driver_id FROM reports WHERE id = ?",
            (report_id,)
        ).fetchone()
        self.conn.commit()
        return res[0] if res else None

    def get_stats(self):
        total   = self.cursor.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        pending = self.cursor.execute("SELECT COUNT(*) FROM reports WHERE status='pending'").fetchone()[0]
        approved= self.cursor.execute("SELECT COUNT(*) FROM reports WHERE status='approved'").fetchone()[0]
        drivers = self.cursor.execute("SELECT COUNT(*) FROM users WHERE role='driver'").fetchone()[0]
        return total, pending, approved, drivers


db = CTSDatabase()
# Гарантируем запись суперадмина при старте
db.upsert_user(SUPER_ADMIN_ID, 'admin')

# ==========================================
# МАШИНА СОСТОЯНИЙ
# ==========================================
class ReportState(StatesGroup):
    photo = State()

class DroneState(StatesGroup):
    description = State()

class RejectState(StatesGroup):
    reason = State()
    report_id = State()

class BroadcastState(StatesGroup):
    text = State()

class EmpIdState(StatesGroup):
    waiting = State()

# ==========================================
# КЛАВИАТУРЫ
# ==========================================
def kb_admin():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📋 Репорты на проверку"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📢 Рассылка водителям"),  KeyboardButton(text="📣 Рассылка пассажирам")],
        [KeyboardButton(text="🗺 Карта Астаны",
                        web_app=WebAppInfo(url="https://yandex.kz/maps/163/astana/"))]
    ], resize_keyboard=True)

def kb_driver():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚨 Сообщить об инциденте")],
        [KeyboardButton(text="🚁 Запросить дрон-аудит")],
        [KeyboardButton(text="📜 Мои репорты"), KeyboardButton(text="🎁 Мои бонусы")],
        [KeyboardButton(text="🗺 Карта Астаны",
                        web_app=WebAppInfo(url="https://yandex.kz/maps/163/astana/"))]
    ], resize_keyboard=True)

def kb_passenger():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚌 Статус маршрутов")],
        [KeyboardButton(text="🗺 Карта Астаны",
                        web_app=WebAppInfo(url="https://yandex.kz/maps/163/astana/"))]
    ], resize_keyboard=True)

def kb_report_actions(report_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить",  callback_data=f"adm_ok_{report_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_no_{report_id}")
    ]])

def kb_role_select():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚌 Я — водитель")],
        [KeyboardButton(text="👤 Я — пассажир")]
    ], resize_keyboard=True)

# ==========================================
# BOT + DISPATCHER
# ==========================================
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

# ==========================================
# /start
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    if user_id == SUPER_ADMIN_ID:
        db.upsert_user(user_id, 'admin')
        await message.answer(
            "👑 *SUPERADMIN ACTIVATED*\nДобро пожаловать в CTS Driver Network.",
            reply_markup=kb_admin(), parse_mode="Markdown"
        )
        return

    user = db.get_user(user_id)
    if user:
        role = user[1]
        kb   = kb_driver() if role == 'driver' else kb_passenger()
        await message.answer(f"👋 С возвращением! Ваша роль: *{role.upper()}*",
                             reply_markup=kb, parse_mode="Markdown")
    else:
        await message.answer(
            "👋 Добро пожаловать в *CTS Driver Network*!\nВыберите вашу роль:",
            reply_markup=kb_role_select(), parse_mode="Markdown"
        )

@dp.message(F.text == "🚌 Я — водитель")
async def reg_driver(message: types.Message, state: FSMContext):
    db.upsert_user(message.from_user.id, 'driver')
    await message.answer(
        "✅ Вы зарегистрированы как *водитель*.\n\n"
        "Пожалуйста, введите ваш табельный номер (Employee ID) для привязки:",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown"
    )
    await state.set_state(EmpIdState.waiting)

@dp.message(F.text == "👤 Я — пассажир")
async def reg_passenger(message: types.Message):
    db.upsert_user(message.from_user.id, 'passenger')
    await message.answer(
        "✅ Вы зарегистрированы как *пассажир*.\n"
        "Вы будете получать оповещения о ситуации на маршрутах.",
        reply_markup=kb_passenger(), parse_mode="Markdown"
    )

@dp.message(EmpIdState.waiting)
async def save_emp_id(message: types.Message, state: FSMContext):
    emp_id = message.text.strip()
    db.set_emp_id(message.from_user.id, emp_id)
    await state.clear()
    await message.answer(
        f"✅ Табельный номер *{emp_id}* сохранён.\nДобро пожаловать в систему!",
        reply_markup=kb_driver(), parse_mode="Markdown"
    )

# ==========================================
# ВОДИТЕЛЬ — репорт
# ==========================================
@dp.message(F.text == "🚨 Сообщить об инциденте")
async def driver_report(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if not user or user[1] != 'driver':
        await message.answer("⛔ Только водители могут отправлять репорты.")
        return
    await state.update_data(drone_req=0)
    await message.answer(
        "📸 Отправьте *фото инцидента* с подписью-описанием (необязательно).\n"
        "Геолокация будет взята автоматически из метаданных.",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown"
    )
    await state.set_state(ReportState.photo)

@dp.message(F.text == "🚁 Запросить дрон-аудит")
async def drone_request(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if not user or user[1] != 'driver':
        await message.answer("⛔ Только водители могут запрашивать дрон-аудит.")
        return
    await state.update_data(drone_req=1)
    await message.answer(
        "🚁 *Дрон-аудит запрошен.*\n\n"
        "Отправьте фото места (или любое фото-заглушку) и кратко опишите ситуацию в подписи к фото.\n"
        "Администратор получит запрос на выезд дрона.",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown"
    )
    await state.set_state(ReportState.photo)

@dp.message(ReportState.photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    data     = await state.get_data()
    drone_req = data.get('drone_req', 0)
    photo_id  = message.photo[-1].file_id
    caption   = message.caption or ""

    # Координаты из геолокации или дефолт (Байтерек)
    location = "51.1283, 71.4305"

    report_id = db.create_report(message.from_user.id, location, photo_id, drone_req)
    await state.clear()

    drone_label = " 🚁 [ДРОН-АУДИТ]" if drone_req else ""
    await message.answer(
        f"✅ Репорт *#{report_id}* отправлен{drone_label}. Ожидайте проверки.",
        reply_markup=kb_driver(), parse_mode="Markdown"
    )

    # Уведомление админу
    flag = "🚁 *ДРОН-АУДИТ ЗАПРОШЕН*\n\n" if drone_req else ""
    admin_caption = (
        f"{flag}📦 Новый репорт *#{report_id}*\n"
        f"👤 Водитель: `{message.from_user.id}`\n"
        f"📍 Локация: `{location}`\n"
        f"📝 Описание: {caption if caption else '—'}"
    )
    await bot.send_photo(
        SUPER_ADMIN_ID, photo=photo_id,
        caption=admin_caption,
        reply_markup=kb_report_actions(report_id),
        parse_mode="Markdown"
    )

# БАГ-ФИК: обработка не-фото в состоянии ReportState.photo
@dp.message(ReportState.photo)
async def photo_wrong_input(message: types.Message):
    await message.answer("⚠️ Пожалуйста, отправьте именно *фото*. Текст и файлы не принимаются.",
                         parse_mode="Markdown")

# ==========================================
# ВОДИТЕЛЬ — история и бонусы
# ==========================================
@dp.message(F.text == "📜 Мои репорты")
async def my_reports(message: types.Message):
    user = db.get_user(message.from_user.id)
    if not user or user[1] != 'driver':
        await message.answer("⛔ Только для водителей.")
        return
    reports = db.get_driver_reports(message.from_user.id)
    if not reports:
        await message.answer("📭 У вас пока нет репортов.")
        return
    status_icon = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}
    lines = ["📜 *Ваши последние репорты:*\n"]
    for r in reports:
        icon  = status_icon.get(r[2], '❓')
        drone = " 🚁" if r[3] else ""
        lines.append(f"{icon} Репорт *#{r[0]}*{drone}\n   📍 {r[1]}  |  статус: {r[2]}")
    await message.answer("\n".join(lines), parse_mode="Markdown")

@dp.message(F.text == "🎁 Мои бонусы")
async def check_bonus(message: types.Message):
    user = db.get_user(message.from_user.id)
    # БАГ-ФИКС: безопасная обработка если пользователь не в БД
    pts  = user[3] if user else 0
    days = pts // 4
    left = 4 - (pts % 4)
    text = (
        f"⭐ Ваши баллы: *{pts}*\n"
        f"📅 Накоплено дней отдыха: *{days}*\n\n"
    )
    if left < 4:
        text += f"До следующего дня отдыха осталось: *{left}* подтверждённых репорта."
    else:
        text += "Подтверждайте инциденты — 4 балла = 1 день оплачиваемого отгула!"
    await message.answer(text, parse_mode="Markdown")

# ==========================================
# ПАССАЖИР
# ==========================================
@dp.message(F.text == "🚌 Статус маршрутов")
async def route_status(message: types.Message):
    approved_count = db.cursor.execute(
        "SELECT COUNT(*) FROM reports WHERE status='approved'"
    ).fetchone()[0]
    await message.answer(
        f"🚌 *Статус маршрутов Астаны*\n\n"
        f"✅ Обработано инцидентов сегодня: *{approved_count}*\n"
        f"ℹ️ Актуальные оповещения появятся здесь после публикации администратором.",
        parse_mode="Markdown"
    )

# ==========================================
# АДМИН — проверка репортов
# ==========================================
@dp.message(F.text == "📋 Репорты на проверку")
async def show_pending(message: types.Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    pending = db.get_pending_reports()
    if not pending:
        await message.answer("✅ Новых репортов нет.")
        return
    await message.answer(f"📋 Ожидают проверки: *{len(pending)}* репорт(ов)", parse_mode="Markdown")
    for r in pending:
        rid, driver_id, location, photo_id, drone_req = r
        drone_label = "🚁 *ДРОН-АУДИТ*\n" if drone_req else ""
        caption = (
            f"{drone_label}📦 Репорт *#{rid}*\n"
            f"👤 Водитель: `{driver_id}`\n"
            f"📍 Локация: `{location}`"
        )
        await bot.send_photo(
            message.chat.id, photo=photo_id,
            caption=caption,
            reply_markup=kb_report_actions(rid),
            parse_mode="Markdown"
        )

@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    total, pending, approved, drivers = db.get_stats()
    await message.answer(
        f"📊 *Статистика CTS Driver Network*\n\n"
        f"👷 Водителей в системе: *{drivers}*\n"
        f"📦 Всего репортов: *{total}*\n"
        f"⏳ Ожидают проверки: *{pending}*\n"
        f"✅ Подтверждено: *{approved}*",
        parse_mode="Markdown"
    )

# БАГ-ФИКС: callback_data "adm_ok_5" → split даёт ["adm","ok","5"]
# Используем int(r_id) чтобы не было проблем с типами
@dp.callback_query(F.data.startswith("adm_ok_"))
async def admin_approve(callback: types.CallbackQuery):
    r_id      = int(callback.data.split("adm_ok_")[1])
    driver_id = db.approve_report(r_id)
    await callback.message.edit_caption(
        caption=f"✅ Репорт *#{r_id}* — ОДОБРЕН. Балл начислен водителю.",
        parse_mode="Markdown"
    )
    await callback.answer("✅ Одобрено")
    if driver_id:
        user  = db.get_user(driver_id)
        pts   = user[3] if user else 0
        await bot.send_message(
            driver_id,
            f"🌟 Ваш репорт *#{r_id}* подтверждён!\n"
            f"Вам начислен *1 балл*. Итого баллов: *{pts}*\n"
            f"📅 Накоплено дней отдыха: *{pts // 4}*",
            parse_mode="Markdown"
        )

@dp.callback_query(F.data.startswith("adm_no_"))
async def admin_reject_start(callback: types.CallbackQuery, state: FSMContext):
    r_id = int(callback.data.split("adm_no_")[1])
    await state.update_data(report_id=r_id)
    await state.set_state(RejectState.reason)
    await callback.message.answer(
        f"❌ Укажите причину отклонения репорта *#{r_id}*:",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message(RejectState.reason)
async def admin_reject_finish(message: types.Message, state: FSMContext):
    data      = await state.get_data()
    r_id      = data['report_id']
    reason    = message.text.strip()
    driver_id = db.reject_report(r_id, reason)
    await state.clear()
    await message.answer(
        f"📋 Репорт *#{r_id}* отклонён.\nПричина: _{reason}_",
        parse_mode="Markdown"
    )
    if driver_id:
        await bot.send_message(
            driver_id,
            f"❌ Ваш репорт *#{r_id}* был отклонён.\n"
            f"📝 Причина: _{reason}_",
            parse_mode="Markdown"
        )

# ==========================================
# АДМИН — рассылка
# ==========================================
@dp.message(F.text == "📢 Рассылка водителям")
async def broadcast_drivers_start(message: types.Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    await state.update_data(target="drivers")
    await message.answer(
        "✏️ Введите текст рассылки для *водителей*:",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown"
    )
    await state.set_state(BroadcastState.text)

@dp.message(F.text == "📣 Рассылка пассажирам")
async def broadcast_passengers_start(message: types.Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    await state.update_data(target="passengers")
    await message.answer(
        "✏️ Введите текст рассылки для *пассажиров*:",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown"
    )
    await state.set_state(BroadcastState.text)

@dp.message(BroadcastState.text)
async def broadcast_send(message: types.Message, state: FSMContext):
    data   = await state.get_data()
    target = data.get('target', 'drivers')
    text   = message.text.strip()
    await state.clear()

    recipients = db.get_all_drivers() if target == 'drivers' else db.get_all_passengers()
    role_label = "водителям" if target == 'drivers' else "пассажирам"

    sent, failed = 0, 0
    for (uid,) in recipients:
        if uid == SUPER_ADMIN_ID:
            continue
        try:
            await bot.send_message(
                uid,
                f"📢 *Оповещение от администратора CTS:*\n\n{text}",
                parse_mode="Markdown"
            )
            sent += 1
        except Exception:
            failed += 1

    await message.answer(
        f"✅ Рассылка {role_label} завершена.\n"
        f"📨 Отправлено: *{sent}* | ❌ Не доставлено: *{failed}*",
        reply_markup=kb_admin(), parse_mode="Markdown"
    )

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    logging.info("CTS Driver Network Bot запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())import asyncio
import logging
import os
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove, WebAppInfo
)

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
BOT_TOKEN      = os.getenv("BOT_TOKEN", "8643364597:AAE2o2zq4kNuKwVxxRzrRbiTfgI2TJXgwhs")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "5967495207"))

logging.basicConfig(level=logging.INFO)

# ==========================================
# БАЗА ДАННЫХ
# ==========================================
class CTSDatabase:
    def __init__(self, db_path="cts_infrastructure.db"):
        self.conn   = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.init_tables()

    def init_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id  INTEGER PRIMARY KEY,
                role     TEXT    NOT NULL,
                emp_id   TEXT    UNIQUE,
                points   INTEGER DEFAULT 0,
                banned   INTEGER DEFAULT 0
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id     INTEGER NOT NULL,
                location      TEXT,
                media_id      TEXT,
                media_type    TEXT,
                description   TEXT,
                drone_req     INTEGER DEFAULT 0,
                status        TEXT    DEFAULT 'pending',
                reject_reason TEXT
            )
        ''')
        # Миграция для старых БД
        for table, col, definition in [
            ("users",   "banned",     "INTEGER DEFAULT 0"),
            ("reports", "media_type", "TEXT"),
            ("reports", "description","TEXT"),
        ]:
            try:
                self.cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            except Exception:
                pass
        self.conn.commit()

    # ---------- пользователи ----------
    def upsert_user(self, user_id: int, role: str):
        self.cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, role) VALUES (?, ?)", (user_id, role))
        self.conn.commit()

    def get_user(self, user_id: int):
        return self.cursor.execute(
            "SELECT user_id, role, emp_id, points, banned FROM users WHERE user_id = ?",
            (user_id,)).fetchone()

    def set_emp_id(self, user_id: int, emp_id: str):
        self.cursor.execute("UPDATE users SET emp_id = ? WHERE user_id = ?", (emp_id, user_id))
        self.conn.commit()

    def set_role(self, user_id: int, role: str):
        self.cursor.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
        self.conn.commit()

    def set_banned(self, user_id: int, banned: int):
        self.cursor.execute("UPDATE users SET banned = ? WHERE user_id = ?", (banned, user_id))
        self.conn.commit()

    def get_all_users(self):
        return self.cursor.execute(
            "SELECT user_id, role, emp_id, points, banned FROM users WHERE user_id != ?",
            (SUPER_ADMIN_ID,)).fetchall()

    def get_all_drivers(self):
        return self.cursor.execute(
            "SELECT user_id FROM users WHERE role='driver' AND banned=0").fetchall()

    def get_all_passengers(self):
        return self.cursor.execute(
            "SELECT user_id FROM users WHERE role='passenger' AND banned=0").fetchall()

    # ---------- репорты ----------
    def create_report(self, driver_id, location, media_id, media_type,
                      drone_req=0, description="", status="pending"):
        self.cursor.execute(
            "INSERT INTO reports (driver_id, location, media_id, media_type, drone_req, description, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (driver_id, location, media_id, media_type, drone_req, description, status))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_report(self, report_id: int):
        return self.cursor.execute(
            "SELECT id, driver_id, location, media_id, media_type, description, drone_req, status "
            "FROM reports WHERE id = ?", (report_id,)).fetchone()

    def get_pending_reports(self):
        return self.cursor.execute(
            "SELECT id, driver_id, location, media_id, media_type, drone_req, description "
            "FROM reports WHERE status='pending'").fetchall()

    def get_all_reports(self, limit=15):
        return self.cursor.execute(
            "SELECT id, driver_id, location, status, drone_req, media_type "
            "FROM reports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def get_driver_reports(self, driver_id: int):
        return self.cursor.execute(
            "SELECT id, location, status, drone_req FROM reports "
            "WHERE driver_id=? ORDER BY id DESC LIMIT 10", (driver_id,)).fetchall()

    def update_report(self, report_id, location, description):
        self.cursor.execute(
            "UPDATE reports SET location=?, description=? WHERE id=?",
            (location, description, report_id))
        self.conn.commit()

    def delete_report(self, report_id: int):
        res = self.cursor.execute(
            "SELECT driver_id, status FROM reports WHERE id=?", (report_id,)).fetchone()
        self.cursor.execute("DELETE FROM reports WHERE id=?", (report_id,))
        self.conn.commit()
        return res

    def approve_report(self, report_id: int):
        self.cursor.execute("UPDATE reports SET status='approved' WHERE id=?", (report_id,))
        res = self.cursor.execute(
            "SELECT driver_id FROM reports WHERE id=?", (report_id,)).fetchone()
        if res:
            self.cursor.execute(
                "UPDATE users SET points=points+1 WHERE user_id=?", (res[0],))
        self.conn.commit()
        return res[0] if res else None

    def reject_report(self, report_id: int, reason: str):
        self.cursor.execute(
            "UPDATE reports SET status='rejected', reject_reason=? WHERE id=?",
            (reason, report_id))
        res = self.cursor.execute(
            "SELECT driver_id FROM reports WHERE id=?", (report_id,)).fetchone()
        self.conn.commit()
        return res[0] if res else None

    def get_stats(self):
        total    = self.cursor.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        pending  = self.cursor.execute("SELECT COUNT(*) FROM reports WHERE status='pending'").fetchone()[0]
        approved = self.cursor.execute("SELECT COUNT(*) FROM reports WHERE status='approved'").fetchone()[0]
        drivers  = self.cursor.execute("SELECT COUNT(*) FROM users WHERE role='driver'").fetchone()[0]
        banned   = self.cursor.execute("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0]
        return total, pending, approved, drivers, banned


db = CTSDatabase()
db.upsert_user(SUPER_ADMIN_ID, 'admin')

# ==========================================
# МАШИНА СОСТОЯНИЙ
# ==========================================
class ReportState(StatesGroup):
    media    = State()   # шаг 1: фото или видео
    location = State()   # шаг 2: геолокация

class RejectState(StatesGroup):
    reason = State()

class BroadcastState(StatesGroup):
    text = State()

class EmpIdState(StatesGroup):
    waiting = State()

class AdminAddReport(StatesGroup):
    driver_id   = State()
    location    = State()
    description = State()
    media       = State()
    status      = State()

class AdminEditReport(StatesGroup):
    report_id   = State()
    location    = State()
    description = State()

# ==========================================
# КЛАВИАТУРЫ
# ==========================================
def kb_admin():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📋 Репорты на проверку"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📢 Рассылка водителям"),  KeyboardButton(text="📣 Рассылка пассажирам")],
        [KeyboardButton(text="🛠 Управление репортами"), KeyboardButton(text="👥 Управление пользователями")],
        [KeyboardButton(text="➕ Добавить репорт")],
        [KeyboardButton(text="🗺 Карта Астаны",
                        web_app=WebAppInfo(url="https://yandex.kz/maps/163/astana/"))]
    ], resize_keyboard=True)

def kb_driver():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚨 Сообщить об инциденте")],
        [KeyboardButton(text="🚁 Запросить дрон-аудит")],
        [KeyboardButton(text="📜 Мои репорты"), KeyboardButton(text="🎁 Мои бонусы")],
        [KeyboardButton(text="🗺 Карта Астаны",
                        web_app=WebAppInfo(url="https://yandex.kz/maps/163/astana/"))]
    ], resize_keyboard=True)

def kb_passenger():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚌 Статус маршрутов")],
        [KeyboardButton(text="🗺 Карта Астаны",
                        web_app=WebAppInfo(url="https://yandex.kz/maps/163/astana/"))]
    ], resize_keyboard=True)

def kb_role_select():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚌 Я — водитель")],
        [KeyboardButton(text="👤 Я — пассажир")]
    ], resize_keyboard=True)

def kb_send_location():
    """Кнопка для отправки геолокации"""
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📍 Отправить мою геолокацию", request_location=True)],
        [KeyboardButton(text="❌ Отменить репорт")]
    ], resize_keyboard=True)

def kb_report_actions(report_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить",  callback_data=f"adm_ok_{report_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_no_{report_id}")
    ]])

def kb_report_manage(report_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{report_id}"),
        InlineKeyboardButton(text="🗑 Удалить",        callback_data=f"del_{report_id}")
    ]])

def kb_user_actions(user_id: int, banned: int, role: str):
    ban_text = "🔓 Разбанить" if banned else "🚫 Забанить"
    ban_data = f"unban_{user_id}" if banned else f"ban_{user_id}"
    role_text = "→ Пассажир" if role == "driver" else "→ Водитель"
    role_data = f"role_passenger_{user_id}" if role == "driver" else f"role_driver_{user_id}"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=ban_text,  callback_data=ban_data),
        InlineKeyboardButton(text=role_text, callback_data=role_data)
    ]])

def kb_add_report_status():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏳ Pending",  callback_data="newrep_pending"),
        InlineKeyboardButton(text="✅ Approved", callback_data="newrep_approved"),
        InlineKeyboardButton(text="❌ Rejected", callback_data="newrep_rejected"),
    ]])

# ==========================================
# BOT + DISPATCHER
# ==========================================
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

# ==========================================
# /start
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    if uid == SUPER_ADMIN_ID:
        db.upsert_user(uid, 'admin')
        await message.answer(
            "👑 *SUPERADMIN ACTIVATED*\nДобро пожаловать в CTS Driver Network.",
            reply_markup=kb_admin(), parse_mode="Markdown")
        return
    user = db.get_user(uid)
    if user:
        if user[4] == 1:
            await message.answer("⛔ Ваш аккаунт заблокирован администратором.")
            return
        kb = kb_driver() if user[1] == 'driver' else kb_passenger()
        await message.answer(f"👋 С возвращением! Роль: *{user[1].upper()}*",
                             reply_markup=kb, parse_mode="Markdown")
    else:
        await message.answer(
            "👋 Добро пожаловать в *CTS Driver Network*!\nВыберите вашу роль:",
            reply_markup=kb_role_select(), parse_mode="Markdown")

@dp.message(F.text == "🚌 Я — водитель")
async def reg_driver(message: types.Message, state: FSMContext):
    db.upsert_user(message.from_user.id, 'driver')
    await message.answer(
        "✅ Вы зарегистрированы как *водитель*.\nВведите ваш табельный номер (Employee ID):",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    await state.set_state(EmpIdState.waiting)

@dp.message(F.text == "👤 Я — пассажир")
async def reg_passenger(message: types.Message):
    db.upsert_user(message.from_user.id, 'passenger')
    await message.answer("✅ Вы зарегистрированы как *пассажир*.",
                         reply_markup=kb_passenger(), parse_mode="Markdown")

@dp.message(EmpIdState.waiting)
async def save_emp_id(message: types.Message, state: FSMContext):
    db.set_emp_id(message.from_user.id, message.text.strip())
    await state.clear()
    await message.answer(f"✅ Табельный номер *{message.text.strip()}* сохранён.",
                         reply_markup=kb_driver(), parse_mode="Markdown")

# ==========================================
# ВОДИТЕЛЬ — РЕПОРТ (2 шага: медиа → геолокация)
# ==========================================
async def start_report_flow(message: types.Message, state: FSMContext, drone_req: int = 0):
    user = db.get_user(message.from_user.id)
    if not user or user[1] != 'driver':
        await message.answer("⛔ Только водители могут отправлять репорты.")
        return
    await state.update_data(drone_req=drone_req)
    drone_label = " 🚁 [ДРОН-АУДИТ]" if drone_req else ""
    await message.answer(
        f"📋 *Создание репорта{drone_label}*\n\n"
        f"*Шаг 1 из 2* — Отправьте 📸 фото или 🎥 видео инцидента.\n"
        f"Можно добавить текстовую подпись с описанием.",
        reply_markup=ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="❌ Отменить репорт")]
        ], resize_keyboard=True),
        parse_mode="Markdown"
    )
    await state.set_state(ReportState.media)

@dp.message(F.text == "🚨 Сообщить об инциденте")
async def driver_report(message: types.Message, state: FSMContext):
    await start_report_flow(message, state, drone_req=0)

@dp.message(F.text == "🚁 Запросить дрон-аудит")
async def drone_request(message: types.Message, state: FSMContext):
    await start_report_flow(message, state, drone_req=1)

@dp.message(F.text == "❌ Отменить репорт")
async def cancel_report(message: types.Message, state: FSMContext):
    await state.clear()
    user = db.get_user(message.from_user.id)
    kb   = kb_driver() if (user and user[1] == 'driver') else kb_passenger()
    await message.answer("❌ Репорт отменён.", reply_markup=kb)

# Шаг 1: принимаем фото или видео
@dp.message(ReportState.media, F.photo | F.video)
async def process_media(message: types.Message, state: FSMContext):
    if message.photo:
        media_id   = message.photo[-1].file_id
        media_type = "photo"
        media_icon = "📸 Фото"
    else:
        media_id   = message.video.file_id
        media_type = "video"
        media_icon = "🎥 Видео"

    description = message.caption or ""
    await state.update_data(media_id=media_id, media_type=media_type, description=description)

    await message.answer(
        f"✅ {media_icon} получено!\n\n"
        f"*Шаг 2 из 2* — Теперь отправьте вашу 📍 геолокацию.\n"
        f"Нажмите кнопку ниже — телефон автоматически определит координаты.",
        reply_markup=kb_send_location(),
        parse_mode="Markdown"
    )
    await state.set_state(ReportState.location)

# Шаг 1: неправильный ввод
@dp.message(ReportState.media)
async def media_wrong_input(message: types.Message):
    await message.answer(
        "⚠️ Пожалуйста, отправьте *фото* или *видео* инцидента.\n"
        "Текстовые сообщения на этом шаге не принимаются.",
        parse_mode="Markdown"
    )

# Шаг 2: принимаем геолокацию
@dp.message(ReportState.location, F.location)
async def process_location(message: types.Message, state: FSMContext):
    data      = await state.get_data()
    lat       = message.location.latitude
    lon       = message.location.longitude
    location  = f"{lat}, {lon}"
    media_id  = data['media_id']
    media_type= data['media_type']
    drone_req = data.get('drone_req', 0)
    description = data.get('description', '')

    report_id = db.create_report(
        driver_id   = message.from_user.id,
        location    = location,
        media_id    = media_id,
        media_type  = media_type,
        drone_req   = drone_req,
        description = description
    )
    await state.clear()

    drone_label = " 🚁 [ДРОН-АУДИТ]" if drone_req else ""
    maps_link   = f"https://maps.google.com/?q={lat},{lon}"

    await message.answer(
        f"✅ *Репорт #{report_id} отправлен{drone_label}!*\n\n"
        f"📍 Координаты: `{location}`\n"
        f"🗺 [Открыть на карте]({maps_link})\n\n"
        f"Ожидайте подтверждения от администратора.",
        reply_markup=kb_driver(),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

    # Уведомление админу
    flag    = "🚁 *ДРОН-АУДИТ ЗАПРОШЕН*\n\n" if drone_req else ""
    caption = (
        f"{flag}📦 Новый репорт *#{report_id}*\n"
        f"👤 Водитель: `{message.from_user.id}`\n"
        f"📍 Локация: `{location}`\n"
        f"🗺 [Открыть на карте]({maps_link})\n"
        f"📝 {description if description else '—'}"
    )

    if media_type == "photo":
        await bot.send_photo(SUPER_ADMIN_ID, photo=media_id,
                             caption=caption, reply_markup=kb_report_actions(report_id),
                             parse_mode="Markdown")
    else:
        await bot.send_video(SUPER_ADMIN_ID, video=media_id,
                             caption=caption, reply_markup=kb_report_actions(report_id),
                             parse_mode="Markdown")

    # Отдельно пересылаем геолокацию админу
    await bot.send_location(SUPER_ADMIN_ID, latitude=lat, longitude=lon)

# Шаг 2: неправильный ввод (не геолокация)
@dp.message(ReportState.location)
async def location_wrong_input(message: types.Message):
    await message.answer(
        "⚠️ Нужна геолокация! Нажмите кнопку *«📍 Отправить мою геолокацию»* ниже.\n\n"
        "Убедитесь что разрешили доступ к геолокации в Telegram.",
        reply_markup=kb_send_location(),
        parse_mode="Markdown"
    )

# ==========================================
# ВОДИТЕЛЬ — история и бонусы
# ==========================================
@dp.message(F.text == "📜 Мои репорты")
async def my_reports(message: types.Message):
    user = db.get_user(message.from_user.id)
    if not user or user[1] != 'driver':
        await message.answer("⛔ Только для водителей.")
        return
    reports = db.get_driver_reports(message.from_user.id)
    if not reports:
        await message.answer("📭 У вас пока нет репортов.")
        return
    icons = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}
    lines = ["📜 *Ваши последние репорты:*\n"]
    for r in reports:
        icon  = icons.get(r[2], '❓')
        drone = " 🚁" if r[3] else ""
        lines.append(f"{icon} Репорт *#{r[0]}*{drone}\n   📍 {r[1]}  |  {r[2]}")
    await message.answer("\n".join(lines), parse_mode="Markdown")

@dp.message(F.text == "🎁 Мои бонусы")
async def check_bonus(message: types.Message):
    user = db.get_user(message.from_user.id)
    pts  = user[3] if user else 0
    days = pts // 4
    left = 4 - (pts % 4)
    text = f"⭐ Ваши баллы: *{pts}*\n📅 Дней отдыха: *{days}*\n\n"
    text += f"До следующего дня: ещё *{left}* репорта." if left < 4 else "4 балла = 1 день отгула!"
    await message.answer(text, parse_mode="Markdown")

# ==========================================
# ПАССАЖИР
# ==========================================
@dp.message(F.text == "🚌 Статус маршрутов")
async def route_status(message: types.Message):
    count = db.cursor.execute("SELECT COUNT(*) FROM reports WHERE status='approved'").fetchone()[0]
    await message.answer(
        f"🚌 *Статус маршрутов Астаны*\n\n✅ Обработано инцидентов: *{count}*",
        parse_mode="Markdown")

# ==========================================
# АДМИН — проверка репортов
# ==========================================
@dp.message(F.text == "📋 Репорты на проверку")
async def show_pending(message: types.Message):
    if message.from_user.id != SUPER_ADMIN_ID: return
    pending = db.get_pending_reports()
    if not pending:
        await message.answer("✅ Новых репортов нет.")
        return
    await message.answer(f"📋 Ожидают проверки: *{len(pending)}*", parse_mode="Markdown")
    for r in pending:
        rid, driver_id, location, media_id, media_type, drone_req, desc = r
        drone   = "🚁 *ДРОН-АУДИТ*\n" if drone_req else ""
        lat_lon = location.split(", ") if location else ["0","0"]
        maps_link = f"https://maps.google.com/?q={location.replace(' ','')}" if location else ""
        caption = (
            f"{drone}📦 Репорт *#{rid}*\n"
            f"👤 `{driver_id}`\n"
            f"📍 [{location}]({maps_link})\n"
            f"📝 {desc or '—'}"
        )
        try:
            if media_type == "video":
                await bot.send_video(message.chat.id, video=media_id,
                                     caption=caption, reply_markup=kb_report_actions(rid),
                                     parse_mode="Markdown")
            else:
                await bot.send_photo(message.chat.id, photo=media_id,
                                     caption=caption, reply_markup=kb_report_actions(rid),
                                     parse_mode="Markdown")
        except Exception:
            await message.answer(caption + f"\n\n⚠️ Медиафайл недоступен.",
                                 reply_markup=kb_report_actions(rid), parse_mode="Markdown")

@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    if message.from_user.id != SUPER_ADMIN_ID: return
    total, pending, approved, drivers, banned = db.get_stats()
    await message.answer(
        f"📊 *Статистика CTS Driver Network*\n\n"
        f"👷 Водителей: *{drivers}*\n🚫 Забанено: *{banned}*\n"
        f"📦 Всего репортов: *{total}*\n"
        f"⏳ Ожидают: *{pending}*\n✅ Подтверждено: *{approved}*",
        parse_mode="Markdown")

@dp.callback_query(F.data.startswith("adm_ok_"))
async def admin_approve(callback: types.CallbackQuery):
    r_id      = int(callback.data.split("adm_ok_")[1])
    driver_id = db.approve_report(r_id)
    await callback.message.edit_caption(
        caption=f"✅ Репорт *#{r_id}* — ОДОБРЕН.", parse_mode="Markdown")
    await callback.answer("✅ Одобрено")
    if driver_id:
        user = db.get_user(driver_id)
        pts  = user[3] if user else 0
        await bot.send_message(
            driver_id,
            f"🌟 Репорт *#{r_id}* подтверждён! +1 балл. Итого: *{pts}*\n📅 Дней отдыха: *{pts // 4}*",
            parse_mode="Markdown")

@dp.callback_query(F.data.startswith("adm_no_"))
async def admin_reject_start(callback: types.CallbackQuery, state: FSMContext):
    r_id = int(callback.data.split("adm_no_")[1])
    await state.update_data(report_id=r_id)
    await state.set_state(RejectState.reason)
    await callback.message.answer(
        f"❌ Причина отклонения репорта *#{r_id}*:", parse_mode="Markdown")
    await callback.answer()

@dp.message(RejectState.reason)
async def admin_reject_finish(message: types.Message, state: FSMContext):
    data      = await state.get_data()
    r_id      = data['report_id']
    reason    = message.text.strip()
    driver_id = db.reject_report(r_id, reason)
    await state.clear()
    await message.answer(
        f"📋 Репорт *#{r_id}* отклонён. Причина: _{reason}_", parse_mode="Markdown")
    if driver_id:
        await bot.send_message(driver_id,
            f"❌ Репорт *#{r_id}* отклонён.\n📝 Причина: _{reason}_", parse_mode="Markdown")

# ==========================================
# АДМИН — УПРАВЛЕНИЕ РЕПОРТАМИ
# ==========================================
@dp.message(F.text == "🛠 Управление репортами")
async def admin_manage_reports(message: types.Message):
    if message.from_user.id != SUPER_ADMIN_ID: return
    reports = db.get_all_reports()
    if not reports:
        await message.answer("📭 Репортов нет.")
        return
    icons = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}
    await message.answer("🛠 *Все репорты (последние 15):*", parse_mode="Markdown")
    for r in reports:
        rid, driver_id, location, status, drone_req, media_type = r
        icon   = icons.get(status, '❓')
        drone  = " 🚁" if drone_req else ""
        mtype  = "🎥" if media_type == "video" else "📸"
        await message.answer(
            f"{icon}{mtype} Репорт *#{rid}*{drone}\n"
            f"👤 `{driver_id}` | 📍 {location} | {status}",
            reply_markup=kb_report_manage(rid), parse_mode="Markdown")

# --- Добавить репорт вручную ---
@dp.message(F.text == "➕ Добавить репорт")
async def admin_add_report_start(message: types.Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID: return
    await message.answer(
        "➕ *Добавление репорта вручную*\n\nВведите Telegram ID водителя:",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    await state.set_state(AdminAddReport.driver_id)

@dp.message(AdminAddReport.driver_id)
async def admin_add_driver_id(message: types.Message, state: FSMContext):
    try:
        await state.update_data(driver_id=int(message.text.strip()))
        await message.answer("📍 Введите локацию (например: 51.1283, 71.4305):")
        await state.set_state(AdminAddReport.location)
    except ValueError:
        await message.answer("⚠️ Введите числовой Telegram ID.")

@dp.message(AdminAddReport.location)
async def admin_add_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text.strip())
    await message.answer("📝 Введите описание инцидента:")
    await state.set_state(AdminAddReport.description)

@dp.message(AdminAddReport.description)
async def admin_add_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await message.answer("📸 Отправьте фото или видео (или напишите `skip`):")
    await state.set_state(AdminAddReport.media)

@dp.message(AdminAddReport.media, F.photo | F.video)
async def admin_add_media(message: types.Message, state: FSMContext):
    if message.photo:
        await state.update_data(media_id=message.photo[-1].file_id, media_type="photo")
    else:
        await state.update_data(media_id=message.video.file_id, media_type="video")
    await message.answer("📌 Выберите статус:", reply_markup=kb_add_report_status())
    await state.set_state(AdminAddReport.status)

@dp.message(AdminAddReport.media, F.text.lower() == "skip")
async def admin_add_media_skip(message: types.Message, state: FSMContext):
    await state.update_data(media_id=None, media_type=None)
    await message.answer("📌 Выберите статус:", reply_markup=kb_add_report_status())
    await state.set_state(AdminAddReport.status)

@dp.message(AdminAddReport.media)
async def admin_add_media_wrong(message: types.Message):
    await message.answer("⚠️ Отправьте фото, видео или напишите `skip`.")

@dp.callback_query(F.data.startswith("newrep_"))
async def admin_add_finish(callback: types.CallbackQuery, state: FSMContext):
    status = callback.data.split("newrep_")[1]
    data   = await state.get_data()
    await state.clear()
    report_id = db.create_report(
        driver_id   = data['driver_id'],
        location    = data['location'],
        media_id    = data.get('media_id') or "",
        media_type  = data.get('media_type') or "photo",
        description = data.get('description', ''),
        status      = status
    )
    if status == 'approved':
        db.cursor.execute(
            "UPDATE users SET points=points+1 WHERE user_id=?", (data['driver_id'],))
        db.conn.commit()
    await callback.message.edit_text(
        f"✅ Репорт *#{report_id}* создан со статусом *{status.upper()}*",
        parse_mode="Markdown")
    await callback.answer("✅ Репорт добавлен")
    await bot.send_message(callback.from_user.id, "Выберите действие:", reply_markup=kb_admin())

# --- Редактировать репорт ---
@dp.callback_query(F.data.startswith("edit_"))
async def admin_edit_start(callback: types.CallbackQuery, state: FSMContext):
    r_id = int(callback.data.split("edit_")[1])
    r    = db.get_report(r_id)
    if not r:
        await callback.answer("Репорт не найден.")
        return
    await state.update_data(report_id=r_id, old_location=r[2], old_desc=r[5])
    await state.set_state(AdminEditReport.location)
    await callback.message.answer(
        f"✏️ *Редактирование репорта #{r_id}*\n\n"
        f"Текущая локация: `{r[2]}`\n"
        f"Текущее описание: {r[5] or '—'}\n\n"
        f"Введите новую локацию (или `skip`):",
        parse_mode="Markdown")
    await callback.answer()

@dp.message(AdminEditReport.location)
async def admin_edit_location(message: types.Message, state: FSMContext):
    data    = await state.get_data()
    new_loc = data['old_location'] if message.text.strip().lower() == 'skip' else message.text.strip()
    await state.update_data(location=new_loc)
    await message.answer("📝 Введите новое описание (или `skip`):")
    await state.set_state(AdminEditReport.description)

@dp.message(AdminEditReport.description)
async def admin_edit_finish(message: types.Message, state: FSMContext):
    data     = await state.get_data()
    r_id     = data['report_id']
    new_desc = data['old_desc'] if message.text.strip().lower() == 'skip' else message.text.strip()
    db.update_report(r_id, data['location'], new_desc)
    await state.clear()
    await message.answer(
        f"✅ Репорт *#{r_id}* обновлён.\n📍 {data['location']}\n📝 {new_desc}",
        reply_markup=kb_admin(), parse_mode="Markdown")

# --- Удалить репорт ---
@dp.callback_query(F.data.startswith("del_"))
async def admin_delete_confirm(callback: types.CallbackQuery):
    r_id = int(callback.data.split("del_")[1])
    await callback.message.answer(
        f"🗑 Удалить репорт *#{r_id}*?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да", callback_data=f"delok_{r_id}"),
            InlineKeyboardButton(text="❌ Нет", callback_data="delcancel")
        ]]), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("delok_"))
async def admin_delete_do(callback: types.CallbackQuery):
    r_id = int(callback.data.split("delok_")[1])
    res  = db.delete_report(r_id)
    if res and res[1] == 'approved':
        db.cursor.execute(
            "UPDATE users SET points=MAX(0, points-1) WHERE user_id=?", (res[0],))
        db.conn.commit()
    await callback.message.edit_text(f"🗑 Репорт *#{r_id}* удалён.", parse_mode="Markdown")
    await callback.answer("🗑 Удалено")

@dp.callback_query(F.data == "delcancel")
async def admin_delete_cancel(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Удаление отменено.")
    await callback.answer()

# ==========================================
# АДМИН — УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ
# ==========================================
@dp.message(F.text == "👥 Управление пользователями")
async def admin_manage_users(message: types.Message):
    if message.from_user.id != SUPER_ADMIN_ID: return
    users = db.get_all_users()
    if not users:
        await message.answer("👥 Пользователей нет.")
        return
    await message.answer(f"👥 *Пользователи ({len(users)}):*", parse_mode="Markdown")
    for u in users:
        uid, role, emp_id, points, banned = u
        status = "🚫 БАН" if banned else "✅ Активен"
        emp    = f"Таб. №{emp_id}" if emp_id else "—"
        await message.answer(
            f"{status} | *{role.upper()}*\n🆔 `{uid}` | {emp}\n⭐ Баллов: {points}",
            reply_markup=kb_user_actions(uid, banned, role), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("ban_"))
async def admin_ban_user(callback: types.CallbackQuery):
    uid = int(callback.data.split("ban_")[1])
    db.set_banned(uid, 1)
    await callback.message.edit_text(
        f"🚫 Пользователь `{uid}` заблокирован.", parse_mode="Markdown")
    await callback.answer("🚫 Забанен")
    try:
        await bot.send_message(uid, "⛔ Ваш аккаунт заблокирован администратором.")
    except Exception: pass

@dp.callback_query(F.data.startswith("unban_"))
async def admin_unban_user(callback: types.CallbackQuery):
    uid = int(callback.data.split("unban_")[1])
    db.set_banned(uid, 0)
    await callback.message.edit_text(
        f"✅ Пользователь `{uid}` разблокирован.", parse_mode="Markdown")
    await callback.answer("✅ Разбанен")
    try:
        await bot.send_message(uid, "✅ Аккаунт разблокирован. Напишите /start.")
    except Exception: pass

@dp.callback_query(F.data.startswith("role_driver_"))
async def admin_set_driver(callback: types.CallbackQuery):
    uid = int(callback.data.split("role_driver_")[1])
    db.set_role(uid, 'driver')
    await callback.message.edit_text(
        f"🚌 `{uid}` теперь *водитель*.", parse_mode="Markdown")
    await callback.answer("✅ Роль изменена")

@dp.callback_query(F.data.startswith("role_passenger_"))
async def admin_set_passenger(callback: types.CallbackQuery):
    uid = int(callback.data.split("role_passenger_")[1])
    db.set_role(uid, 'passenger')
    await callback.message.edit_text(
        f"👤 `{uid}` теперь *пассажир*.", parse_mode="Markdown")
    await callback.answer("✅ Роль изменена")

# ==========================================
# АДМИН — рассылка
# ==========================================
@dp.message(F.text == "📢 Рассылка водителям")
async def broadcast_drivers_start(message: types.Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID: return
    await state.update_data(target="drivers")
    await message.answer("✏️ Текст рассылки для *водителей*:",
                         reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    await state.set_state(BroadcastState.text)

@dp.message(F.text == "📣 Рассылка пассажирам")
async def broadcast_passengers_start(message: types.Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID: return
    await state.update_data(target="passengers")
    await message.answer("✏️ Текст рассылки для *пассажиров*:",
                         reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    await state.set_state(BroadcastState.text)

@dp.message(BroadcastState.text)
async def broadcast_send(message: types.Message, state: FSMContext):
    data       = await state.get_data()
    target     = data.get('target', 'drivers')
    text       = message.text.strip()
    await state.clear()
    recipients = db.get_all_drivers() if target == 'drivers' else db.get_all_passengers()
    label      = "водителям" if target == 'drivers' else "пассажирам"
    sent, failed = 0, 0
    for (uid,) in recipients:
        try:
            await bot.send_message(uid, f"📢 *Оповещение CTS:*\n\n{text}", parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1
    await message.answer(
        f"✅ Рассылка {label} завершена.\n📨 Отправлено: *{sent}* | ❌ Ошибок: *{failed}*",
        reply_markup=kb_admin(), parse_mode="Markdown")

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    logging.info("CTS Driver Network Bot запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
