"""Small pure formatting helpers."""
from __future__ import annotations


def money(n: int) -> str:
    return f"{int(n):,}"


def short_money(n: int) -> str:
    """50000 -> 50K, 1500000 -> 1.5M, 2000000 -> 2M"""
    n = int(n)
    if n >= 1_000_000 and n % 100_000 == 0:
        v = n / 1_000_000
        return f"{v:g}M"
    if n >= 1_000 and n % 100 == 0:
        v = n / 1_000
        return f"{v:g}K"
    return str(n)


def chunk_lines(lines: list[str], limit: int = 3800) -> list[str]:
    """Join lines into pages that each stay below `limit` characters."""
    pages: list[str] = []
    current = ""
    for line in lines:
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        if current and len(current) + len(line) + 1 > limit:
            pages.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        pages.append(current)
    return pages
