from dataclasses import dataclass


@dataclass
class RawMessage:
    """Сообщение из любого источника (бот или юзербот) в едином виде."""

    chat_id: int
    chat_title: str
    message_id: int
    link: str | None
    author_id: int | None
    author_username: str | None
    author_name: str
    text: str


@dataclass
class Order:
    id: int
    chat_title: str
    link: str | None
    author_id: int | None
    author_username: str | None
    author_name: str
    text: str
    work_type: str | None
    budget: str | None
    paid_message_id: int | None
