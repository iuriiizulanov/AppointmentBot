import tempfile
import unittest
from pathlib import Path

from database import BookingError, Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(str(Path(self.temp_dir.name) / "test.sqlite3"))
        self.database.create_event("0123456789", "Тест", 1, 2)
        self.database.join_event("0123456789", 2)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_member_can_request_and_admin_approve_slot(self):
        self.database.join_event("0123456789", 3)
        self.database.add_slot("0123456789", "2026-10-25 12:30", 2)
        slot = self.database.list_slots("0123456789", 3)[0]
        request_id = self.database.request_slot(
            "0123456789", slot["id"], 3
        )

        self.assertEqual(self.database.approve(request_id, 2), 3)
        slots = self.database.list_slots("0123456789", 2, admin_view=True)
        self.assertEqual(slots[0]["status"], "booked")
        self.assertEqual(slots[0]["booked_by"], 3)

    def test_request_details_checks_event_admin_membership(self):
        self.database.join_event("0123456789", 3)
        self.database.add_slot("0123456789", "2026-10-25 12:30", 2)
        slot_id = self.database.list_slots("0123456789", 3)[0]["id"]
        request_id = self.database.request_slot("0123456789", slot_id, 3)

        self.assertEqual(
            self.database.request_details(request_id, 2)["event_id"],
            self.database.event_by_code("0123456789")["id"],
        )
        with self.assertRaises(BookingError):
            self.database.request_details(request_id, 4)

    def test_my_bookings_shows_requested_and_scheduled_not_rejected(self):
        self.database.join_event("0123456789", 3)
        for starts_at in (
            "2026-10-25 12:30",
            "2026-10-26 12:30",
            "2026-10-27 12:30",
        ):
            self.database.add_slot("0123456789", starts_at, 2)
        slots = self.database.list_slots("0123456789", 3)
        pending_id = self.database.request_slot("0123456789", slots[0]["id"], 3)
        approved_id = self.database.request_slot("0123456789", slots[1]["id"], 3)
        rejected_id = self.database.request_slot("0123456789", slots[2]["id"], 3)

        self.database.approve(approved_id, 2)
        self.database.reject(rejected_id, 2, "Время недоступно")
        bookings = self.database.my_bookings("0123456789", 3)

        self.assertEqual(
            [(row["request_id"], row["status"]) for row in bookings],
            [(pending_id, "pending"), (approved_id, "approved")],
        )
        with self.assertRaises(BookingError):
            self.database.my_bookings("0123456789", 4)

    def test_only_one_pending_request_per_slot(self):
        self.database.join_event("0123456789", 3)
        self.database.join_event("0123456789", 4)
        self.database.add_slot("0123456789", "2026-10-25 12:30", 2)
        slot_id = self.database.list_slots("0123456789", 3)[0]["id"]
        self.database.request_slot("0123456789", slot_id, 3)

        with self.assertRaises(BookingError):
            self.database.request_slot("0123456789", slot_id, 4)

    def test_non_admin_cannot_add_slot(self):
        with self.assertRaises(BookingError):
            self.database.add_slot("0123456789", "2026-10-25 12:30", 3)

    def test_events_are_listed_for_members_and_event_admins(self):
        self.database.join_event("0123456789", 3)

        self.assertEqual(
            [event["code"] for event in self.database.events_for_user(3)],
            ["0123456789"],
        )
        self.assertEqual(
            [event["code"] for event in self.database.events_for_user(2, admin_only=True)],
            ["0123456789"],
        )
        self.assertEqual(self.database.events_for_user(4), [])

    def test_join_remembers_active_event_and_leave_removes_access(self):
        self.database.create_event("9876543210", "Второе", 1, 2)
        self.database.join_event("0123456789", 2)
        self.database.join_event("9876543210", 2)
        self.assertEqual(
            self.database.active_event_for_user(2)["code"], "9876543210"
        )

        self.database.leave_event("9876543210", 2)

        self.assertEqual(
            self.database.active_event_for_user(2)["code"], "0123456789"
        )
        self.assertEqual(
            [event["code"] for event in self.database.events_for_user(2, True)],
            ["0123456789"],
        )
        with self.assertRaises(BookingError):
            self.database.add_slot("9876543210", "2026-10-25 13:00", 2)

    def test_leaving_last_event_clears_active_event_and_admin_actions(self):
        self.database.leave_event("0123456789", 2)

        self.assertIsNone(self.database.active_event_for_user(2))
        self.assertEqual(self.database.events_for_user(2), [])
        with self.assertRaises(BookingError):
            self.database.add_slot("0123456789", "2026-10-25 14:00", 2)

    def test_rejection_message_is_saved(self):
        self.database.join_event("0123456789", 3)
        self.database.add_slot("0123456789", "2026-10-25 12:30", 2)
        slot_id = self.database.list_slots("0123456789", 3)[0]["id"]
        request_id = self.database.request_slot("0123456789", slot_id, 3)

        self.assertEqual(self.database.reject(request_id, 2, "Попробуйте другое время"), 3)
        next_request_id = self.database.request_slot(
            "0123456789", slot_id, 3
        )
        self.assertNotEqual(next_request_id, request_id)
        with self.database._connection() as connection:
            row = connection.execute(
                "SELECT status, rejection_message FROM booking_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["rejection_message"], "Попробуйте другое время")


if __name__ == "__main__":
    unittest.main()
