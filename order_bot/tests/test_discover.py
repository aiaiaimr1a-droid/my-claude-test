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


def test_check_queue_order_and_cache(tmp_path, monkeypatch):
    import time

    import bot.discover as d

    monkeypatch.setattr(d, "CACHE_FILE", tmp_path / "cache.json")
    now = time.time()
    old_any = Found("invite", "old", posted_at=now - 400 * 86400)
    new_any = Found("invite", "new", posted_at=now - 86400)
    study_old = Found("invite", "study", posted_at=now - 300 * 86400, context_study=True)
    expired = Found("invite", "dead", status="expired")
    fresh_ok = Found("invite", "ok", status="ok", checked_at=now)
    stale_ok = Found("invite", "stale", status="ok", checked_at=now - 30 * 86400)

    items = [old_any, new_any, study_old, expired, fresh_ok, stale_ok]
    queue = sorted((i for i in items if i.needs_check()), key=Found.priority)
    assert [i.key for i in queue][:3] == ["study", "new", "old"]
    assert "dead" not in [i.key for i in queue] and "ok" not in [i.key for i in queue]
    assert "stale" in [i.key for i in queue]

    d._save_cache({f"invite:{i.key}": i for i in items})
    loaded = d._load_cache()
    assert loaded["invite:dead"].status == "expired"
    assert loaded["invite:study"].context_study
