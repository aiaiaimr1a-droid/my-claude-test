import pytest

from bot.detector import detect, fingerprint, mask_contacts


@pytest.mark.parametrize(
    "text, work_type",
    [
        ("Ребята, нужна курсовая по финансам до 15 ноября, бюджет 1500 грн", "Курсовая"),
        ("Хто може написати реферат з історії України? Терміново", "Реферат"),
        ("Потрібна допомога з дипломною, магістерська, економіка", "Дипломная"),
        ("Кто сделает лабу по C++ ? заплачу", "Лабораторная"),
        ("Шукаю когось, хто зробить звіт з практики", "Отчёт по практике"),
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
        "Сегодня пара по курсовой отменяется",  # нет просьбы
        "Кто идёт на пару?",  # нет типа работы
        "ок",
    ],
)
def test_non_orders_skipped(text):
    assert not detect(text).is_order


def test_budget_extracted():
    assert detect("Нужна курсовая, бюджет 1 500 грн").budget == "1 500 грн"


def test_fingerprint_ignores_case_and_punctuation():
    assert fingerprint("Нужна курсовая!!!") == fingerprint("нужна   курсовая")


def test_contacts_masked():
    masked = mask_contacts("Нужна курсовая, пишите @student_ivan или +380 67 123 45 67, t.me/x")
    assert "@student_ivan" not in masked
    assert "123 45 67" not in masked
    assert "t.me" not in masked
