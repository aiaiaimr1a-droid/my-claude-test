import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Task:
    id: int
    summary: str
    deadline: str | None  # "YYYY-MM-DD" або "YYYY-MM-DDTHH:MM" (місцевий час)
    price: str | None
    customer: str | None
    customer_link: str | None
    source_text: str
    status: str
    reminded_day: bool
    reminded_soon: bool
    created_at: str

    @property
    def has_time(self) -> bool:
        return self.deadline is not None and "T" in self.deadline


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    summary       TEXT NOT NULL,
    deadline      TEXT,
    price         TEXT,
    customer      TEXT,
    customer_link TEXT,
    source_text   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',
    reminded_day  INTEGER NOT NULL DEFAULT 0,
    reminded_soon INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> Task | None:
        if row is None:
            return None
        data = dict(row)
        data["reminded_day"] = bool(data["reminded_day"])
        data["reminded_soon"] = bool(data["reminded_soon"])
        return Task(**data)

    def add(self, *, summary: str, deadline: str | None, price: str | None,
            customer: str | None, customer_link: str | None, source_text: str) -> Task:
        cur = self.conn.execute(
            "INSERT INTO tasks (summary, deadline, price, customer, customer_link, source_text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (summary, deadline, price, customer, customer_link, source_text,
             datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, task_id: int) -> Task | None:
        return self._row(self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())

    def active(self) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE status = 'active'"
            " ORDER BY deadline IS NULL, deadline, id"
        ).fetchall()
        return [self._row(r) for r in rows]

    def set_deadline(self, task_id: int, deadline: str | None) -> None:
        self.conn.execute(
            "UPDATE tasks SET deadline = ?, reminded_day = 0, reminded_soon = 0 WHERE id = ?",
            (deadline, task_id),
        )
        self.conn.commit()

    def set_reminded(self, task_id: int, *, day: bool | None = None, soon: bool | None = None) -> None:
        if day is not None:
            self.conn.execute("UPDATE tasks SET reminded_day = ? WHERE id = ?", (int(day), task_id))
        if soon is not None:
            self.conn.execute("UPDATE tasks SET reminded_soon = ? WHERE id = ?", (int(soon), task_id))
        self.conn.commit()

    def set_status(self, task_id: int, status: str) -> None:
        self.conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        self.conn.commit()

    def delete(self, task_id: int) -> None:
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()
