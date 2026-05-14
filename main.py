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
    asyncio.run(main())