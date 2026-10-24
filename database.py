import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


class BookingError(ValueError):
    pass


class Database:
    def __init__(self, path: str = "appointments.sqlite3") -> None:
        self.path = path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE CHECK(length(code) = 10),
                    title TEXT NOT NULL,
                    superadmin_id INTEGER NOT NULL,
                    admin_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS members (
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (event_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS active_events (
                    user_id INTEGER PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS slots (
                    id INTEGER PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    starts_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK(status IN ('open', 'booked')),
                    UNIQUE(event_id, starts_at)
                );
                CREATE TABLE IF NOT EXISTS booking_requests (
                    id INTEGER PRIMARY KEY,
                    slot_id INTEGER NOT NULL REFERENCES slots(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'approved', 'rejected')),
                    rejection_message TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_request_per_slot
                    ON booking_requests(slot_id) WHERE status = 'pending';
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_request_per_member_slot
                    ON booking_requests(slot_id, user_id) WHERE status = 'pending';
                """
            )

    def create_event(
        self, code: str, title: str, superadmin_id: int, admin_id: int
    ) -> None:
        try:
            with self._connection() as connection:
                connection.execute(
                    "INSERT INTO events(code, title, superadmin_id, admin_id) "
                    "VALUES (?, ?, ?, ?)",
                    (code, title, superadmin_id, admin_id),
                )
        except sqlite3.IntegrityError as exc:
            raise BookingError("Не удалось создать событие: код уже существует.") from exc

    def event_by_code(self, code: str) -> Optional[sqlite3.Row]:
        with self._connection() as connection:
            return connection.execute(
                "SELECT * FROM events WHERE code = ?", (code,)
            ).fetchone()

    def event_code_for_slot(self, slot_id: int) -> Optional[str]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT e.code FROM events e JOIN slots s ON s.event_id = e.id "
                "WHERE s.id = ?",
                (slot_id,),
            ).fetchone()
            return row["code"] if row else None

    def join_event(self, code: str, user_id: int) -> str:
        with self._connection() as connection:
            event = connection.execute(
                "SELECT id FROM events WHERE code = ?", (code,)
            ).fetchone()
            if event is None:
                raise BookingError("Событие с таким кодом не найдено.")
            connection.execute(
                "INSERT OR IGNORE INTO members(event_id, user_id) VALUES (?, ?)",
                (event["id"], user_id),
            )
            connection.execute(
                "INSERT INTO active_events(user_id, event_id) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET event_id = excluded.event_id",
                (user_id, event["id"]),
            )
        return code

    def leave_event(self, code: str, user_id: int) -> str:
        with self._connection() as connection:
            event = connection.execute(
                "SELECT id FROM events WHERE code = ?", (code,)
            ).fetchone()
            if event is None:
                raise BookingError("Событие с таким кодом не найдено.")
            deleted = connection.execute(
                "DELETE FROM members WHERE event_id = ? AND user_id = ?",
                (event["id"], user_id),
            )
            if deleted.rowcount == 0:
                raise BookingError("Вы не состоите в этом событии.")
            active = connection.execute(
                "SELECT event_id FROM active_events WHERE user_id = ?", (user_id,)
            ).fetchone()
            if active is not None and active["event_id"] == event["id"]:
                next_event = connection.execute(
                    "SELECT event_id FROM members WHERE user_id = ? "
                    "ORDER BY joined_at DESC LIMIT 1",
                    (user_id,),
                ).fetchone()
                if next_event is None:
                    connection.execute(
                        "DELETE FROM active_events WHERE user_id = ?", (user_id,)
                    )
                else:
                    connection.execute(
                        "UPDATE active_events SET event_id = ? WHERE user_id = ?",
                        (next_event["event_id"], user_id),
                    )
        return code

    def active_event_for_user(self, user_id: int) -> Optional[sqlite3.Row]:
        with self._connection() as connection:
            return connection.execute(
                "SELECT e.code, e.title FROM active_events a "
                "JOIN events e ON e.id = a.event_id "
                "JOIN members m ON m.event_id = e.id AND m.user_id = a.user_id "
                "WHERE a.user_id = ?",
                (user_id,),
            ).fetchone()

    def select_event(self, code: str, user_id: int) -> None:
        with self._connection() as connection:
            event = connection.execute(
                "SELECT e.id FROM events e JOIN members m ON m.event_id = e.id "
                "WHERE e.code = ? AND m.user_id = ?",
                (code, user_id),
            ).fetchone()
            if event is None:
                raise BookingError("Сначала добавьте это событие командой /join.")
            connection.execute(
                "INSERT INTO active_events(user_id, event_id) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET event_id = excluded.event_id",
                (user_id, event["id"]),
            )

    def events_for_user(self, user_id: int, admin_only: bool = False):
        with self._connection() as connection:
            if admin_only:
                return connection.execute(
                    "SELECT e.code, e.title FROM events e "
                    "JOIN members m ON m.event_id = e.id "
                    "WHERE m.user_id = ? AND (e.admin_id = ? OR e.superadmin_id = ?) "
                    "ORDER BY e.title",
                    (user_id, user_id, user_id),
                ).fetchall()
            return connection.execute(
                "SELECT e.code, e.title FROM events e "
                "JOIN members m ON m.event_id = e.id "
                "WHERE m.user_id = ? ORDER BY e.title",
                (user_id,),
            ).fetchall()

    def _authorized_event(
        self, connection: sqlite3.Connection, code: str, user_id: int
    ) -> sqlite3.Row:
        event = connection.execute(
            "SELECT * FROM events WHERE code = ?", (code,)
        ).fetchone()
        if event is None:
            raise BookingError("Событие с таким кодом не найдено.")
        if user_id not in (event["admin_id"], event["superadmin_id"]):
            raise BookingError("У вас нет прав администратора этого события.")
        member = connection.execute(
            "SELECT 1 FROM members WHERE event_id = ? AND user_id = ?",
            (event["id"], user_id),
        ).fetchone()
        if member is None:
            raise BookingError("Сначала добавьте это событие командой /join.")
        return event

    def add_slot(self, code: str, starts_at: str, admin_id: int) -> None:
        try:
            with self._connection() as connection:
                event = self._authorized_event(connection, code, admin_id)
                connection.execute(
                    "INSERT INTO slots(event_id, starts_at) VALUES (?, ?)",
                    (event["id"], starts_at),
                )
        except sqlite3.IntegrityError as exc:
            raise BookingError("Такое окно уже добавлено.") from exc

    def list_slots(self, code: str, user_id: int, admin_view: bool = False):
        with self._connection() as connection:
            event = connection.execute(
                "SELECT * FROM events WHERE code = ?", (code,)
            ).fetchone()
            if event is None:
                raise BookingError("Событие с таким кодом не найдено.")
            if admin_view:
                self._authorized_event(connection, code, user_id)
                return connection.execute(
                    "SELECT s.*, br.user_id AS booked_by FROM slots s "
                    "LEFT JOIN booking_requests br "
                    "ON br.slot_id = s.id AND br.status = 'approved' "
                    "WHERE s.event_id = ? ORDER BY s.starts_at",
                    (event["id"],),
                ).fetchall()
            is_member = connection.execute(
                "SELECT 1 FROM members WHERE event_id = ? AND user_id = ?",
                (event["id"], user_id),
            ).fetchone()
            if is_member is None:
                raise BookingError("Сначала добавьте событие командой /join.")
            return connection.execute(
                "SELECT * FROM slots WHERE event_id = ? AND status = 'open' "
                "ORDER BY starts_at",
                (event["id"],),
            ).fetchall()

    def request_slot(self, code: str, slot_id: int, user_id: int) -> int:
        try:
            with self._connection() as connection:
                event = connection.execute(
                    "SELECT * FROM events WHERE code = ?", (code,)
                ).fetchone()
                if event is None:
                    raise BookingError("Событие с таким кодом не найдено.")
                member = connection.execute(
                    "SELECT 1 FROM members WHERE event_id = ? AND user_id = ?",
                    (event["id"], user_id),
                ).fetchone()
                if member is None:
                    raise BookingError("Сначала добавьте событие командой /join.")
                slot = connection.execute(
                    "SELECT * FROM slots WHERE id = ? AND event_id = ?",
                    (slot_id, event["id"]),
                ).fetchone()
                if slot is None or slot["status"] != "open":
                    raise BookingError("Это окно больше недоступно.")
                cursor = connection.execute(
                    "INSERT INTO booking_requests(slot_id, user_id) VALUES (?, ?)",
                    (slot_id, user_id),
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise BookingError("Для этого окна уже есть запрос на рассмотрении.") from exc

    def request_details(self, request_id: int, admin_id: int):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT br.*, s.event_id, s.starts_at, e.code, "
                "e.admin_id, e.superadmin_id "
                "FROM booking_requests br "
                "JOIN slots s ON s.id = br.slot_id "
                "JOIN events e ON e.id = s.event_id WHERE br.id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise BookingError("Запрос не найден.")
            if admin_id not in (row["admin_id"], row["superadmin_id"]):
                raise BookingError("У вас нет прав администратора этого события.")
            member = connection.execute(
                "SELECT 1 FROM members WHERE event_id = ? AND user_id = ?",
                (row["event_id"], admin_id),
            ).fetchone()
            if member is None:
                raise BookingError("Сначала добавьте это событие командой /join.")
            return row

    def list_requests(self, code: str, admin_id: int):
        with self._connection() as connection:
            event = self._authorized_event(connection, code, admin_id)
            return connection.execute(
                "SELECT br.id, br.user_id, s.starts_at FROM booking_requests br "
                "JOIN slots s ON s.id = br.slot_id "
                "WHERE s.event_id = ? AND br.status = 'pending' "
                "ORDER BY br.created_at",
                (event["id"],),
            ).fetchall()

    def approve(self, request_id: int, admin_id: int) -> int:
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                request = connection.execute(
                    "SELECT br.*, s.event_id, s.status AS slot_status "
                    "FROM booking_requests br JOIN slots s ON s.id = br.slot_id "
                    "WHERE br.id = ?",
                    (request_id,),
                ).fetchone()
                if request is None:
                    raise BookingError("Запрос не найден.")
                event = connection.execute(
                    "SELECT * FROM events WHERE id = ?", (request["event_id"],)
                ).fetchone()
                if admin_id not in (event["admin_id"], event["superadmin_id"]):
                    raise BookingError("У вас нет прав администратора этого события.")
                member = connection.execute(
                    "SELECT 1 FROM members WHERE event_id = ? AND user_id = ?",
                    (event["id"], admin_id),
                ).fetchone()
                if member is None:
                    raise BookingError("Сначала добавьте это событие командой /join.")
                if request["status"] != "pending" or request["slot_status"] != "open":
                    raise BookingError("Запрос уже обработан или окно занято.")
                connection.execute(
                    "UPDATE booking_requests SET status = 'approved' WHERE id = ?",
                    (request_id,),
                )
                connection.execute(
                    "UPDATE slots SET status = 'booked' WHERE id = ?",
                    (request["slot_id"],),
                )
                return request["user_id"]
        except sqlite3.IntegrityError as exc:
            raise BookingError("Не удалось подтвердить запись.") from exc

    def reject(self, request_id: int, admin_id: int, message: str) -> int:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                "SELECT br.*, s.event_id FROM booking_requests br "
                "JOIN slots s ON s.id = br.slot_id WHERE br.id = ?",
                (request_id,),
            ).fetchone()
            if request is None:
                raise BookingError("Запрос не найден.")
            event = connection.execute(
                "SELECT * FROM events WHERE id = ?", (request["event_id"],)
            ).fetchone()
            if admin_id not in (event["admin_id"], event["superadmin_id"]):
                raise BookingError("У вас нет прав администратора этого события.")
            member = connection.execute(
                "SELECT 1 FROM members WHERE event_id = ? AND user_id = ?",
                (event["id"], admin_id),
            ).fetchone()
            if member is None:
                raise BookingError("Сначала добавьте это событие командой /join.")
            if request["status"] != "pending":
                raise BookingError("Запрос уже обработан.")
            connection.execute(
                "UPDATE booking_requests SET status = 'rejected', "
                "rejection_message = ? WHERE id = ?",
                (message, request_id),
            )
            return request["user_id"]
