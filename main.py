import asyncio
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
                user_id   INTEGER PRIMARY KEY,
                username  TEXT,
                full_name TEXT,
                role      TEXT    DEFAULT 'pending',
                emp_id    TEXT,
                points    INTEGER DEFAULT 0,
                banned    INTEGER DEFAULT 0
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id     INTEGER NOT NULL,
                driver_name   TEXT,
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
        migrations = [
            ("users",   "username",    "TEXT"),
            ("users",   "full_name",   "TEXT"),
            ("users",   "banned",      "INTEGER DEFAULT 0"),
            ("reports", "driver_name", "TEXT"),
            ("reports", "media_type",  "TEXT"),
            ("reports", "description", "TEXT"),
        ]
        for table, col, definition in migrations:
            try:
                self.cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            except Exception:
                pass
        self.conn.commit()

    # ---------- пользователи ----------
    def register_user(self, user_id: int, username: str, full_name: str):
        """Регистрирует нового юзера со статусом pending"""
        self.cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, role) VALUES (?, ?, ?, 'pending')",
            (user_id, username, full_name))
        # Обновляем имя если уже есть
        self.cursor.execute(
            "UPDATE users SET username=?, full_name=? WHERE user_id=?",
            (username, full_name, user_id))
        self.conn.commit()

    def upsert_admin(self, user_id: int):
        self.cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, role) VALUES (?, 'admin')", (user_id,))
        self.cursor.execute(
            "UPDATE users SET role='admin' WHERE user_id=?", (user_id,))
        self.conn.commit()

    def get_user(self, user_id: int):
        return self.cursor.execute(
            "SELECT user_id, username, full_name, role, emp_id, points, banned FROM users WHERE user_id=?",
            (user_id,)).fetchone()

    def set_role(self, user_id: int, role: str):
        self.cursor.execute("UPDATE users SET role=? WHERE user_id=?", (role, user_id))
        self.conn.commit()

    def set_emp_id(self, user_id: int, emp_id: str):
        self.cursor.execute("UPDATE users SET emp_id=? WHERE user_id=?", (emp_id, user_id))
        self.conn.commit()

    def set_banned(self, user_id: int, banned: int):
        self.cursor.execute("UPDATE users SET banned=? WHERE user_id=?", (banned, user_id))
        self.conn.commit()

    def get_pending_users(self):
        return self.cursor.execute(
            "SELECT user_id, username, full_name FROM users WHERE role='pending' AND banned=0"
        ).fetchall()

    def get_all_users(self):
        return self.cursor.execute(
            "SELECT user_id, username, full_name, role, emp_id, points, banned "
            "FROM users WHERE user_id != ?", (SUPER_ADMIN_ID,)
        ).fetchall()

    def get_all_drivers(self):
        return self.cursor.execute(
            "SELECT user_id FROM users WHERE role='driver' AND banned=0").fetchall()

    def get_all_passengers(self):
        return self.cursor.execute(
            "SELECT user_id FROM users WHERE role='passenger' AND banned=0").fetchall()

    # ---------- репорты ----------
    def create_report(self, driver_id, driver_name, location, media_id, media_type,
                      drone_req=0, description="", status="pending"):
        self.cursor.execute(
            "INSERT INTO reports (driver_id, driver_name, location, media_id, media_type, "
            "drone_req, description, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (driver_id, driver_name, location, media_id, media_type, drone_req, description, status))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_report(self, report_id: int):
        return self.cursor.execute(
            "SELECT id, driver_id, driver_name, location, media_id, media_type, "
            "description, drone_req, status FROM reports WHERE id=?", (report_id,)).fetchone()

    def get_pending_reports(self):
        return self.cursor.execute(
            "SELECT id, driver_id, driver_name, location, media_id, media_type, drone_req, description "
            "FROM reports WHERE status='pending'").fetchall()

    def get_all_reports(self, limit=15):
        return self.cursor.execute(
            "SELECT id, driver_id, driver_name, location, status, drone_req, media_type "
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
        pending_users = self.cursor.execute("SELECT COUNT(*) FROM users WHERE role='pending'").fetchone()[0]
        banned   = self.cursor.execute("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0]
        return total, pending, approved, drivers, pending_users, banned


db = CTSDatabase()
db.upsert_admin(SUPER_ADMIN_ID)

# ==========================================
# МАШИНА СОСТОЯНИЙ
# ==========================================
class ReportState(StatesGroup):
    media    = State()
    location = State()

class RejectState(StatesGroup):
    reason = State()

class BroadcastState(StatesGroup):
    text = State()

class AdminAddReport(StatesGroup):
    location    = State()
    description = State()
    media       = State()
    status      = State()

class AdminEditReport(StatesGroup):
    report_id   = State()
    location    = State()
    description = State()

class AdminAssignRole(StatesGroup):
    user_id = State()
    role    = State()
    emp_id  = State()

# ==========================================
# КЛАВИАТУРЫ
# ==========================================
def kb_admin():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📋 Репорты на проверку"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="👥 Пользователи"),        KeyboardButton(text="⏳ Заявки на вступление")],
        [KeyboardButton(text="📢 Рассылка водителям"),  KeyboardButton(text="📣 Рассылка пассажирам")],
        [KeyboardButton(text="🛠 Управление репортами"), KeyboardButton(text="➕ Добавить репорт")],
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

def kb_send_location():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
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
    ban_text  = "🔓 Разбанить" if banned else "🚫 Забанить"
    ban_data  = f"unban_{user_id}" if banned else f"ban_{user_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚌 Водитель",   callback_data=f"role_driver_{user_id}"),
            InlineKeyboardButton(text="👤 Пассажир",   callback_data=f"role_passenger_{user_id}"),
        ],
        [
            InlineKeyboardButton(text=ban_text, callback_data=ban_data),
        ]
    ])

def kb_approve_user(user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚌 Водитель",  callback_data=f"approve_driver_{user_id}"),
        InlineKeyboardButton(text="👤 Пассажир",  callback_data=f"approve_passenger_{user_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"approve_reject_{user_id}"),
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
# /start — только регистрация, роль выдаёт админ
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid       = message.from_user.id
    username  = message.from_user.username or ""
    full_name = message.from_user.full_name or ""

    # Супер-админ
    if uid == SUPER_ADMIN_ID:
        db.upsert_admin(uid)
        await message.answer(
            "👑 *SUPERADMIN ACTIVATED*\nДобро пожаловать в CTS Driver Network.",
            reply_markup=kb_admin(), parse_mode="Markdown")
        return

    user = db.get_user(uid)

    if user:
        role   = user[3]
        banned = user[6]

        if banned:
            await message.answer("⛔ Ваш аккаунт заблокирован администратором.")
            return

        if role == 'driver':
            await message.answer(f"👋 Добро пожаловать, *{full_name}*!",
                                 reply_markup=kb_driver(), parse_mode="Markdown")
        elif role == 'passenger':
            await message.answer(f"👋 Добро пожаловать, *{full_name}*!",
                                 reply_markup=kb_passenger(), parse_mode="Markdown")
        elif role == 'pending':
            await message.answer(
                "⏳ Ваша заявка уже отправлена.\n"
                "Ожидайте подтверждения от администратора.")
        return

    # Новый пользователь — регистрируем как pending
    db.register_user(uid, username, full_name)

    await message.answer(
        f"👋 Здравствуйте, *{full_name}*!\n\n"
        f"Ваша заявка на вступление в *CTS Driver Network* отправлена.\n"
        f"Ожидайте — администратор назначит вам роль.",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")

    # Уведомление админу
    uname_str = f"@{username}" if username else "—"
    await bot.send_message(
        SUPER_ADMIN_ID,
        f"🔔 *Новая заявка на вступление!*\n\n"
        f"👤 Имя: *{full_name}*\n"
        f"🔗 Username: {uname_str}\n"
        f"🆔 ID: `{uid}`",
        reply_markup=kb_approve_user(uid),
        parse_mode="Markdown"
    )

# ==========================================
# АДМИН — одобрение заявок прямо из уведомления
# ==========================================
@dp.callback_query(F.data.startswith("approve_driver_"))
async def approve_as_driver(callback: types.CallbackQuery):
    uid = int(callback.data.split("approve_driver_")[1])
    db.set_role(uid, 'driver')
    user = db.get_user(uid)
    name = user[2] if user else str(uid)
    await callback.message.edit_text(
        f"✅ *{name}* (`{uid}`) назначен *водителем*.", parse_mode="Markdown")
    await callback.answer("✅ Назначен водителем")
    try:
        await bot.send_message(
            uid,
            "✅ Ваша заявка одобрена!\n\n"
            "Вам назначена роль *🚌 Водитель*.\n"
            "Нажмите /start чтобы начать.",
            parse_mode="Markdown")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("approve_passenger_"))
async def approve_as_passenger(callback: types.CallbackQuery):
    uid = int(callback.data.split("approve_passenger_")[1])
    db.set_role(uid, 'passenger')
    user = db.get_user(uid)
    name = user[2] if user else str(uid)
    await callback.message.edit_text(
        f"✅ *{name}* (`{uid}`) назначен *пассажиром*.", parse_mode="Markdown")
    await callback.answer("✅ Назначен пассажиром")
    try:
        await bot.send_message(
            uid,
            "✅ Ваша заявка одобрена!\n\n"
            "Вам назначена роль *👤 Пассажир*.\n"
            "Нажмите /start чтобы начать.",
            parse_mode="Markdown")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("approve_reject_"))
async def approve_reject(callback: types.CallbackQuery):
    uid = int(callback.data.split("approve_reject_")[1])
    db.set_banned(uid, 1)
    user = db.get_user(uid)
    name = user[2] if user else str(uid)
    await callback.message.edit_text(
        f"❌ Заявка *{name}* (`{uid}`) отклонена.", parse_mode="Markdown")
    await callback.answer("❌ Отклонено")
    try:
        await bot.send_message(uid, "❌ Ваша заявка на вступление отклонена администратором.")
    except Exception:
        pass

# ==========================================
# АДМИН — список pending заявок
# ==========================================
@dp.message(F.text == "⏳ Заявки на вступление")
async def show_pending_users(message: types.Message):
    if message.from_user.id != SUPER_ADMIN_ID: return
    pending = db.get_pending_users()
    if not pending:
        await message.answer("✅ Новых заявок нет.")
        return
    await message.answer(f"⏳ *Заявки на вступление ({len(pending)}):*", parse_mode="Markdown")
    for uid, username, full_name in pending:
        uname_str = f"@{username}" if username else "—"
        await message.answer(
            f"👤 *{full_name}*\n{uname_str}\n🆔 `{uid}`",
            reply_markup=kb_approve_user(uid),
            parse_mode="Markdown")

# ==========================================
# ВОДИТЕЛЬ — РЕПОРТ (2 шага: медиа → геолокация)
# ==========================================
async def start_report_flow(message: types.Message, state: FSMContext, drone_req: int = 0):
    user = db.get_user(message.from_user.id)
    if not user or user[3] != 'driver':
        await message.answer("⛔ Только водители могут отправлять репорты.")
        return
    await state.update_data(drone_req=drone_req)
    drone_label = " 🚁 [ДРОН-АУДИТ]" if drone_req else ""
    await message.answer(
        f"📋 *Создание репорта{drone_label}*\n\n"
        f"*Шаг 1 из 2* — Отправьте 📸 фото или 🎥 видео инцидента.\n"
        f"Можно добавить описание в подписи к медиафайлу.",
        reply_markup=ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="❌ Отменить репорт")]
        ], resize_keyboard=True),
        parse_mode="Markdown")
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
    kb   = kb_driver() if (user and user[3] == 'driver') else kb_passenger()
    await message.answer("❌ Репорт отменён.", reply_markup=kb)

@dp.message(ReportState.media, F.photo | F.video)
async def process_media(message: types.Message, state: FSMContext):
    if message.photo:
        media_id, media_type, icon = message.photo[-1].file_id, "photo", "📸 Фото"
    else:
        media_id, media_type, icon = message.video.file_id, "video", "🎥 Видео"

    await state.update_data(media_id=media_id, media_type=media_type,
                            description=message.caption or "")
    await message.answer(
        f"✅ {icon} получено!\n\n"
        f"*Шаг 2 из 2* — Отправьте 📍 геолокацию.\n"
        f"Нажмите кнопку ниже — телефон определит координаты автоматически.",
        reply_markup=kb_send_location(), parse_mode="Markdown")
    await state.set_state(ReportState.location)

@dp.message(ReportState.media)
async def media_wrong(message: types.Message):
    await message.answer("⚠️ Отправьте *фото* или *видео* инцидента.", parse_mode="Markdown")

@dp.message(ReportState.location, F.location)
async def process_location(message: types.Message, state: FSMContext):
    data        = await state.get_data()
    lat, lon    = message.location.latitude, message.location.longitude
    location    = f"{lat}, {lon}"
    maps_link   = f"https://maps.google.com/?q={lat},{lon}"
    user        = db.get_user(message.from_user.id)
    driver_name = user[2] if user else str(message.from_user.id)

    report_id = db.create_report(
        driver_id   = message.from_user.id,
        driver_name = driver_name,
        location    = location,
        media_id    = data['media_id'],
        media_type  = data['media_type'],
        drone_req   = data.get('drone_req', 0),
        description = data.get('description', '')
    )
    await state.clear()

    drone_label = " 🚁 [ДРОН-АУДИТ]" if data.get('drone_req') else ""
    await message.answer(
        f"✅ *Репорт #{report_id} отправлен{drone_label}!*\n"
        f"📍 [{location}]({maps_link})\n"
        f"Ожидайте подтверждения администратора.",
        reply_markup=kb_driver(), parse_mode="Markdown", disable_web_page_preview=True)

    flag    = "🚁 *ДРОН-АУДИТ ЗАПРОШЕН*\n\n" if data.get('drone_req') else ""
    caption = (
        f"{flag}📦 Новый репорт *#{report_id}*\n"
        f"👤 {driver_name} (`{message.from_user.id}`)\n"
        f"📍 [{location}]({maps_link})\n"
        f"📝 {data.get('description') or '—'}"
    )
    send_media = bot.send_photo if data['media_type'] == "photo" else bot.send_video
    media_key  = "photo" if data['media_type'] == "photo" else "video"
    await send_media(SUPER_ADMIN_ID, **{media_key: data['media_id']},
                     caption=caption, reply_markup=kb_report_actions(report_id),
                     parse_mode="Markdown")
    await bot.send_location(SUPER_ADMIN_ID, latitude=lat, longitude=lon)

@dp.message(ReportState.location)
async def location_wrong(message: types.Message):
    await message.answer(
        "⚠️ Нажмите кнопку *«📍 Отправить геолокацию»* ниже.",
        reply_markup=kb_send_location(), parse_mode="Markdown")

# ==========================================
# ВОДИТЕЛЬ — бонусы и история
# ==========================================
@dp.message(F.text == "📜 Мои репорты")
async def my_reports(message: types.Message):
    user = db.get_user(message.from_user.id)
    if not user or user[3] != 'driver':
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
    pts  = user[5] if user else 0
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
# АДМИН — репорты
# ==========================================
@dp.message(F.text == "📋 Репорты на проверку")
async def show_pending_reports(message: types.Message):
    if message.from_user.id != SUPER_ADMIN_ID: return
    pending = db.get_pending_reports()
    if not pending:
        await message.answer("✅ Новых репортов нет.")
        return
    await message.answer(f"📋 Ожидают проверки: *{len(pending)}*", parse_mode="Markdown")
    for r in pending:
        rid, driver_id, driver_name, location, media_id, media_type, drone_req, desc = r
        drone     = "🚁 *ДРОН-АУДИТ*\n" if drone_req else ""
        maps_link = f"https://maps.google.com/?q={location.replace(' ','')}"
        caption   = (
            f"{drone}📦 Репорт *#{rid}*\n"
            f"👤 {driver_name} (`{driver_id}`)\n"
            f"📍 [{location}]({maps_link})\n"
            f"📝 {desc or '—'}"
        )
        try:
            send_fn   = bot.send_photo if media_type == "photo" else bot.send_video
            media_key = "photo" if media_type == "photo" else "video"
            await send_fn(message.chat.id, **{media_key: media_id},
                          caption=caption, reply_markup=kb_report_actions(rid),
                          parse_mode="Markdown")
        except Exception:
            await message.answer(caption, reply_markup=kb_report_actions(rid),
                                 parse_mode="Markdown")

@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    if message.from_user.id != SUPER_ADMIN_ID: return
    total, pending, approved, drivers, pending_users, banned = db.get_stats()
    await message.answer(
        f"📊 *Статистика CTS Driver Network*\n\n"
        f"👷 Водителей: *{drivers}*\n"
        f"⏳ Ожидают роли: *{pending_users}*\n"
        f"🚫 Забанено: *{banned}*\n\n"
        f"📦 Всего репортов: *{total}*\n"
        f"⏳ На проверке: *{pending}*\n"
        f"✅ Подтверждено: *{approved}*",
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
        pts  = user[5] if user else 0
        await bot.send_message(
            driver_id,
            f"🌟 Репорт *#{r_id}* подтверждён! +1 балл. Итого: *{pts}*\n"
            f"📅 Дней отдыха: *{pts // 4}*",
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
    driver_id = db.reject_report(r_id, message.text.strip())
    await state.clear()
    await message.answer(f"📋 Репорт *#{r_id}* отклонён.", parse_mode="Markdown")
    if driver_id:
        await bot.send_message(driver_id,
            f"❌ Репорт *#{r_id}* отклонён.\n📝 Причина: _{message.text.strip()}_",
            parse_mode="Markdown")

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
        rid, driver_id, driver_name, location, status, drone_req, media_type = r
        icon  = icons.get(status, '❓')
        drone = " 🚁" if drone_req else ""
        mtype = "🎥" if media_type == "video" else "📸"
        await message.answer(
            f"{icon}{mtype} Репорт *#{rid}*{drone}\n"
            f"👤 {driver_name} | 📍 {location} | {status}",
            reply_markup=kb_report_manage(rid), parse_mode="Markdown")

# --- Добавить репорт от имени супер-админа ---
@dp.message(F.text == "➕ Добавить репорт")
async def admin_add_report_start(message: types.Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID: return
    await message.answer(
        "➕ *Добавление репорта*\n\n"
        "Репорт будет создан от вашего имени (супер-админ).\n\n"
        "📍 Введите локацию (например: 51.1283, 71.4305 или название места):",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    await state.set_state(AdminAddReport.location)

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
    await message.answer("📌 Выберите статус репорта:", reply_markup=kb_add_report_status())
    await state.set_state(AdminAddReport.status)

@dp.message(AdminAddReport.media, F.text.lower() == "skip")
async def admin_add_media_skip(message: types.Message, state: FSMContext):
    await state.update_data(media_id="", media_type="photo")
    await message.answer("📌 Выберите статус репорта:", reply_markup=kb_add_report_status())
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
        driver_id   = SUPER_ADMIN_ID,
        driver_name = "Супер-Админ",
        location    = data['location'],
        media_id    = data.get('media_id', ''),
        media_type  = data.get('media_type', 'photo'),
        description = data.get('description', ''),
        status      = status
    )
    if status == 'approved':
        db.cursor.execute(
            "UPDATE users SET points=points+1 WHERE user_id=?", (SUPER_ADMIN_ID,))
        db.conn.commit()

    await callback.message.edit_text(
        f"✅ Репорт *#{report_id}* создан от имени *Супер-Админа*\n"
        f"📍 {data['location']} | Статус: *{status.upper()}*",
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
    await state.update_data(report_id=r_id, old_location=r[3], old_desc=r[6])
    await state.set_state(AdminEditReport.location)
    await callback.message.answer(
        f"✏️ *Редактирование репорта #{r_id}*\n\n"
        f"Текущая локация: `{r[3]}`\nТекущее описание: {r[6] or '—'}\n\n"
        f"Введите новую локацию (или `skip`):", parse_mode="Markdown")
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
@dp.message(F.text == "👥 Пользователи")
async def admin_manage_users(message: types.Message):
    if message.from_user.id != SUPER_ADMIN_ID: return
    users = db.get_all_users()
    if not users:
        await message.answer("👥 Пользователей нет.")
        return
    await message.answer(f"👥 *Все пользователи ({len(users)}):*", parse_mode="Markdown")
    for u in users:
        uid, username, full_name, role, emp_id, points, banned = u
        status    = "🚫 БАН" if banned else "✅"
        uname_str = f"@{username}" if username else "—"
        role_icon = {"driver": "🚌", "passenger": "👤", "pending": "⏳"}.get(role, "❓")
        await message.answer(
            f"{status} {role_icon} *{full_name}*\n"
            f"{uname_str} | 🆔 `{uid}`\n"
            f"⭐ Баллов: {points}",
            reply_markup=kb_user_actions(uid, banned, role),
            parse_mode="Markdown")

@dp.callback_query(F.data.startswith("ban_"))
async def admin_ban(callback: types.CallbackQuery):
    uid = int(callback.data.split("ban_")[1])
    db.set_banned(uid, 1)
    await callback.message.edit_text(
        f"🚫 Пользователь `{uid}` заблокирован.", parse_mode="Markdown")
    await callback.answer("🚫 Забанен")
    try:
        await bot.send_message(uid, "⛔ Ваш аккаунт заблокирован администратором.")
    except Exception: pass

@dp.callback_query(F.data.startswith("unban_"))
async def admin_unban(callback: types.CallbackQuery):
    uid = int(callback.data.split("unban_")[1])
    db.set_banned(uid, 0)
    await callback.message.edit_text(
        f"✅ Пользователь `{uid}` разблокирован.", parse_mode="Markdown")
    await callback.answer("✅ Разбанен")
    try:
        await bot.send_message(uid, "✅ Аккаунт разблокирован. Напишите /start.")
    except Exception: pass

@dp.callback_query(F.data.startswith("role_driver_"))
async def set_driver(callback: types.CallbackQuery):
    uid = int(callback.data.split("role_driver_")[1])
    db.set_role(uid, 'driver')
    await callback.message.edit_text(
        f"🚌 `{uid}` теперь *водитель*.", parse_mode="Markdown")
    await callback.answer("✅ Роль изменена")
    try:
        await bot.send_message(uid, "🚌 Ваша роль изменена на *Водитель*. Напишите /start.",
                               parse_mode="Markdown")
    except Exception: pass

@dp.callback_query(F.data.startswith("role_passenger_"))
async def set_passenger(callback: types.CallbackQuery):
    uid = int(callback.data.split("role_passenger_")[1])
    db.set_role(uid, 'passenger')
    await callback.message.edit_text(
        f"👤 `{uid}` теперь *пассажир*.", parse_mode="Markdown")
    await callback.answer("✅ Роль изменена")
    try:
        await bot.send_message(uid, "👤 Ваша роль изменена на *Пассажир*. Напишите /start.",
                               parse_mode="Markdown")
    except Exception: pass

# ==========================================
# АДМИН — рассылка
# ==========================================
@dp.message(F.text == "📢 Рассылка водителям")
async def broadcast_drivers(message: types.Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID: return
    await state.update_data(target="drivers")
    await message.answer("✏️ Текст рассылки для *водителей*:",
                         reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    await state.set_state(BroadcastState.text)

@dp.message(F.text == "📣 Рассылка пассажирам")
async def broadcast_passengers(message: types.Message, state: FSMContext):
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
        f"✅ Рассылка {label}: *{sent}* доставлено, *{failed}* ошибок.",
        reply_markup=kb_admin(), parse_mode="Markdown")

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    logging.info("CTS Driver Network Bot запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
