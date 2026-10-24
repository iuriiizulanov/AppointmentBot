import unittest

from calendar_ui import build_calendar, build_hour_keyboard, build_minute_keyboard


class CalendarUiTests(unittest.TestCase):
    def test_calendar_contains_all_days_of_leap_february(self):
        keyboard = build_calendar(2028, 2)
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]
        day_callbacks = [value for value in callbacks if value.startswith("cal:day:")]

        self.assertEqual(len(day_callbacks), 29)
        self.assertIn("cal:day:2028-02-29", day_callbacks)

    def test_calendar_navigation_handles_year_boundary(self):
        keyboard = build_calendar(2026, 1)
        navigation = [
            button.callback_data
            for button in keyboard.inline_keyboard[0]
        ]

        self.assertEqual(navigation, ["cal:month:2025:12", "cal:noop", "cal:month:2026:2"])

    def test_time_picker_offers_all_hours_and_quarter_hours(self):
        hour_keyboard = build_hour_keyboard()
        hour_callbacks = [
            button.callback_data
            for row in hour_keyboard.inline_keyboard
            for button in row
        ]
        minute_keyboard = build_minute_keyboard()
        minute_callbacks = [
            button.callback_data
            for row in minute_keyboard.inline_keyboard
            for button in row
        ]

        self.assertEqual(
            [value for value in hour_callbacks if value.startswith("cal:hour:")],
            [f"cal:hour:{hour:02d}" for hour in range(24)],
        )
        self.assertEqual(
            [value for value in minute_callbacks if value.startswith("cal:minute:")],
            ["cal:minute:00", "cal:minute:15", "cal:minute:30", "cal:minute:45"],
        )


if __name__ == "__main__":
    unittest.main()
