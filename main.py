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

BOT_TOKEN = "8643364597:AAE2o2zq4kNuKwVxxRzrRbiTfgI2TJXgwhs"
SUPER_ADMIN_ID = 5967495207

logging.basicConfig(level=logging.INFO)

# ==========================================
# БАЗА ДАННЫХ
# ==========================================
class CTSDatabase:
    def __init__(self, db_path="cts_infrastructure.db"):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.init_tables()

    def init_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                role      TEXT,
                status    TEXT DEFAULT 'pending',
                full_name TEXT,
                points    INTEGER DEFAULT 0
            )''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER,
                location  TEXT,
                photo_id  TEXT,
                status    TEXT
            )''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS drone_audits (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                location    TEXT,
                requested_by INTEGER,
                status      TEXT DEFAULT 'pending'
            )''')
        self.conn.commit()

    def get_user(self, user_id):
        return self.cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

    def register_user(self, user_id, full_name, role):
        self.cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, full_name, role, status) VALUES (?,?,?,'pending')",
            (user_id, full_name, role))
        self.conn.commit()

    def approve_user(self, user_id):
        self.cursor.execute("UPDATE users SET status='approved' WHERE user_id=?", (user_id,))
        self.conn.commit()

    def reject_user(self, user_id):
        self.cursor.execute("UPDATE users SET status='rejected' WHERE user_id=?", (user_id,))
        self.conn.commit()

    def bind_superadmin(self, user_id):
        self.cursor.execute(
            "INSERT OR REPLACE INTO users (user_id, role, status, full_name) VALUES (?,'admin','approved','SuperAdmin')",
            (user_id,))
        self.conn.commit()

    def get_pending_users(self):
        return self.cursor.execute(
            "SELECT user_id, full_name, role FROM users WHERE status='pending'").fetchall()

    def get_all_users(self):
        return self.cursor.execute(
            "SELECT user_id, full_name, role, status, points FROM users ORDER BY points DESC").fetchall()

    def get_all_drivers(self):
        return self.cursor.execute(
            "SELECT user_id FROM users WHERE role='driver' AND status='approved'").fetchall()

    def get_all_approved(self):
        return self.cursor.execute(
            "SELECT user_id FROM users WHERE status='approved'").fetchall()

    def adjust_points(self, user_id, delta):
        self.cursor.execute(
            "UPDATE users SET points = MAX(0, points + ?) WHERE user_id=?", (delta, user_id))
        self.conn.commit()

    def create_report(self, driver_id, location, photo_id):
        self.cursor.execute(
            "INSERT INTO reports (driver_id, location, photo_id, status) VALUES (?,?,?,'pending')",
            (driver_id, location, photo_id))
        self.conn.commit()
        return self.cursor.lastrowid

    def update_report_status(self, report_id, status):
        self.cursor.execute("UPDATE reports SET status=? WHERE id=?", (status, report_id))
        if status == 'approved':
            res = self.cursor.execute("SELECT driver_id FROM reports WHERE id=?", (report_id,)).fetchone()
            if res:
                self.cursor.execute("UPDATE users SET points=points+1 WHERE user_id=?", (res[0],))
        self.conn.commit()

    def get_pending_reports(self):
        return self.cursor.execute("SELECT * FROM reports WHERE status='pending'").fetchall()

    def get_stats(self):
        total   = self.cursor.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        approved= self.cursor.execute("SELECT COUNT(*) FROM reports WHERE status='approved'").fetchone()[0]
        rejected= self.cursor.execute("SELECT COUNT(*) FROM reports WHERE status='rejected'").fetchone()[0]
        pending = self.cursor.execute("SELECT COUNT(*) FROM reports WHERE status='pending'").fetchone()[0]
        users   = self.cursor.execute("SELECT COUNT(*) FROM users WHERE status='approved'").fetchone()[0]
        top     = self.cursor.execute(
            "SELECT full_name, points FROM users WHERE role='driver' AND status='approved' ORDER BY points DESC LIMIT 5"
        ).fetchall()
        return total, approved, rejected, pending, users, top

    def create_drone_audit(self, location, requested_by):
        self.cursor.execute(
            "INSERT INTO drone_audits (location, requested_by, status) VALUES (?,?,'pending')",
            (location, requested_by))
        self.conn.commit()
        return self.cursor.lastrowid

db = CTSDatabase()

# ==========================================
# FSM
# ==========================================
class RegisterState(StatesGroup):
    role = State()

class ReportState(StatesGroup):
    location = State()
    photo    = State()

class AdminReportState(StatesGroup):
    location = State()
    photo    = State()

class BroadcastState(StatesGroup):
    text = State()

class DroneState(StatesGroup):
    location = State()

class AdjustPointsState(StatesGroup):
    user_id = State()
    delta   = State()

# ==========================================
# КЛАВИАТУРЫ
# ==========================================
def get_superadmin_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Статистика системы"),   KeyboardButton(text="📋 Проверить репорты")],
        [KeyboardButton(text="➕ Создать репорт"),        KeyboardButton(text="👥 Заявки на регистрацию")],
        [KeyboardButton(text="👤 Все пользователи"),      KeyboardButton(text="✏️ Изменить баллы")],
        [KeyboardButton(text="📢 Рассылка"),              KeyboardButton(text="🚁 Дрон-аудит")],
        [KeyboardButton(text="🎁 Мои бонусы"),
         KeyboardButton(text="🗺 Карта",
                        web_app=WebAppInfo(url="https://yandex.kz/maps/163/astana/"))]
    ], resize_keyboard=True)

def get_driver_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚨 Сообщить об инциденте")],
        [KeyboardButton(text="🎁 Мои бонусы")]
    ], resize_keyboard=True)

def get_passenger_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🗺 Карта пробок",
                        web_app=WebAppInfo(url="https://yandex.kz/maps/163/astana/"))]
    ], resize_keyboard=True)

def get_role_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚌 Я водитель")],
        [KeyboardButton(text="👤 Я пассажир")]
    ], resize_keyboard=True, one_time_keyboard=True)

def get_cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]],
                               resize_keyboard=True)

def user_approve_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"usr_ok_{user_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"usr_no_{user_id}")
    ]])

def role_kb(role):
    if role == 'admin':   return get_superadmin_kb()
    if role == 'driver':  return get_driver_kb()
    return get_passenger_kb()

def is_admin(uid): return uid == SUPER_ADMIN_ID

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

# ==========================================
# CANCEL — универсальная отмена FSM
# ==========================================
@dp.message(F.text == "❌ Отмена")
async def cancel_any(message: types.Message, state: FSMContext):
    await state.clear()
    kb = get_superadmin_kb() if is_admin(message.from_user.id) else get_driver_kb()
    await message.answer("↩️ Отменено.", reply_markup=kb)

# ==========================================
# /start
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if is_admin(uid):
        db.bind_superadmin(uid)
        await message.answer("👑 *SUPERADMIN ACTIVATED*\nДобро пожаловать в CTS.",
                             reply_markup=get_superadmin_kb(), parse_mode="Markdown")
        return

    user = db.get_user(uid)
    if user and user[2] == 'approved':
        await message.answer(f"👋 С возвращением! Роль: *{user[1].upper()}*",
                             reply_markup=role_kb(user[1]), parse_mode="Markdown")
        return
    if user and user[2] == 'pending':
        await message.answer("⏳ Заявка уже отправлена. Ожидайте одобрения."); return
    if user and user[2] == 'rejected':
        await message.answer("❌ Заявка отклонена. Обратитесь к администратору."); return

    await message.answer("👋 Добро пожаловать в CTS!\n\nВыберите вашу роль:",
                         reply_markup=get_role_kb())
    await state.set_state(RegisterState.role)

# ==========================================
# РЕГИСТРАЦИЯ
# ==========================================
@dp.message(RegisterState.role, F.text.in_(["🚌 Я водитель", "👤 Я пассажир"]))
async def process_role(message: types.Message, state: FSMContext):
    role = "driver" if "водитель" in message.text else "passenger"
    uid  = message.from_user.id
    name = message.from_user.full_name
    db.register_user(uid, name, role)
    await message.answer("✅ Заявка отправлена! Ожидайте одобрения.", reply_markup=ReplyKeyboardRemove())
    await state.clear()
    label = "Водитель 🚌" if role == "driver" else "Пассажир 👤"
    await bot.send_message(SUPER_ADMIN_ID,
        f"🔔 *Новая заявка*\n👤 {name}\n🆔 `{uid}`\n🎭 {label}",
        reply_markup=user_approve_kb(uid), parse_mode="Markdown")

# ==========================================
# СУПЕРАДМИН: одобрение пользователей
# ==========================================
@dp.message(F.text == "👥 Заявки на регистрацию")
async def show_pending_users(message: types.Message):
    if not is_admin(message.from_user.id): return
    rows = db.get_pending_users()
    if not rows:
        await message.answer("✅ Новых заявок нет."); return
    for uid, name, role in rows:
        label = "Водитель 🚌" if role == "driver" else "Пассажир 👤"
        await message.answer(f"👤 *{name}*\n🆔 `{uid}`\n🎭 {label}",
                             reply_markup=user_approve_kb(uid), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("usr_"))
async def handle_user_decision(callback: types.CallbackQuery):
    _, decision, uid_str = callback.data.split("_")
    uid  = int(uid_str)
    user = db.get_user(uid)
    name = user[3] if user else str(uid)
    role = user[1] if user else "passenger"
    label = "Водитель 🚌" if role == "driver" else "Пассажир 👤"
    if decision == "ok":
        db.approve_user(uid)
        await callback.message.edit_text(f"✅ *{name}* одобрен как {label}", parse_mode="Markdown")
        kb = get_driver_kb() if role == "driver" else get_passenger_kb()
        await bot.send_message(uid, f"🎉 Заявка одобрена! Роль: *{label}*",
                               reply_markup=kb, parse_mode="Markdown")
    else:
        db.reject_user(uid)
        await callback.message.edit_text(f"❌ *{name}* отклонён.", parse_mode="Markdown")
        await bot.send_message(uid, "❌ Ваша заявка отклонена. Обратитесь к администратору.")
    await callback.answer()

# ==========================================
# СУПЕРАДМИН: все пользователи
# ==========================================
@dp.message(F.text == "👤 Все пользователи")
async def show_all_users(message: types.Message):
    if not is_admin(message.from_user.id): return
    rows = db.get_all_users()
    if not rows:
        await message.answer("База пользователей пуста."); return
    status_icon = {'approved': '✅', 'pending': '⏳', 'rejected': '❌'}
    role_icon   = {'admin': '👑', 'driver': '🚌', 'passenger': '👤'}
    lines = ["*👥 Все пользователи:*\n"]
    for uid, name, role, status, points in rows:
        si = status_icon.get(status, '❓')
        ri = role_icon.get(role, '❓')
        lines.append(f"{si}{ri} *{name or uid}* — ⭐{points} баллов")
    await message.answer("\n".join(lines), parse_mode="Markdown")

# ==========================================
# СУПЕРАДМИН: изменить баллы
# ==========================================
@dp.message(F.text == "✏️ Изменить баллы")
async def adjust_points_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    rows = db.get_all_users()
    lines = ["*Выберите пользователя — введите его ID:*\n"]
    for uid, name, role, status, points in rows:
        if status == 'approved':
            lines.append(f"🆔 `{uid}` — {name} ({role}) ⭐{points}")
    await message.answer("\n".join(lines), reply_markup=get_cancel_kb(), parse_mode="Markdown")
    await state.set_state(AdjustPointsState.user_id)

@dp.message(AdjustPointsState.user_id, F.text)
async def adjust_points_get_id(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
        user = db.get_user(uid)
        if not user:
            await message.answer("❌ Пользователь не найден. Попробуйте ещё раз."); return
        await state.update_data(target_uid=uid, target_name=user[3])
        await message.answer(
            f"👤 *{user[3]}* — текущие баллы: ⭐{user[4]}\n\n"
            f"Введите число для изменения баллов:\n"
            f"• `+3` — начислить 3 балла\n"
            f"• `-2` — снять 2 балла",
            parse_mode="Markdown")
        await state.set_state(AdjustPointsState.delta)
    except ValueError:
        await message.answer("⚠️ Введите числовой ID.")

@dp.message(AdjustPointsState.delta, F.text)
async def adjust_points_apply(message: types.Message, state: FSMContext):
    try:
        delta = int(message.text.strip().replace("+", ""))
        data  = await state.get_data()
        uid   = data["target_uid"]
        name  = data["target_name"]
        db.adjust_points(uid, delta)
        user  = db.get_user(uid)
        sign  = "+" if delta >= 0 else ""
        await message.answer(
            f"✅ Баллы изменены!\n👤 *{name}*\n{sign}{delta} → итого ⭐{user[4]}",
            reply_markup=get_superadmin_kb(), parse_mode="Markdown")
        await bot.send_message(uid,
            f"📊 Ваш баланс обновлён администратором: {sign}{delta} балл(ов).\n"
            f"Текущий баланс: ⭐{user[4]}")
        await state.clear()
    except ValueError:
        await message.answer("⚠️ Введите число, например: +2 или -1")

# ==========================================
# СУПЕРАДМИН: статистика системы
# ==========================================
@dp.message(F.text == "📊 Статистика системы")
async def show_full_stats(message: types.Message):
    if not is_admin(message.from_user.id): return
    total, approved, rejected, pending, users, top = db.get_stats()
    top_lines = "\n".join([f"  {i+1}. {n or '?'} — ⭐{p}" for i, (n, p) in enumerate(top)]) or "  —"
    text = (
        f"📈 *Статистика CTS*\n\n"
        f"👥 Активных пользователей: *{users}*\n\n"
        f"📦 Репортов всего: *{total}*\n"
        f"  ✅ Одобрено: *{approved}*\n"
        f"  ❌ Отклонено: *{rejected}*\n"
        f"  ⏳ На проверке: *{pending}*\n\n"
        f"🏆 Топ водителей:\n{top_lines}"
    )
    await message.answer(text, parse_mode="Markdown")

# ==========================================
# СУПЕРАДМИН: проверить репорты
# ==========================================
@dp.message(F.text == "📋 Проверить репорты")
async def show_pending_reports(message: types.Message):
    if not is_admin(message.from_user.id): return
    pending = db.get_pending_reports()
    if not pending:
        await message.answer("✅ Новых репортов нет."); return
    for r in pending:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"adm_ok_{r[0]}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_no_{r[0]}")
        ]])
        await bot.send_photo(message.chat.id, photo=r[3],
                             caption=f"📦 Репорт #{r[0]}\n📍 {r[2]}\n👤 Driver ID: {r[1]}",
                             reply_markup=kb)

@dp.callback_query(F.data.startswith("adm_"))
async def admin_report_decision(callback: types.CallbackQuery):
    _, decision, r_id = callback.data.split("_")
    status = "approved" if decision == "ok" else "rejected"
    db.update_report_status(r_id, status)
    icon = "✅" if status == "approved" else "❌"
    await callback.message.edit_caption(caption=f"{icon} Репорт #{r_id}: {status.upper()}")
    # Уведомить водителя
    res = db.cursor.execute("SELECT driver_id FROM reports WHERE id=?", (r_id,)).fetchone()
    if res and status == "approved":
        user = db.get_user(res[0])
        pts  = user[4] if user else "?"
        await bot.send_message(res[0],
            f"🌟 Репорт #{r_id} подтверждён! +1 балл.\nВаш баланс: ⭐{pts}")
    elif res and status == "rejected":
        await bot.send_message(res[0], f"❌ Репорт #{r_id} отклонён (недостаточно доказательств).")
    await callback.answer()

# ==========================================
# СУПЕРАДМИН: создать репорт вручную
# ==========================================
@dp.message(F.text == "➕ Создать репорт")
async def admin_create_report(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer(
        "📍 Введите локацию инцидента.\n"
        "Координаты (напр. <code>51.1283, 71.4305</code>) или описание места.",
        reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(AdminReportState.location)

@dp.message(AdminReportState.location, F.text)
async def admin_report_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text)
    await message.answer("📸 Отправьте фото инцидента.")
    await state.set_state(AdminReportState.photo)

@dp.message(AdminReportState.photo, F.photo)
async def admin_report_photo(message: types.Message, state: FSMContext):
    data     = await state.get_data()
    photo_id = message.photo[-1].file_id
    location = data["location"]
    report_id = db.create_report(message.from_user.id, location, photo_id)
    db.update_report_status(report_id, "approved")
    await message.answer(
        f"✅ Репорт <b>#{report_id}</b> создан и автоматически одобрен.\n"
        f"📍 Локация: <code>{location}</code>",
        reply_markup=get_superadmin_kb(), parse_mode="HTML")
    await state.clear()

@dp.message(AdminReportState.photo)
async def admin_report_photo_wrong(message: types.Message):
    await message.answer("⚠️ Отправьте именно фото.")

# ==========================================
# СУПЕРАДМИН: рассылка
# ==========================================
@dp.message(F.text == "📢 Рассылка")
async def broadcast_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer(
        "📢 Введите текст рассылки.\nОн будет отправлен *всем* одобренным пользователям.",
        reply_markup=get_cancel_kb(), parse_mode="Markdown")
    await state.set_state(BroadcastState.text)

@dp.message(BroadcastState.text, F.text)
async def broadcast_send(message: types.Message, state: FSMContext):
    await state.clear()
    users = db.get_all_approved()
    text  = f"📢 *Сообщение от администратора CTS:*\n\n{message.text}"
    sent, failed = 0, 0
    for (uid,) in users:
        if uid == SUPER_ADMIN_ID: continue
        try:
            await bot.send_message(uid, text, parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1
    await message.answer(
        f"✅ Рассылка завершена.\n📨 Отправлено: {sent}\n❌ Не доставлено: {failed}",
        reply_markup=get_superadmin_kb())

# ==========================================
# СУПЕРАДМИН: дрон-аудит
# ==========================================
@dp.message(F.text == "🚁 Дрон-аудит")
async def drone_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer(
        "🚁 Введите локацию для дрон-аудита.\n"
        "Координаты (напр. <code>51.1283, 71.4305</code>) или описание.",
        reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(DroneState.location)

@dp.message(DroneState.location, F.text)
async def drone_submit(message: types.Message, state: FSMContext):
    location  = message.text
    audit_id  = db.create_drone_audit(location, message.from_user.id)
    await state.clear()
    await message.answer(
        f"🚁 Дрон-аудит <b>#{audit_id}</b> запрошен.\n"
        f"📍 Локация: <code>{location}</code>\n\n"
        f"Статус: ⏳ Ожидает назначения оператора.",
        reply_markup=get_superadmin_kb(), parse_mode="HTML")

# ==========================================
# ВОДИТЕЛЬ: репорт (с вводом локации)
# ==========================================
@dp.message(F.text == "🚨 Сообщить об инциденте")
async def driver_report_start(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if not (is_admin(message.from_user.id) or
            (user and user[2] == 'approved' and user[1] == 'driver')):
        await message.answer("⛔ У вас нет доступа."); return
    await message.answer(
        "📍 Введите локацию инцидента\n(адрес или координаты):",
        reply_markup=get_cancel_kb())
    await state.set_state(ReportState.location)

@dp.message(ReportState.location, F.text)
async def driver_report_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text)
    await message.answer("📸 Теперь отправьте фото инцидента.", reply_markup=ReplyKeyboardRemove())
    await state.set_state(ReportState.photo)

@dp.message(ReportState.photo, F.photo)
async def driver_report_photo(message: types.Message, state: FSMContext):
    data      = await state.get_data()
    photo_id  = message.photo[-1].file_id
    location  = data["location"]
    uid       = message.from_user.id
    report_id = db.create_report(uid, location, photo_id)
    kb = get_superadmin_kb() if is_admin(uid) else get_driver_kb()
    await message.answer(f"✅ Репорт #{report_id} отправлен на проверку.", reply_markup=kb)
    await state.clear()
    # Уведомить суперадмина
    if not is_admin(uid):
        user = db.get_user(uid)
        name = user[3] if user else str(uid)
        await bot.send_photo(SUPER_ADMIN_ID, photo=photo_id,
            caption=f"🔔 Новый репорт #{report_id}\n👤 {name}\n📍 {location}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"adm_ok_{report_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_no_{report_id}")
            ]]))

@dp.message(ReportState.photo)
async def driver_report_photo_wrong(message: types.Message):
    await message.answer("⚠️ Отправьте именно фото.")

# ==========================================
# БОНУСЫ
# ==========================================
@dp.message(F.text == "🎁 Мои бонусы")
async def check_bonus(message: types.Message):
    res = db.cursor.execute("SELECT points FROM users WHERE user_id=?", (message.from_user.id,)).fetchone()
    pts  = res[0] if res else 0
    days = pts // 4
    left = 4 - (pts % 4) if pts % 4 != 0 else 0
    text = f"⭐ Ваши баллы: *{pts}*\n📅 Дней отдыха: *{days}*"
    if left:
        text += f"\n\nДо следующего дня отдыха: ещё *{left}* репорта."
    await message.answer(text, parse_mode="Markdown")

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
