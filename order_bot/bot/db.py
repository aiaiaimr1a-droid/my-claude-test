import sqlite3
import time

from .models import Order, RawMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint      TEXT NOT NULL,
    chat_id          INTEGER NOT NULL,
    chat_title       TEXT NOT NULL,
    message_id       INTEGER NOT NULL,
    link             TEXT,
    author_id        INTEGER,
    author_username  TEXT,
    author_name      TEXT NOT NULL,
    text             TEXT NOT NULL,
    work_type        TEXT,
    budget           TEXT,
    created_at       INTEGER NOT NULL,
    paid_message_id  INTEGER,
    free_due_at      INTEGER,
    free_posted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_orders_fp ON orders (fingerprint, created_at);
CREATE INDEX IF NOT EXISTS idx_orders_free ON orders (free_posted, free_due_at);

CREATE TABLE IF NOT EXISTS contacts (
    order_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    opened_at  INTEGER NOT NULL,
    PRIMARY KEY (order_id, user_id)
);
"""

ORDER_COLUMNS = (
    "id, chat_title, link, author_id, author_username, author_name, "
    "text, work_type, budget, paid_message_id"
)


class Database:
    """Синхронный sqlite: запросы короткие, а между проверкой лимита
    и записью нет await — значит, гонок внутри одного процесса нет."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    def is_duplicate(self, fp: str, hours: int) -> bool:
        since = int(time.time()) - hours * 3600
        row = self.conn.execute(
            "SELECT 1 FROM orders WHERE fingerprint = ? AND created_at >= ? LIMIT 1",
            (fp, since),
        ).fetchone()
        return row is not None

    def add_order(
        self,
        msg: RawMessage,
        fp: str,
        work_type: str | None,
        budget: str | None,
        free_due_at: int | None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO orders (fingerprint, chat_id, chat_title, message_id, link,
                   author_id, author_username, author_name, text, work_type, budget,
                   created_at, free_due_at, free_posted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fp, msg.chat_id, msg.chat_title, msg.message_id, msg.link,
                msg.author_id, msg.author_username, msg.author_name, msg.text,
                work_type, budget, int(time.time()), free_due_at,
                0 if free_due_at else 1,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_paid_message(self, order_id: int, message_id: int) -> None:
        self.conn.execute(
            "UPDATE orders SET paid_message_id = ? WHERE id = ?", (message_id, order_id)
        )
        self.conn.commit()

    def get_order(self, order_id: int) -> Order | None:
        row = self.conn.execute(
            f"SELECT {ORDER_COLUMNS} FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        return Order(*row) if row else None

    def due_free_posts(self) -> list[Order]:
        rows = self.conn.execute(
            f"SELECT {ORDER_COLUMNS} FROM orders "
            "WHERE free_posted = 0 AND free_due_at <= ? ORDER BY id",
            (int(time.time()),),
        ).fetchall()
        return [Order(*r) for r in rows]

    def mark_free_posted(self, order_id: int) -> None:
        self.conn.execute("UPDATE orders SET free_posted = 1 WHERE id = ?", (order_id,))
        self.conn.commit()

    def contacts_count(self, order_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE order_id = ?", (order_id,)
        ).fetchone()[0]

    def has_contact(self, order_id: int, user_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM contacts WHERE order_id = ? AND user_id = ?",
            (order_id, user_id),
        ).fetchone() is not None

    def add_contact(self, order_id: int, user_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO contacts (order_id, user_id, opened_at) VALUES (?, ?, ?)",
            (order_id, user_id, int(time.time())),
        )
        self.conn.commit()

    def stats(self, days: int = 7) -> dict:
        since = int(time.time()) - days * 86400
        orders = self.conn.execute(
            "SELECT COUNT(*) FROM orders WHERE created_at >= ?", (since,)
        ).fetchone()[0]
        opened = self.conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE opened_at >= ?", (since,)
        ).fetchone()[0]
        authors = self.conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM contacts WHERE opened_at >= ?", (since,)
        ).fetchone()[0]
        by_source = self.conn.execute(
            "SELECT chat_title, COUNT(*) AS n FROM orders WHERE created_at >= ? "
            "GROUP BY chat_id ORDER BY n DESC LIMIT 10",
            (since,),
        ).fetchall()
        return {
            "orders": orders,
            "opened": opened,
            "authors": authors,
            "by_source": by_source,
        }
