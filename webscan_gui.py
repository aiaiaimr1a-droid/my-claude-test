#!/usr/bin/env python3
"""
webscan_gui.py — визуальный веб-интерфейс (Gradio) к сканеру портов webscan.py.

Переиспользует функции из webscan.py (разбор целей и портов, проверку
одного порта), поэтому ДОЛЖЕН лежать в той же папке, что и webscan.py.

Результаты каждого сканирования сохраняются в ОТДЕЛЬНУЮ ПАПКУ рядом со
скриптом — webscan_results/ — в двух файлах:
    webscan_results_ГГГГММДД_ЧЧММСС.txt  — по IP, под каждым его порты
    webscan_results_ГГГГММДД_ЧЧММСС.csv  — та же таблица для Excel

Инструмент рассчитан на небольшие (локальные) диапазоны — он намеренно
простой и не распараллеливает всё подряд, чтобы не зависать.

Запуск:
    python webscan_gui.py
Затем открой в браузере адрес из консоли (обычно http://127.0.0.1:7860).

⚠️  Сканируй только то, что тебе принадлежит или на что есть явное разрешение.
"""

import concurrent.futures
import csv
import datetime as dt
import html
import os
import socket
import tempfile

import gradio as gr

import webscan  # webscan.py лежит рядом — берём из него готовую логику.

MAX_HOSTS = 1024
# Инструмент для небольших сетей: ограничиваем число одновременных проверок,
# чтобы большой список портов не завесил интерфейс.
MAX_WORKERS = 64

# Отдельная папка для результатов рядом со скриптом.
RESULTS_DIRNAME = "webscan_results"

try:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    SCRIPT_DIR = os.getcwd()

SAVE_DIR = os.path.join(SCRIPT_DIR, RESULTS_DIRNAME)

FILTER_ALL = "Все порты"
FILTER_OPEN = "Только открытые"
FILTER_CLOSED = "Только закрытые"


# --------------------------------------------------------------------------- #
# Сканирование
# --------------------------------------------------------------------------- #
def _scan_all(hosts, ports, timeout):
    details, unresolved = [], []
    workers = max(1, min(len(ports), MAX_WORKERS))
    for host in hosts:
        try:
            socket.gethostbyname(host)
        except socket.gaierror:
            unresolved.append(host)
            continue
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(webscan.scan_port, host, p, timeout) for p in ports]
            for future in concurrent.futures.as_completed(futures):
                port, is_open = future.result()
                details.append((host, port, is_open))
    details.sort(key=lambda r: (hosts.index(r[0]), r[1]))
    return details, unresolved


def _ports_by_host(details, hosts):
    result = {}
    for host in hosts:
        opens = sorted(p for (h, p, ok) in details if h == host and ok)
        closes = sorted(p for (h, p, ok) in details if h == host and not ok)
        result[host] = (opens, closes)
    return result


# --------------------------------------------------------------------------- #
# Сохранение файлов
# --------------------------------------------------------------------------- #
def _ensure_save_dir():
    """Создаёт отдельную папку для результатов. Возвращает рабочий каталог.

    Если создать её рядом со скриптом нельзя (нет прав) — откатываемся во
    временную папку, чтобы сканирование не падало.
    """
    try:
        os.makedirs(SAVE_DIR, exist_ok=True)
        return SAVE_DIR
    except OSError:
        fallback = os.path.join(tempfile.gettempdir(), RESULTS_DIRNAME)
        os.makedirs(fallback, exist_ok=True)
        return fallback


def _open_for_write(save_dir, filename):
    path = os.path.join(save_dir, filename)
    return open(path, "w", newline="", encoding="utf-8-sig"), path


def _write_txt(save_dir, hosts, unresolved, by_host, mode, stamp, now_human):
    f, path = _open_for_write(save_dir, f"webscan_results_{stamp}.txt")
    with f:
        f.write("webscan — результаты сканирования\n")
        f.write(f"Время:  {now_human}\n")
        f.write(f"Фильтр: {mode}\n")
        f.write("=" * 44 + "\n\n")
        for host in hosts:
            if host in unresolved:
                f.write(f"{host}\n    не удалось разрешить имя\n\n")
                continue
            opens, closes = by_host[host]
            f.write(f"{host}\n")
            if mode in (FILTER_ALL, FILTER_OPEN):
                f.write(f"    открытые: {', '.join(map(str, opens)) if opens else '—'}\n")
            if mode in (FILTER_ALL, FILTER_CLOSED):
                f.write(f"    закрытые: {', '.join(map(str, closes)) if closes else '—'}\n")
            f.write("\n")
    return path


def _write_csv(save_dir, details, unresolved, mode, stamp, now_iso):
    f, path = _open_for_write(save_dir, f"webscan_results_{stamp}.csv")
    with f:
        writer = csv.writer(f)
        writer.writerow(["Время", "Хост", "Порт", "Статус"])
        for host, port, is_open in details:
            if mode == FILTER_OPEN and not is_open:
                continue
            if mode == FILTER_CLOSED and is_open:
                continue
            writer.writerow([now_iso, host, port, "открыт" if is_open else "закрыт"])
        if mode == FILTER_ALL:
            for host in unresolved:
                writer.writerow([now_iso, host, "", "не удалось разрешить имя"])
    return path


# --------------------------------------------------------------------------- #
# Отрисовка результатов (HTML)
# --------------------------------------------------------------------------- #
def _tile(value, label, color):
    return (
        f'<div style="flex:1;min-width:130px;background:{color}14;'
        f'border:1px solid {color}40;border-radius:14px;padding:16px 18px;">'
        f'<div style="font-size:28px;font-weight:700;color:{color};line-height:1;">{value}</div>'
        f'<div style="font-size:11px;opacity:.7;margin-top:6px;text-transform:uppercase;'
        f'letter-spacing:.05em;">{label}</div></div>'
    )


def _badge(text, bg, fg):
    return (
        f'<span style="background:{bg};color:{fg};padding:3px 11px;border-radius:999px;'
        f'font-size:12px;font-weight:600;white-space:nowrap;">{text}</span>'
    )


def _notice(text, color="#b45309"):
    return (
        f'<div style="padding:16px 18px;border-radius:12px;border:1px solid {color}40;'
        f'background:{color}10;color:{color};font-weight:500;">{text}</div>'
    )


def _results_html(hosts, unresolved, by_host, open_total, n_ports, mode):
    hosts_with_open = sum(1 for h in hosts if h not in unresolved and by_host[h][0])
    tiles = (
        _tile(len(hosts), "адресов проверено", "#4f46e5")
        + _tile(hosts_with_open, "с открытыми портами", "#16a34a")
        + _tile(open_total, "открытых портов", "#0891b2")
        + _tile(len(unresolved), "имя не разрешилось", "#d97706")
    )

    rows = []
    for host in hosts:
        host_cell = f'<td style="padding:10px 12px;font-family:monospace;">{html.escape(host)}</td>'
        if host in unresolved:
            badge = _badge("имя не разрешилось", "#fef3c7", "#b45309")
            rows.append(
                f'<tr style="border-bottom:1px solid #8884;">{host_cell}'
                f'<td style="padding:10px 12px;opacity:.5;">—</td>'
                f'<td style="padding:10px 12px;opacity:.5;">—</td>'
                f'<td style="padding:10px 12px;">{badge}</td></tr>'
            )
            continue
        opens, closes = by_host[host]
        opens_txt = ", ".join(map(str, opens)) if opens else "—"
        closes_txt = ", ".join(map(str, closes)) if closes else "—"
        if opens:
            badge = _badge("открыт", "#dcfce7", "#15803d")
        else:
            badge = _badge("закрыт / нет ответа", "#8882", "#6b7280")
        rows.append(
            f'<tr style="border-bottom:1px solid #8884;">{host_cell}'
            f'<td style="padding:10px 12px;color:#16a34a;font-weight:600;">{opens_txt}</td>'
            f'<td style="padding:10px 12px;opacity:.55;">{closes_txt}</td>'
            f'<td style="padding:10px 12px;">{badge}</td></tr>'
        )

    table = (
        '<table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:4px;">'
        '<thead><tr style="text-align:left;border-bottom:2px solid #8886;">'
        '<th style="padding:10px 12px;">Хост</th>'
        '<th style="padding:10px 12px;">Открытые порты</th>'
        '<th style="padding:10px 12px;">Закрытые</th>'
        '<th style="padding:10px 12px;">Статус</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )

    return (
        f'<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px;">{tiles}</div>'
        f'<div style="overflow-x:auto;">{table}</div>'
    )


# --------------------------------------------------------------------------- #
# Обработчик кнопки
# --------------------------------------------------------------------------- #
def run_scan(targets_text, ports_text, timeout, save_mode):
    raw_targets = targets_text.split()
    if not raw_targets:
        return _notice("⚠️ Укажите хотя бы один адрес, диапазон или CIDR."), None

    try:
        hosts = webscan.expand_targets(raw_targets)
    except ValueError as exc:
        return _notice(f"⚠️ Ошибка в целях: {html.escape(str(exc))}"), None

    try:
        ports = webscan.parse_ports(ports_text)
    except ValueError as exc:
        return _notice(f"⚠️ Ошибка в списке портов: {html.escape(str(exc))}"), None

    if len(hosts) > MAX_HOSTS:
        return _notice(
            f"⚠️ Слишком много адресов ({len(hosts)}). "
            f"Сузьте диапазон до {MAX_HOSTS} или меньше.", "#dc2626"
        ), None

    details, unresolved = _scan_all(hosts, ports, timeout)
    by_host = _ports_by_host(details, [h for h in hosts if h not in unresolved])
    open_total = sum(len(by_host[h][0]) for h in hosts if h not in unresolved)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    now_human = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now_iso = dt.datetime.now().isoformat(timespec="seconds")

    save_dir = _ensure_save_dir()
    txt_path = _write_txt(save_dir, hosts, unresolved, by_host, save_mode, stamp, now_human)
    csv_path = _write_csv(save_dir, details, unresolved, save_mode, stamp, now_iso)

    body = _results_html(hosts, unresolved, by_host, open_total, len(ports), save_mode)
    saved = (
        f'<div style="margin-top:16px;font-size:12px;opacity:.7;">'
        f'💾 Сохранено ({html.escape(save_mode.lower())}) в папку '
        f'<code>{html.escape(os.path.basename(save_dir.rstrip(os.sep)) or save_dir)}/</code>: '
        f'<code>{html.escape(os.path.basename(txt_path))}</code> и '
        f'<code>{html.escape(os.path.basename(csv_path))}</code>.'
        f'</div>'
    )
    return body + saved, [txt_path, csv_path]


# --------------------------------------------------------------------------- #
# Интерфейс
# --------------------------------------------------------------------------- #
THEME = gr.themes.Soft(primary_hue="indigo", neutral_hue="slate")


def build_ui():
    with gr.Blocks(title="webscan") as demo:
        gr.Markdown(
            "# 🔎 webscan\n"
            "### Проверка открытых TCP-портов в небольших (локальных) сетях\n"
            "Результаты сохраняются в отдельную папку **webscan_results/** рядом "
            "со скриптом — в **.txt** (по IP) и **.csv**. "
            "⚠️ Сканируй только то, что тебе принадлежит или на что есть явное разрешение."
        )

        with gr.Row(equal_height=True):
            with gr.Column(scale=2):
                targets = gr.Textbox(
                    label="Цели",
                    value="192.168.1.0-10",
                    lines=4,
                    placeholder=(
                        "Один или несколько через пробел/перенос строки:\n"
                        "192.168.1.0-10\n"
                        "192.168.1.0/28  10.0.0.5  camera.local"
                    ),
                    info="Форматы: адрес, диапазон 192.168.1.0-10, "
                         "полный 192.168.1.5-192.168.1.12, CIDR 192.168.1.0/28.",
                )
            with gr.Column(scale=1):
                ports = gr.Textbox(
                    label="Порты",
                    value="80,554,8000,8080",
                    info="Через запятую или диапазоном: 80,8000-8010. 1–65535.",
                )
                timeout = gr.Slider(
                    minimum=0.2, maximum=5.0, value=1.0, step=0.1,
                    label="Таймаут на порт, сек",
                )
                save_mode = gr.Radio(
                    choices=[FILTER_ALL, FILTER_OPEN, FILTER_CLOSED],
                    value=FILTER_ALL,
                    label="Что сохранять в файлы",
                )

        scan_btn = gr.Button("🚀  Сканировать", variant="primary", size="lg")

        results = gr.HTML(
            value='<div style="opacity:.55;padding:24px 4px;">'
                  'Задай цели и нажми «Сканировать» — здесь появятся результаты.</div>'
        )
        files = gr.File(label="Скачать результаты (.txt и .csv)", file_count="multiple")

        scan_btn.click(
            fn=run_scan,
            inputs=[targets, ports, timeout, save_mode],
            outputs=[results, files],
        )

    return demo


if __name__ == "__main__":
    # share=False — доступ только с этого компьютера (localhost).
    build_ui().launch(theme=THEME)
