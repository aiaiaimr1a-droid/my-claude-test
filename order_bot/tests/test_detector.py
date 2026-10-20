import pytest

from bot.detector import detect, fingerprint, hide_budget, mask_contacts


@pytest.mark.parametrize(
    "text, work_type",
    [
        ("Ребята, нужна курсовая по финансам до 15 ноября, бюджет 1500 грн", "Coursework"),
        ("Хто може написати реферат з історії України? Терміново", "Essay / report"),
        ("Потрібна допомога з дипломною, магістерська, економіка", "Thesis"),
        ("Кто сделает лабу по C++ ? заплачу", "Lab work"),
        ("Шукаю когось, хто зробить звіт з практики", "Internship report"),
        ("Потрібна praca licencjacka з економіки, хто може?", "Thesis"),
        ("Szukam kogoś, kto napisze pracę magisterską", "Thesis"),
        ("Хто допоможе з Hausarbeit? Brauche Hilfe", "Coursework"),
    ],
)
def test_orders_detected(text, work_type):
    result = detect(text)
    assert result.is_order, result.reason
    assert result.work_type == work_type


@pytest.mark.parametrize(
    "text",
    [
        "Пишу курсовые и дипломы, обращайтесь в лс, без предоплаты!",
        "Виконую реферати, якісно та недорого",
        "Piszę prace licencjackie, zapraszam",
        "Сегодня пара по курсовой отменяется",  # нет просьбы
        "Кто идёт на пару?",  # нет типа работы
        "ок",
    ],
)
def test_non_orders_skipped(text):
    assert not detect(text).is_order


def test_budget_extracted():
    assert detect("Нужна курсовая, бюджет 1 500 грн").budget == "1 500 грн"


@pytest.mark.parametrize(
    "text, budget",
    [
        ("нужно сделать курсовую бюджет 1000", "1000"),
        ("Потрібна курсова, ціна до 1 500", "1 500"),
        ("нужна курсовая, заплачу 800$", "800$"),
        ("нужна курсовая к пятнице", None),
        ("Кто напишет реферат? Плачу 1500р", "1500р"),
        ("нужна курсовая за 5000 руб", "5000 руб"),
        ("потрібна курсова, 400 zł", "400 zł"),
    ],
)
def test_budget_variants(text, budget):
    assert detect(text).budget == budget


def test_hide_budget():
    hidden = hide_budget("нужна курсовая, бюджет 1000, или 1200 грн")
    assert "1000" not in hidden and "1200" not in hidden


def test_fingerprint_ignores_case_and_punctuation():
    assert fingerprint("Нужна курсовая!!!") == fingerprint("нужна   курсовая")


def test_contacts_masked():
    masked = mask_contacts("Нужна курсовая, пишите @student_ivan или +380 67 123 45 67, t.me/x")
    assert "@student_ivan" not in masked
    assert "123 45 67" not in masked
    assert "t.me" not in masked
