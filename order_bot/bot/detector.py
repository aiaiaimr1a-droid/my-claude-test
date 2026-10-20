"""Распознавание заказов в потоке сообщений.

Простые правила на ключевых словах (RU + UA, плюс PL/CZ/DE для студентов
за рубежом). Идея:
  1. отсечь рекламу авторов ("пишу курсовые, обращайтесь");
  2. найти тип работы;
  3. убедиться, что это просьба, а не обсуждение.
Списки слов — главное, что стоит подкручивать под свои чаты.
"""

import hashlib
import re
from dataclasses import dataclass

# Все слова ищутся по НАЧАЛУ слова: корень "есе" найдёт "есе" и "есей",
# но не "донесения" и "пересек".

# Тип работы -> корни слов. Порядок важен: берётся первый подошедший тип.
WORK_TYPES: dict[str, tuple[str, ...]] = {
    "Thesis": (
        "диплом", "бакалаврськ", "бакалаврск", "магістерськ", "магистерск",
        "выпускн", "випускн", "вкр",
        "licencjack", "magistersk", "dyplomow", "bakalářsk", "diplomov",
        "bachelorarbeit", "masterarbeit",
    ),
    "Coursework": (
        "курсов", "курсач", "курсак", "hausarbeit", "seminární", "semestráln",
    ),
    "Essay / report": ("реферат", "referat", "доклад", "доповід"),
    "Test paper": ("контрольн", "модульн", "самостоятельн", "самостійн"),
    "Essay": ("эссе", "есе", "esej", "essay"),
    "Internship report": ("по практик", "з практик", "звіт", "отчет по", "отчёт по"),
    "Lab work": ("лаборатор", "лабу", "лабы", "лаби", "лаба", "лабк"),
    "Presentation": ("презентац", "prezentac", "präsentation"),
    "Calculation / drawing": (
        "ргр", "расчетно", "расчётно", "розрахунков", "чертеж", "чертёж", "креслен",
    ),
    "Problems": ("задач", "розв'яз", "решить", "вирішити"),
    "Test / exam": ("тест", "экзамен", "екзамен", "залік", "зачет", "зачёт"),
    "Article / abstract": ("стать", "тези", "тезис"),
}

# Слишком общие слова: "задача", "тест", "статья" встречаются везде.
# Для них нужна явная просьба решить/сделать или упоминание оплаты.
WEAK_TYPES = {"Problems", "Test / exam", "Article / abstract"}
WEAK_CONFIRM = (
    "кто решит", "кто может решить", "помогите решить", "решить за", "хто розв'яже",
    "хто вирішить", "допоможіть розв'язати", "допоможіть вирішити", "кто пройдет",
    "кто пройдёт", "хто пройде", "кто сдаст", "хто здасть", "кто напишет", "хто напише",
    "заплачу", "плачу", "оплачу", "оплата", "за сколько", "за скільки",
    "сколько стоит", "скільки кошт", "цена", "ціна", "бюджет",
)

# Признаки того, что человек ищет исполнителя
REQUEST_MARKERS = (
    "нужн", "нужен", "надо", "треба", "потрібн", "помогите", "допоможіть", "допоможе",
    "кто может", "кто сможет", "кто напишет", "кто сделает", "кто возьм", "кто поможет",
    "хто може", "хто зможе", "хто напише", "хто зробить", "хто візьм",
    "ищу", "шукаю", "замов", "сколько стоит", "скільки кошт", "за сколько", "за скільки",
    "кто шарит", "хто шарить", "можете помочь", "можете допомогти",
    "кто решит", "хто розв'яже", "хто вирішить", "кто пройдет", "кто пройдёт",
    "хто пройде", "кто сдаст", "хто здасть",
    # PL / CZ / DE
    "potrzebuj", "szukam", "kto napisze", "kto zrobi", "ile kosztuje", "hledám",
    "potřebuj", "kdo napíše", "suche", "brauche", "wer kann",
)

# Признаки рекламы от авторов, сервисов и репетиторов — такие сообщения отбрасываем
AD_MARKERS = (
    "пишу ", "напишу", "выполню", "выполняю", "виконаю", "виконую", "обращайтесь",
    "звертайтесь", "пишите в лс", "пишіть в лс", "пишіть в особист", "без предоплат",
    "без передоплат", "гарантия", "гарантія", "наш сервис", "наш сервіс", "скидк",
    "знижк", "опыт работы", "досвід роботи", "принимаю заказы", "приймаю замовлення",
    "выполняем", "виконуємо", "пишем ", "пишемо", "помогу", "допоможу", "сделаю", "зроблю",
    "предлагаю", "пропоную", "записывайтесь", "записуйтесь",
    # репетиторы
    "репетитор", "провожу занят", "проводжу занят", "занятия", "заняття", "уроки",
    "готовлю к", "готую до", "подготовка к", "підготовка до", "пробное занят",
    "пробне занят", "индивидуальн", "індивідуальн", "мои ученики", "мої учні",
    "преподаю", "викладаю",
    # PL / CZ / DE
    "piszę", "napiszę", "oferuję", "zapraszam", "píšu", "napíšu", "nabízím",
    "schreibe", "biete", "korepetycj", "doučování", "nachhilfe",
)


def _starts_word(words: tuple[str, ...]) -> re.Pattern:
    alternatives = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})", re.IGNORECASE)


TYPE_PATTERNS = {name: _starts_word(roots) for name, roots in WORK_TYPES.items()}
WEAK_CONFIRM_RE = _starts_word(WEAK_CONFIRM)
REQUEST_RE = _starts_word(REQUEST_MARKERS)
AD_RE = _starts_word(AD_MARKERS)

MIN_LEN = 15
MAX_LEN = 1200  # длинные полотна почти всегда реклама

BUDGET_RE = re.compile(
    r"(\d[\d\s.,]{0,7})\s*(грн|гривен|гривень|uah|₴|руб|₽|р\.?(?!\w)|тг|тенге|₸|byn|zł|zl|злот|€|eur|\$|usd|дол)",
    re.IGNORECASE,
)
# "бюджет 1000", "ціна до 800", "заплачу 500" — валюта не указана
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
    if not MIN_LEN <= len(text) <= MAX_LEN:
        return Detection(False, "length")
    if AD_RE.search(text):
        return Detection(False, "ad")
    if len(URL_RE.findall(text)) + len(MENTION_RE.findall(text)) > 2:
        return Detection(False, "too many links")

    work_type = next((name for name, p in TYPE_PATTERNS.items() if p.search(text)), None)
    if work_type is None:
        return Detection(False, "no work type")
    if not REQUEST_RE.search(text):
        return Detection(False, "no request")

    budget = find_budget(text)
    if work_type in WEAK_TYPES and not (budget or WEAK_CONFIRM_RE.search(text)):
        return Detection(False, "weak type without confirmation")

    return Detection(True, "ok", work_type, budget)


def find_budget(text: str) -> str | None:
    match = BUDGET_RE.search(text)
    if match:
        return " ".join(match.group(0).split())
    match = BUDGET_WORD_RE.search(text)
    if match:
        return " ".join(match.group(2).split())
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
