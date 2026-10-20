from bot.discover import Found, extract_links


def test_invite_links():
    invites, _ = extract_links(
        "Чат 1 курсу: https://t.me/+AbCdEf123456_x, старий: t.me/joinchat/QWERTYUIOP12",
        [],
    )
    assert invites == {"AbCdEf123456_x", "QWERTYUIOP12"}


def test_public_links_and_mentions():
    _, names = extract_links(
        "Чат факультету @fit_knu_chat, канал https://t.me/knu_official/123, бот @knu_help_bot",
        ["https://t.me/s/profkom_knu"],
    )
    assert names == {"fit_knu_chat", "knu_official", "profkom_knu"}


def test_service_links_ignored():
    _, names = extract_links("t.me/addstickers/pack t.me/share/url?x t.me/c/123/45", [])
    assert names == set()


def test_hidden_link_in_entity():
    invites, _ = extract_links("Чат групи ЕК-21", ["https://t.me/+HiddenHash12345"])
    assert invites == {"HiddenHash12345"}


def test_study_title():
    assert Found("invite", "x", title="ФІТ 2 курс, група ПІ-22").is_study
    assert not Found("invite", "x", title="Барахолка Київ").is_study
