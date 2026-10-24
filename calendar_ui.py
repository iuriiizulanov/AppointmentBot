import calendar
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

MONTH_NAMES = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
WEEKDAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def build_calendar(year: int, month: int) -> InlineKeyboardMarkup:
    month_date = date(year, month, 1)
    previous = (
        date(year - 1, 12, 1) if month == 1 else date(year, month - 1, 1)
    )
    following = (
        date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    )
    keyboard = [
        [
            InlineKeyboardButton(
                "‹", callback_data=f"cal:month:{previous.year}:{previous.month}"
            ),
            InlineKeyboardButton(
                f"{MONTH_NAMES[month - 1]} {year}", callback_data="cal:noop"
            ),
            InlineKeyboardButton(
                "›", callback_data=f"cal:month:{following.year}:{following.month}"
            ),
        ],
        [
            InlineKeyboardButton(name, callback_data="cal:noop")
            for name in WEEKDAY_NAMES
        ],
    ]
    for week in calendar.monthcalendar(month_date.year, month_date.month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton("·", callback_data="cal:noop"))
            else:
                selected_date = date(year, month, day)
                row.append(
                    InlineKeyboardButton(
                        str(day),
                        callback_data=f"cal:day:{selected_date.isoformat()}",
                    )
                )
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


def build_hour_keyboard():
    hours = [
        InlineKeyboardButton(f"{hour:02d}", callback_data=f"cal:hour:{hour:02d}")
        for hour in range(24)
    ]
    rows = [hours[index : index + 4] for index in range(0, 24, 4)]
    rows.append([InlineKeyboardButton("Назад к календарю", callback_data="cal:back")])
    return InlineKeyboardMarkup(rows)


def build_minute_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{minute:02d}", callback_data=f"cal:minute:{minute:02d}"
                )
                for minute in (0, 15, 30, 45)
            ],
            [InlineKeyboardButton("Назад к выбору часа", callback_data="cal:back_hour")],
        ]
    )
