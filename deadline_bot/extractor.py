"""Розбір повідомлень замовника через Claude API (structured outputs)."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

import anthropic

log = logging.getLogger(__name__)

WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя"]

DEADLINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "deadline": {"type": "string"},
        "price": {"type": "string"},
        "customer": {"type": "string"},
    },
    "required": ["summary", "deadline", "price", "customer"],
    "additionalProperties": False,
}

DEADLINE_SCHEMA = {
    "type": "object",
    "properties": {"deadline": {"type": "string"}},
    "required": ["deadline"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Ти — помічник фрилансера. Тобі надходять повідомлення від замовника \
(переслані в Telegram) і, можливо, коментар самого фрилансера. Витягни з них дані про замовлення.

Поля відповіді:
- summary: суть задачі українською мовою, стисло (1–3 речення), без вітань і зайвого. \
Якщо повідомлення іншою мовою — перекажи українською.
- deadline: дедлайн у форматі "YYYY-MM-DD", або "YYYY-MM-DDTHH:MM", якщо вказано час. \
Відносні дати ("завтра", "до пʼятниці", "через тиждень") рахуй від дати, коли замовник написав повідомлення. \
Якщо фрилансер у коментарі вказав дату — вона має пріоритет. Якщо дедлайну немає — порожній рядок.
- price: ціна/бюджет так, як вказано (з валютою, напр. "1500 грн", "$50", "договірна"). Якщо немає — порожній рядок.
- customer: імʼя, нік, компанія чи контакт замовника, якщо згадані в тексті. Якщо немає — порожній рядок.

Нічого не вигадуй: якщо даних немає, залишай поле порожнім."""


@dataclass
class Extracted:
    summary: str
    deadline: str | None
    price: str | None
    customer: str | None


def _describe_now(now: datetime) -> str:
    return f"{now:%Y-%m-%d %H:%M} ({WEEKDAYS[now.weekday()]})"


def _clean_deadline(value: str) -> str | None:
    value = value.strip()
    if not DEADLINE_RE.match(value):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


class Extractor:
    def __init__(self, model: str):
        self.client = anthropic.AsyncAnthropic()
        self.model = model

    async def _ask(self, system: str, user_text: str, schema: dict) -> dict | None:
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=system,
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": schema},
                },
                messages=[{"role": "user", "content": user_text}],
            )
        except anthropic.RateLimitError:
            log.warning("Claude API: перевищено ліміт запитів")
            return None
        except anthropic.APIStatusError as e:
            log.error("Claude API error %s: %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError:
            log.error("Claude API: немає зʼєднання")
            return None

        if response.stop_reason == "refusal":
            log.warning("Claude відмовився обробляти повідомлення")
            return None
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.error("Не вдалося розібрати відповідь Claude: %r", text[:200])
            return None

    async def extract_task(self, client_messages: list[tuple[datetime, str]],
                           owner_comment: str, now: datetime) -> Extracted | None:
        parts = [f"Зараз: {_describe_now(now)}."]
        if client_messages:
            parts.append("Повідомлення замовника:")
            for sent_at, text in client_messages:
                parts.append(f"[{_describe_now(sent_at)}]\n{text}")
        if owner_comment:
            parts.append(f"Коментар фрилансера:\n{owner_comment}")

        data = await self._ask(SYSTEM_PROMPT, "\n\n".join(parts), TASK_SCHEMA)
        if data is None:
            return None
        return Extracted(
            summary=data["summary"].strip(),
            deadline=_clean_deadline(data["deadline"]),
            price=data["price"].strip() or None,
            customer=data["customer"].strip() or None,
        )

    async def parse_deadline(self, text: str, now: datetime) -> str | None:
        system = (
            "Перетвори дату з повідомлення користувача у формат \"YYYY-MM-DD\", або "
            "\"YYYY-MM-DDTHH:MM\", якщо вказано час. Відносні дати рахуй від поточного моменту. "
            "Якщо дату розпізнати неможливо — поверни порожній рядок."
        )
        data = await self._ask(system, f"Зараз: {_describe_now(now)}.\n\nПовідомлення: {text}", DEADLINE_SCHEMA)
        if data is None:
            return None
        return _clean_deadline(data["deadline"])
