import logging
import os
import secrets
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from calendar_ui import build_calendar, build_hour_keyboard, build_minute_keyboard
from database import BookingError, Database

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
TOKEN = os.environ.get("BOT_TOKEN", "")
SUPERADMIN_ID = int(os.environ.get("SUPERADMIN_TELEGRAM_ID", "0"))
DB_PATH = os.environ.get("DATABASE_PATH", "appointments.sqlite3")
database = Database(DB_PATH)


def _user_id(update: Update) -> int:
    if update.effective_user is None:
        raise BookingError("Не удалось определить пользователя.")
    return update.effective_user.id


def command_menu(user_id: int) -> ReplyKeyboardMarkup:
    active_event = database.active_event_for_user(user_id)
    rows = []
    if active_event is not None:
        rows.extend([["🗓 Мои записи"], ["📅 Свободные окна"]])
        admin_events = database.events_for_user(user_id, admin_only=True)
        if any(event["code"] == active_event["code"] for event in admin_events):
            rows.extend(
                [
                    ["📋 Все записи"],
                    ["➕ Добавить окно"],
                    ["📨 Запросы на запись"],
                    ["✉️ Отказать по заявке"],
                ]
            )
    if user_id == SUPERADMIN_ID:
        rows.append(["✨ Создать зал"])
    rows.append(["🔑 Войти в зал"])
    if active_event is not None:
        rows.append(["🚪 Выйти из зала"])
    rows.append(["ℹ️ Помощь"])
    rows.append(["❌ Отменить"])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def _reply(update: Update, text: str) -> None:
    if update.effective_message is not None and update.effective_user is not None:
        await update.effective_message.reply_text(
            text, reply_markup=command_menu(update.effective_user.id)
        )


async def _ask_for_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    prompt: str,
    data: Optional[dict] = None,
) -> None:
    context.user_data["input_flow"] = {"action": action, "data": data or {}}
    await _reply(update, prompt)


async def _ask_for_event(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    admin_only: bool,
) -> None:
    events = database.events_for_user(_user_id(update), admin_only=admin_only)
    if not events:
        await _reply(update, "Сначала нажмите /join и добавьте зал.")
        return
    active = database.active_event_for_user(_user_id(update))
    if active is not None and any(event["code"] == active["code"] for event in events):
        await _select_event_action(update, context, action, active["code"])
    elif len(events) == 1:
        await _select_event_action(update, context, action, events[0]["code"])
    else:
        keyboard = [
            [
                InlineKeyboardButton(
                    f"{event['title']} ({event['code']})",
                    callback_data=f"event:{action}:{event['code']}",
                )
            ]
            for event in events
        ]
        if update.effective_message is not None:
            await update.effective_message.reply_text(
                "Ваши залы:", reply_markup=InlineKeyboardMarkup(keyboard)
            )


async def _show_available_slots(
    update: Update, code: str, user_id: int
) -> None:
    available = database.list_slots(code, user_id)
    if not available:
        await _reply(update, "Свободных окон пока нет.")
        return
    keyboard = [[
        InlineKeyboardButton(
            f"Запросить {slot['starts_at']}",
            callback_data=f"book:{slot['id']}",
        )
    ] for slot in available]
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Доступные окна:", reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def _show_admin_slots(
    update: Update, code: str, user_id: int
) -> None:
    all_slots = database.list_slots(code, user_id, admin_view=True)
    if not all_slots:
        await _reply(update, "В зале пока нет окон.")
        return
    lines = [
        f"{row['starts_at']} — "
        + (
            f"занято пользователем {row['booked_by']}"
            if row["status"] == "booked"
            else "свободно"
        )
        for row in all_slots
    ]
    await _reply(update, "\n".join(lines))


async def _show_requests(update: Update, code: str, user_id: int) -> None:
    pending = database.list_requests(code, user_id)
    if not pending:
        await _reply(update, "Ожидающих запросов нет.")
        return
    for request in pending:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "Подтвердить", callback_data=f"approve:{request['id']}"
            ),
            InlineKeyboardButton(
                "Отказать", callback_data=f"reject:{request['id']}"
            ),
        ]])
        if update.effective_message is not None:
            await update.effective_message.reply_text(
                f"Запрос #{request['id']}: пользователь {request['user_id']}, "
                f"окно {request['starts_at']}",
                reply_markup=keyboard,
            )

async def _show_my_bookings(update: Update, code: str, user_id: int) -> None:
    bookings = database.my_bookings(code, user_id)
    if not bookings:
        await _reply(update, "У вас пока нет запрошенных или запланированных записей.")
        return
    status_labels = {
        "pending": "Запрошено",
        "approved": "Запланировано",
    }
    lines = [
        f"{booking['starts_at']} — {status_labels[booking['status']]}"
        for booking in bookings
    ]
    await _reply(update, "Ваши записи:\n" + "\n".join(lines))


async def _select_event_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    code: str,
) -> None:
    context.user_data.pop("input_flow", None)
    user_id = _user_id(update)
    database.select_event(code, user_id)
    if action == "slots":
        await _show_available_slots(update, code, user_id)
    elif action == "event_slots":
        await _show_admin_slots(update, code, user_id)
    elif action == "requests":
        await _show_requests(update, code, user_id)
    elif action == "my_bookings":
        await _show_my_bookings(update, code, user_id)
    elif action == "addslot":
        await _show_addslot_calendar(update, context, code)


async def _show_addslot_calendar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    code: str,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> None:
    database.list_slots(code, _user_id(update), admin_view=True)
    now = datetime.now()
    context.user_data["calendar_addslot"] = {
        "code": code,
        "year": year or now.year,
        "month": month or now.month,
    }
    if update.effective_message is not None:
        await update.effective_message.reply_text(
            "Выберите дату свободного окна:",
            reply_markup=build_calendar(
                context.user_data["calendar_addslot"]["year"],
                context.user_data["calendar_addslot"]["month"],
            ),
        )


async def calendar_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    try:
        parts = query.data.split(":")
        if parts[1] == "noop":
            return
        flow = context.user_data.get("calendar_addslot")
        if flow is None:
            raise BookingError("Календарь устарел. Нажмите «Добавить окно» ещё раз.")
        code = flow["code"]
        user_id = _user_id(update)
        database.list_slots(code, user_id, admin_view=True)

        if parts[1] == "month" and len(parts) == 4:
            year, month = int(parts[2]), int(parts[3])
            build_calendar(year, month)
            flow.update(year=year, month=month)
            await query.edit_message_text(
                "Выберите дату свободного окна:",
                reply_markup=build_calendar(year, month),
            )
        elif parts[1] == "day" and len(parts) == 3:
            selected_date = datetime.strptime(parts[2], "%Y-%m-%d").date()
            flow["date"] = selected_date.isoformat()
            await query.edit_message_text(
                f"Дата: {selected_date:%d.%m.%Y}\nВыберите час:",
                reply_markup=build_hour_keyboard(),
            )
        elif parts[1] == "hour" and len(parts) == 3:
            hour = int(parts[2])
            if not 0 <= hour <= 23 or "date" not in flow:
                raise BookingError("Выберите дату и время заново.")
            flow["hour"] = hour
            await query.edit_message_text(
                f"Дата: {flow['date']}, час: {hour:02d}\nВыберите минуты:",
                reply_markup=build_minute_keyboard(),
            )
        elif parts[1] == "back":
            await query.edit_message_text(
                "Выберите дату свободного окна:",
                reply_markup=build_calendar(flow["year"], flow["month"]),
            )
        elif parts[1] == "back_hour" and "date" in flow:
            await query.edit_message_text(
                f"Дата: {flow['date']}\nВыберите час:",
                reply_markup=build_hour_keyboard(),
            )
        elif parts[1] == "minute" and len(parts) == 3:
            minute = int(parts[2])
            if minute not in (0, 15, 30, 45) or "date" not in flow or "hour" not in flow:
                raise BookingError("Выберите дату и время заново.")
            starts_at = datetime.strptime(
                f"{flow['date']} {flow['hour']:02d}:{minute:02d}",
                "%Y-%m-%d %H:%M",
            ).strftime("%Y-%m-%d %H:%M")
            database.add_slot(code, starts_at, user_id)
            context.user_data.pop("calendar_addslot", None)
            await query.edit_message_text(f"Свободное окно {starts_at} добавлено.")
        else:
            raise BookingError("Неизвестное действие календаря.")
    except (BookingError, ValueError) as exc:
        await query.edit_message_text(str(exc))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("input_flow", None)
    context.user_data.pop("calendar_addslot", None)
    await _reply(
        update,
        "Вас приветствует Марина в своем зале!\n"
        "Можете оставить заявку на запись в свободные окна.\n\n"
        "Команды также можно вводить вручную: /join, /slots, /leave, "
        "/addslot, /event_slots, /requests, /create_event, /reject, /switch.",
    )


async def create_event(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if _user_id(update) != SUPERADMIN_ID:
        await _reply(update, "Команда доступна только суперадминистратору.")
        return
    if not context.args:
        await _ask_for_input(
            update,
            context,
            "create_admin",
            "Введите Telegram ID администратора зала:",
        )
        return
    if len(context.args) < 2:
        try:
            admin_id = int(context.args[0])
        except ValueError:
            await _reply(update, "Telegram ID должен быть числом.")
            return
        await _ask_for_input(
            update,
            context,
            "create_title",
            "Введите название нового зала:",
            {"admin_id": admin_id},
        )
        return
    try:
        admin_id = int(context.args[0])
        title = " ".join(context.args[1:]).strip()
        if not title:
            raise ValueError("empty title")
        while True:
            code = f"{secrets.randbelow(10**10):010d}"
            try:
                database.create_event(code, title, SUPERADMIN_ID, admin_id)
                break
            except BookingError as exc:
                if "уже существует" not in str(exc):
                    raise
    except (ValueError, BookingError) as exc:
        await _reply(update, f"Не удалось создать зал: {exc}")
        return
    await _reply(
        update,
        f"Зал «{title}» создан. Код: {code}. Администратор: {admin_id}.",
    )
    try:
        await context.bot.send_message(
            admin_id,
            f"Вас назначили администратором зала «{title}» (код {code}).",
        )
    except TelegramError:
        logger.exception("Could not notify assigned admin %s", admin_id)


async def join_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _ask_for_input(
            update, context, "join_code", "Введите 10-значный код зала:"
        )
        return
    if len(context.args) != 1 or not _valid_code(context.args[0]):
        await _reply(update, "Формат: /join <10-значный код>")
        return
    try:
        database.join_event(context.args[0], _user_id(update))
        await _reply(
            update,
            "Вы вошли в зал. Оно запомнено; повторно вводить код не нужно.",
        )
    except BookingError as exc:
        await _reply(update, str(exc))


async def leave_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user_id(update)
    events = database.events_for_user(user_id)
    if not events:
        await _reply(update, "Вы не состоите ни в одном зале.")
        return
    if len(events) == 1:
        try:
            database.leave_event(events[0]["code"], user_id)
            await _reply(
                update,
                f"Вы вышли из зала «{events[0]['title']}». "
                "Кнопки его действий больше недоступны.",
            )
        except BookingError as exc:
            await _reply(update, str(exc))
        return
    keyboard = [[
        InlineKeyboardButton(
            f"Выйти: {event['title']}",
            callback_data=f"leave:{event['code']}",
        )
    ] for event in events]
    if update.effective_message is not None:
        await update.effective_message.reply_text(
            "Выберите зал, из которого хотите выйти:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def switch_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    events = database.events_for_user(_user_id(update))
    if len(events) < 2:
        await _reply(update, "Для смены зала нужно состоять хотя бы в двух.")
        return
    keyboard = [[
        InlineKeyboardButton(
            f"{event['title']} ({event['code']})",
            callback_data=f"select:{event['code']}",
        )
    ] for event in events]
    if update.effective_message is not None:
        await update.effective_message.reply_text(
            "Выберите активный зал:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


def _valid_code(code: str) -> bool:
    return len(code) == 10 and code.isascii() and code.isdigit()


async def slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _ask_for_event(update, context, "slots", admin_only=False)
        return
    if len(context.args) != 1 or not _valid_code(context.args[0]):
        await _reply(update, "Формат: /slots <10-значный код>")
        return
    try:
        await _select_event_action(
            update, context, "slots", context.args[0]
        )
    except BookingError as exc:
        await _reply(update, str(exc))


async def add_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _ask_for_event(update, context, "addslot", admin_only=True)
        return
    if len(context.args) == 1 and _valid_code(context.args[0]):
        try:
            database.select_event(context.args[0], _user_id(update))
        except BookingError as exc:
            await _reply(update, str(exc))
            return
        await _show_addslot_calendar(update, context, context.args[0])
        return
    if len(context.args) < 2 or not _valid_code(context.args[0]):
        await _reply(
            update, "Формат: /addslot <код> <YYYY-MM-DD HH:MM>"
        )
        return
    try:
        starts_at = datetime.strptime(
            " ".join(context.args[1:]), "%Y-%m-%d %H:%M"
        ).strftime("%Y-%m-%d %H:%M")
        database.select_event(context.args[0], _user_id(update))
        database.add_slot(context.args[0], starts_at, _user_id(update))
        await _reply(update, f"Окно {starts_at} добавлено.")
    except BookingError as exc:
        await _reply(update, str(exc))
    except ValueError:
        await _reply(update, "Некорректная дата. Используйте YYYY-MM-DD HH:MM.")


async def event_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _ask_for_event(update, context, "event_slots", admin_only=True)
        return
    if len(context.args) != 1 or not _valid_code(context.args[0]):
        await _reply(update, "Формат: /event_slots <10-значный код>")
        return
    try:
        await _select_event_action(
            update, context, "event_slots", context.args[0]
        )
    except BookingError as exc:
        await _reply(update, str(exc))


async def requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _ask_for_event(update, context, "requests", admin_only=True)
        return
    if len(context.args) != 1 or not _valid_code(context.args[0]):
        await _reply(update, "Формат: /requests <10-значный код>")
        return
    try:
        await _select_event_action(
            update, context, "requests", context.args[0]
        )
    except BookingError as exc:
        await _reply(update, str(exc))

async def my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _ask_for_event(update, context, "my_bookings", admin_only=False)
        return
    if len(context.args) != 1 or not _valid_code(context.args[0]):
        await _reply(update, "Формат: /my_bookings <10-значный код>")
        return
    try:
        await _select_event_action(update, context, "my_bookings", context.args[0])
    except BookingError as exc:
        await _reply(update, str(exc))


async def booking_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    try:
        parts = query.data.split(":")
        if parts[0] == "leave" and len(parts) == 2:
            user_id = _user_id(update)
            code = parts[1]
            event = next(
                (
                    item
                    for item in database.events_for_user(user_id)
                    if item["code"] == code
                ),
                None,
            )
            if event is None:
                raise BookingError("Вы уже не состоите в этом зале.")
            database.leave_event(code, user_id)
            await query.edit_message_text(f"Вы вышли из зала «{event['title']}».")
            await _reply(
                update,
                "Кнопки действий зала больше недоступны. Чтобы вернуться, нажмите /join.",
            )
            return
        if parts[0] == "select" and len(parts) == 2:
            database.select_event(parts[1], _user_id(update))
            event = next(
                item
                for item in database.events_for_user(_user_id(update))
                if item["code"] == parts[1]
            )
            await query.edit_message_text(
                f"Активный зал: «{event['title']}»."
            )
            await _reply(update, "Готово. Действия используют выбранный зал.")
            return
        if parts[0] == "event" and len(parts) == 3:
            await query.edit_message_text("Зал выбран.")
            await _select_event_action(update, context, parts[1], parts[2])
            return
        action, raw_id = parts
        item_id = int(raw_id)
        admin_id = _user_id(update)
        if action == "book":
            event_code = database.event_code_for_slot(item_id)
            if event_code is None:
                raise BookingError("Окно не найдено.")
            request_id = database.request_slot(event_code, item_id, admin_id)
            event = database.event_by_code(event_code)
            try:
                await context.bot.send_message(
                    event["admin_id"],
                    f"Новый запрос #{request_id} на окно. Используйте "
                    f"/requests {event_code} для рассмотрения.",
                )
                response = "Запрос на запись отправлен."
            except TelegramError:
                logger.exception("Could not notify event admin %s", event["admin_id"])
                response = (
                    "Запрос сохранён, но не удалось уведомить администратора. "
                    "Попросите его открыть чат с ботом."
                )
            await query.edit_message_text(response)
        elif action == "approve":
            database.request_details(item_id, admin_id)
            user_id = database.approve(item_id, admin_id)
            await context.bot.send_message(
                user_id, "Ваш запрос на запись подтверждён."
            )
            await query.edit_message_text(f"Запрос #{item_id} подтверждён.")
        elif action == "reject":
            database.request_details(item_id, admin_id)
            context.user_data["input_flow"] = {
                "action": "reject_message",
                "data": {"request_id": item_id},
            }
            await query.edit_message_text(
                f"Введите сообщение для пользователя по запросу #{item_id}:"
            )
        else:
            raise BookingError("Неизвестное действие.")
    except (BookingError, ValueError) as exc:
        await query.edit_message_text(str(exc))


async def reject_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _ask_for_input(
            update, context, "reject_id", "Введите номер запроса для отказа:"
        )
        return
    if len(context.args) == 1:
        try:
            request_id = int(context.args[0])
            database.request_details(request_id, _user_id(update))
        except ValueError:
            await _reply(update, "Номер запроса должен быть числом.")
            return
        except BookingError as exc:
            await _reply(update, str(exc))
            return
        await _ask_for_input(
            update,
            context,
            "reject_message",
            f"Введите сообщение для пользователя по запросу #{request_id}:",
            {"request_id": request_id},
        )
        return
    if len(context.args) < 2:
        await _reply(update, "Формат: /reject <номер_запроса> <сообщение>")
        return
    try:
        request_id = int(context.args[0])
        user_id = database.reject(
            request_id, _user_id(update), " ".join(context.args[1:])
        )
        await context.bot.send_message(
            user_id, f"Ваш запрос отклонён. Ответ администратора: {' '.join(context.args[1:])}"
        )
        await _reply(update, f"Запрос #{request_id} отклонён, ответ отправлен.")
    except (ValueError, BookingError) as exc:
        await _reply(update, str(exc))


async def handle_text_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return
    text = message.text.strip()
    menu_actions = {
        "ℹ️ Помощь": start,
        "🔑 Войти в зал": join_event,
        "📅 Свободные окна": slots,
        "🗓 Мои записи": my_bookings,
        "🚪 Выйти из зала": leave_event,
        "🔄 Сменить зал": switch_event,
        "➕ Добавить окно": add_slot,
        "📋 Все записи": event_slots,
        "📨 Запросы на запись": requests,
        "✉️ Отказать по заявке": reject_request,
        "✨ Создать зал": create_event,
        "❌ Отменить": cancel_input,
    }
    menu_action = menu_actions.get(text)
    if menu_action is not None:
        await menu_action(update, context)
        return

    flow = context.user_data.get("input_flow")
    if flow is None:
        return
    action = flow["action"]
    data = flow["data"]
    context.user_data.pop("input_flow", None)

    try:
        if action == "create_admin":
            admin_id = int(text)
            if admin_id <= 0:
                raise ValueError("Telegram ID должен быть положительным числом.")
            await _ask_for_input(
                update,
                context,
                "create_title",
                "Введите название нового зала:",
                {"admin_id": admin_id},
            )
        elif action == "create_title":
            if not text:
                raise BookingError("Название зала не может быть пустым.")
            while True:
                code = f"{secrets.randbelow(10**10):010d}"
                try:
                    database.create_event(code, text, SUPERADMIN_ID, data["admin_id"])
                    break
                except BookingError as exc:
                    if "уже существует" not in str(exc):
                        raise
            await _reply(
                update,
                f"Зал «{text}» создан. Код: {code}. "
                f"Администратор: {data['admin_id']}.",
            )
            try:
                await context.bot.send_message(
                    data["admin_id"],
                    f"Вас назначили администратором зала «{text}» (код {code}).",
                )
            except TelegramError:
                logger.exception("Could not notify assigned admin %s", data["admin_id"])
        elif action == "join_code":
            if not _valid_code(text):
                raise BookingError("Код должен содержать ровно 10 цифр.")
            database.join_event(text, _user_id(update))
            await _reply(
                update,
                f"Вы вошли в зал {text}. Оно запомнено; повторно вводить код не нужно.",
            )
        elif action == "slots_code":
            if not _valid_code(text):
                raise BookingError("Код должен содержать ровно 10 цифр.")
            await _select_event_action(update, context, "slots", text)
        elif action == "addslot_code":
            if not _valid_code(text):
                raise BookingError("Код должен содержать ровно 10 цифр.")
            await _ask_for_input(
                update,
                context,
                "addslot_datetime",
                "Введите дату и время окна в формате YYYY-MM-DD HH:MM:",
                {"code": text},
            )
        elif action == "addslot_datetime":
            starts_at = datetime.strptime(text, "%Y-%m-%d %H:%M").strftime(
                "%Y-%m-%d %H:%M"
            )
            database.add_slot(data["code"], starts_at, _user_id(update))
            await _reply(update, f"Окно {starts_at} добавлено.")
        elif action == "event_slots_code":
            if not _valid_code(text):
                raise BookingError("Код должен содержать ровно 10 цифр.")
            await _select_event_action(update, context, "event_slots", text)
        elif action == "requests_code":
            if not _valid_code(text):
                raise BookingError("Код должен содержать ровно 10 цифр.")
            await _select_event_action(update, context, "requests", text)
        elif action == "my_bookings_code":
            if not _valid_code(text):
                raise BookingError("Код должен содержать ровно 10 цифр.")
            await _select_event_action(update, context, "my_bookings", text)
        elif action == "reject_id":
            request_id = int(text)
            await database.request_details(request_id, _user_id(update))
            await _ask_for_input(
                update,
                context,
                "reject_message",
                f"Введите сообщение для пользователя по запросу #{request_id}:",
                {"request_id": request_id},
            )
        elif action == "reject_message":
            user_id = database.reject(
                data["request_id"], _user_id(update), text
            )
            await context.bot.send_message(
                user_id, f"Ваш запрос отклонён. Ответ администратора: {text}"
            )
            await _reply(
                update, f"Запрос #{data['request_id']} отклонён, ответ отправлен."
            )
    except (BookingError, ValueError) as exc:
        context.user_data["input_flow"] = flow
        await _reply(update, str(exc))


async def cancel_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("input_flow", None)
    context.user_data.pop("calendar_addslot", None)
    await _reply(update, "Ввод отменён.")


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.error("Unhandled Telegram update error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        await update.effective_message.reply_text(
            "Произошла внутренняя ошибка. Попробуйте ещё раз позже."
        )


def main() -> None:
    if not TOKEN or SUPERADMIN_ID <= 0:
        raise RuntimeError(
            "Задайте BOT_TOKEN и положительный SUPERADMIN_TELEGRAM_ID."
        )
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("create_event", create_event))
    application.add_handler(CommandHandler("join", join_event))
    application.add_handler(CommandHandler("leave", leave_event))
    application.add_handler(CommandHandler("switch", switch_event))
    application.add_handler(CommandHandler("slots", slots))
    application.add_handler(CommandHandler("addslot", add_slot))
    application.add_handler(CommandHandler("event_slots", event_slots))
    application.add_handler(CommandHandler("requests", requests))
    application.add_handler(CommandHandler("my_bookings", my_bookings))
    application.add_handler(CommandHandler("reject", reject_request))
    application.add_handler(CommandHandler("cancel", cancel_input))
    application.add_handler(CallbackQueryHandler(calendar_action, pattern=r"^cal:"))
    application.add_handler(CallbackQueryHandler(booking_action))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input)
    )
    application.add_error_handler(error_handler)
    application.run_polling()


if __name__ == "__main__":
    main()
