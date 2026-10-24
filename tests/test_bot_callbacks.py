import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot


class BookingCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_slot_callback_shows_confirmation(self):
        query = SimpleNamespace(
            data="delete_slot:17",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=42),
        )
        context = SimpleNamespace(user_data={})
        with (
            patch.object(
                bot.database,
                "active_event_for_user",
                return_value={"code": "0123456789"},
            ),
            patch.object(
                bot.database,
                "list_slots",
                return_value=[
                    {
                        "id": 17,
                        "starts_at": "2026-10-25 12:30",
                        "status": "open",
                        "pending_by": None,
                    }
                ],
            ),
        ):
            await bot.booking_action(update, context)

        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()
        self.assertIn(
            "Удалить свободное окно 2026-10-25 12:30?",
            query.edit_message_text.await_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
