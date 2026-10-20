"""Распознавание заказов в потоке сообщений.

Простые правила на ключевых словах (RU + UA). Идея:
  1. отсечь рекламу авторов ("пишу курсовые, обращайтесь");
  2. найти тип работы;
  3. убедиться, что это просьба, а не обсуждение.
Списки слов — главное, что стоит подкручивать под свои чаты.
"""

import hashlib
import re
from dataclasses import dataclass

# Тип работы -> корни слов
WORK_TYPES: dict[str, tuple[str, ...]] = {
    "Thesis": ("диплом", "бакалаврськ", "бакалаврск", "магістерськ", "магистерск"),
    "Coursework": ("курсов",),
    "Essay / report": ("реферат",),
    "Test paper": ("контрольн", "модульн"),
    "Essay": ("эссе", "есе"),
    "Internship report": ("по практик", "з практик", "звіт", "отчет по", "отчёт по"),
    "Lab work": ("лаборатор", "лабу", "лабы", "лаби"),
    "Presentation": ("презентац",),
    "Problems": ("задач", "розв'яз", "решить", "вирішити"),
    "Test / exam": ("тест", "экзамен", "екзамен", "залік", "зачет", "зачёт"),
    "Article / abstract": ("стать", "тези", "тезис"),
}

# Признаки того, что человек ищет исполнителя
REQUEST_MARKERS = (
    "нужн", "нужен", "надо", "треба", "потрібн", "помогите", "допоможіть", "допоможе",
    "кто может", "кто напишет", "кто сделает", "кто возьм", "хто може", "хто напише",
    "хто зробить", "хто візьм", "ищу", "шукаю", "замов", "сколько стоит", "скільки кошт",
    "кто шарит", "хто шарить", "?",
)

# Признаки рекламы от авторов / сервисов — такие сообщения отбрасываем
AD_MARKERS = (
    "пишу ", "напишу", "выполню", "выполняю", "виконаю", "виконую", "обращайтесь",
    "звертайтесь", "пишите в лс", "пишіть в лс", "пишіть в особист", "без предоплат",
    "без передоплат", "гарантия", "гарантія", "наш сервис", "наш сервіс", "скидк",
    "знижк", "недорого", "качественно", "якісно", "опыт работы", "досвід роботи",
    "принимаю заказы", "приймаю замовлення",
)

MIN_LEN = 15
MAX_LEN = 1200  # длинные полотна почти всегда реклама

BUDGET_RE = re.compile(
    r"(\d[\d\s.,]{0,7})\s*(грн|гривен|гривень|uah|₴|\$|usd|дол)",
    re.IGNORECASE,
)
# "бюджет 1000", "ціна до 800", "заплачу 500" — без валюты, считаем гривнами
BUDGET_WORD_RE = re.compile(
    r"(бюджет|цена|ціна|оплата|заплачу|плачу|вартість|стоимость)\D{0,6}(\d[\d\s]{1,6}\d|\d{2,})",
    re.IGNORECASE,
)
MENTION_RE = re.compile(r"@[A-Za-z0-9_]{4,}")
URL_RE = re.compile(r"(https?://\S+|t\.me/\S+)", re.IGNORECASE)
PHONE_RE = re.compile(r"\+?\d[\d\s()-]{8,}\d")


@dataclass
class Detection:
    is_order: bool
    reason: str
    work_type: str | None = None
    budget: str | None = None


def detect(text: str) -> Detection:
    low = text.lower()

    if not MIN_LEN <= len(text) <= MAX_LEN:
        return Detection(False, "length")
    if any(m in low for m in AD_MARKERS):
        return Detection(False, "ad")
    if len(URL_RE.findall(text)) + len(MENTION_RE.findall(text)) > 2:
        return Detection(False, "too many links")

    work_type = next(
        (name for name, roots in WORK_TYPES.items() if any(r in low for r in roots)),
        None,
    )
    if work_type is None:
        return Detection(False, "no work type")
    if not any(m in low for m in REQUEST_MARKERS):
        return Detection(False, "no request")

    return Detection(True, "ok", work_type, find_budget(text))


def find_budget(text: str) -> str | None:
    match = BUDGET_RE.search(text)
    if match:
        return " ".join(match.group(0).split())
    match = BUDGET_WORD_RE.search(text)
    if match:
        return " ".join(match.group(2).split()) + " UAH"
    return None


def hide_budget(text: str) -> str:
    """Для бесплатного канала: бюджет видят только платные подписчики."""
    text = BUDGET_RE.sub("💰🔒", text)
    return BUDGET_WORD_RE.sub(lambda m: f"{m.group(1)} 💰🔒", text)


def fingerprint(text: str) -> str:
    """Отпечаток текста для поиска дублей: один студент часто кидает
    одно и то же в 5 чатов, а в канал это должно попасть один раз."""
    norm = re.sub(r"[^\w]+", " ", text.lower()).strip()
    return hashlib.sha1(norm[:300].encode()).hexdigest()


def mask_contacts(text: str) -> str:
    """Прячем @username, ссылки и телефоны — иначе лимит контактов
    теряет смысл: автор просто напишет напрямую из текста."""
    text = URL_RE.sub("🔒", text)
    text = MENTION_RE.sub("🔒", text)
    return PHONE_RE.sub("🔒", text)
