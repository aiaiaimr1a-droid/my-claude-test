#!/usr/bin/env python3
"""
webscan.py — ядро сканера TCP-портов для небольших (локальных) диапазонов.

Здесь живёт вся логика, которую переиспользует webscan_gui.py:
    expand_targets(raw_targets) -> list[str]   разбор целей в список хостов
    parse_ports(text)           -> list[int]   разбор списка портов
    scan_port(host, port, t)    -> (port, bool) проверка одного TCP-порта

Рассчитано на небольшие объёмы (локальные сети): намеренно простое и
последовательное по хостам, без тяжёлой многопоточности, чтобы не зависало.

Можно запускать и из консоли:
    python webscan.py 192.168.1.0-10 -p 80,8080 -t 1.0

⚠️  Сканируй только то, что тебе принадлежит или на что есть явное разрешение.
"""

import argparse
import ipaddress
import socket

# Защита от случайного огромного диапазона — инструмент для небольших сетей.
MAX_HOSTS = 1024


# --------------------------------------------------------------------------- #
# Разбор портов
# --------------------------------------------------------------------------- #
def parse_ports(text):
    """'80, 8000-8010, 8080' -> [80, 8000, ..., 8010, 8080] (без дублей, по порядку).

    Поддерживает отдельные порты и диапазоны «начало-конец».
    Бросает ValueError с понятным сообщением при любой ошибке.
    """
    if text is None:
        raise ValueError("список портов пуст")

    ports = []
    seen = set()
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo_s, _, hi_s = chunk.partition("-")
            lo, hi = _port_int(lo_s), _port_int(hi_s)
            if lo > hi:
                raise ValueError(f"диапазон портов задом наперёд: «{chunk}»")
            rng = range(lo, hi + 1)
        else:
            rng = (_port_int(chunk),)
        for p in rng:
            if p not in seen:
                seen.add(p)
                ports.append(p)

    if not ports:
        raise ValueError("не найдено ни одного порта")
    return ports


def _port_int(value):
    value = value.strip()
    if not value.isdigit():
        raise ValueError(f"порт должен быть числом: «{value}»")
    port = int(value)
    if not (1 <= port <= 65535):
        raise ValueError(f"порт вне диапазона 1–65535: {port}")
    return port


# --------------------------------------------------------------------------- #
# Разбор целей
# --------------------------------------------------------------------------- #
def expand_targets(raw_targets):
    """Превращает список строк-целей в плоский список хостов (без дублей).

    Поддерживаемые форматы для каждого элемента:
        192.168.1.5            одиночный IP
        example.com            имя хоста (проверяется на сканировании)
        192.168.1.0-10         короткий диапазон по последнему октету
        192.168.1.5-192.168.1.12   полный диапазон IP
        192.168.1.0/28         подсеть CIDR
    """
    hosts = []
    seen = set()

    def add(host):
        if host not in seen:
            seen.add(host)
            hosts.append(host)
        if len(hosts) > MAX_HOSTS:
            raise ValueError(
                f"слишком большой диапазон (> {MAX_HOSTS} адресов). "
                f"Это инструмент для небольших/локальных сетей — сузьте цель."
            )

    for raw in raw_targets:
        target = raw.strip()
        if not target:
            continue

        # CIDR: 192.168.1.0/28
        if "/" in target:
            for ip in _expand_cidr(target):
                add(ip)
            continue

        # Диапазон: 192.168.1.5-192.168.1.12  или  192.168.1.0-10
        if "-" in target:
            for ip in _expand_range(target):
                add(ip)
            continue

        # Одиночный IP или имя хоста.
        add(target)

    if not hosts:
        raise ValueError("не задано ни одной цели")
    return hosts


def _expand_cidr(target):
    try:
        net = ipaddress.ip_network(target, strict=False)
    except ValueError:
        raise ValueError(f"некорректная подсеть CIDR: «{target}»")
    # Для /31 и /32 берём все адреса, иначе — только хосты (без сети/бродкаста).
    addrs = net.hosts() if net.num_addresses > 2 else net
    return [str(ip) for ip in addrs]


def _expand_range(target):
    lo_s, _, hi_s = target.partition("-")
    lo_s, hi_s = lo_s.strip(), hi_s.strip()

    try:
        lo = ipaddress.ip_address(lo_s)
    except ValueError:
        raise ValueError(f"некорректный адрес в диапазоне: «{lo_s}»")

    # Короткая форма: 192.168.1.0-10  ->  конец = последний октет = 10.
    if "." not in hi_s and ":" not in hi_s:
        if not hi_s.isdigit():
            raise ValueError(f"некорректный конец диапазона: «{hi_s}»")
        prefix = lo_s.rsplit(".", 1)[0]
        hi_s = f"{prefix}.{hi_s}"

    try:
        hi = ipaddress.ip_address(hi_s)
    except ValueError:
        raise ValueError(f"некорректный адрес в диапазоне: «{hi_s}»")

    if int(hi) < int(lo):
        raise ValueError(f"диапазон задом наперёд: «{target}»")

    return [str(ipaddress.ip_address(n)) for n in range(int(lo), int(hi) + 1)]


# --------------------------------------------------------------------------- #
# Проверка одного порта
# --------------------------------------------------------------------------- #
def scan_port(host, port, timeout=1.0):
    """Пробует подключиться к host:port. Возвращает (port, is_open)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return port, True
    except (OSError, socket.timeout, ValueError):
        return port, False


# --------------------------------------------------------------------------- #
# Небольшой CLI (необязательный)
# --------------------------------------------------------------------------- #
def _main():
    ap = argparse.ArgumentParser(description="Сканер TCP-портов для небольших сетей.")
    ap.add_argument("targets", nargs="+", help="IP, имя, диапазон или CIDR")
    ap.add_argument("-p", "--ports", default="80,8080", help="порты: 80,8000-8010")
    ap.add_argument("-t", "--timeout", type=float, default=1.0, help="таймаут, сек")
    args = ap.parse_args()

    hosts = expand_targets(args.targets)
    ports = parse_ports(args.ports)
    for host in hosts:
        try:
            socket.gethostbyname(host)
        except socket.gaierror:
            print(f"{host}: имя не разрешилось")
            continue
        opens = [p for p in ports if scan_port(host, p, args.timeout)[1]]
        print(f"{host}: {', '.join(map(str, opens)) if opens else 'нет открытых'}")


if __name__ == "__main__":
    _main()
