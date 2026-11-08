"""Pure helpers: read kill-claim messages and price text. No Discord imports, easy to test."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# <@123> 5x   <@!123>: 5k   <@123> x5   <@123> 5 kills   <@123> 5×
_CLAIM_RE = re.compile(
    r"<@!?(?P<uid>\d{15,25})>\s*[:=\-\u2013\u2014]?\s*"
    r"(?:(?P<k1>\d{1,4})\s*(?:(?:x|k|kills?)\b|\u00d7)|(?:x|\u00d7)\s*(?P<k2>\d{1,4})\b)",
    re.IGNORECASE,
)

_TAG_RE = re.compile(r"<[^>\n]{1,60}>")          # mentions, channels, emojis, timestamps
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# 100k  1.5m  2M   (suffix price)
_SUFFIX_PRICE_RE = re.compile(r"(?<![\w.,])(?P<num>\d+(?:\.\d+)?)\s?(?P<suf>[kKmM])(?![\w])")
# 100000  1,000,000   (plain big number)
_PLAIN_PRICE_RE = re.compile(r"(?<![\w.,])(?P<num>\d{1,3}(?:,\d{3})+|\d{4,})(?![\w]|[.,]\d)")

_AMOUNT_RE = re.compile(
    r"^\s*(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<suf>[kKmM])?\s*$"
)

MAX_PRICE = 10_000_000_000
MAX_KILLS = 100_000


@dataclass
class ParsedClaim:
    """Result of reading one message."""

    entries: dict[int, int] = field(default_factory=dict)  # user_id -> kills (insertion ordered)
    price: int | None = None                               # price written inside the message

    def __bool__(self) -> bool:
        return bool(self.entries)


def parse_amount(text: str) -> int | None:
    """'50000' / '50k' / '1.5m' / '1,000,000' -> int. Returns None if it is not a price."""
    m = _AMOUNT_RE.match(text or "")
    if not m:
        return None
    num = m.group("num").replace(",", "")
    try:
        value = float(num)
    except ValueError:
        return None
    suf = (m.group("suf") or "").lower()
    if suf == "k":
        value *= 1_000
    elif suf == "m":
        value *= 1_000_000
    value = int(round(value))
    if value <= 0 or value > MAX_PRICE:
        return None
    return value


def parse_price_list(text: str) -> list[int] | None:
    """'50000,1000000,2000000' -> [50000, 1000000, 2000000]. None if any part is invalid.

    Spaces after commas are fine ('50k, 100k'). A thousands-style number such as
    '1,000,000' is NOT supported here because comma separates prices.
    """
    parts = [p.strip() for p in re.split(r"[,;]", text or "") if p.strip()]
    if not parts:
        return None
    out: list[int] = []
    for part in parts:
        value = parse_amount(part)
        if value is None:
            return None
        out.append(value)
    return out


def looks_like_price_list(text: str) -> bool:
    return parse_price_list(text) is not None


def _find_price(text: str) -> int | None:
    best: tuple[int, int] | None = None  # (position, value)
    for m in _SUFFIX_PRICE_RE.finditer(text):
        value = parse_amount(m.group("num") + m.group("suf"))
        if value is not None and (best is None or m.start() < best[0]):
            best = (m.start(), value)
    for m in _PLAIN_PRICE_RE.finditer(text):
        value = parse_amount(m.group("num"))
        if value is not None and (best is None or m.start() < best[0]):
            best = (m.start(), value)
    return best[1] if best else None


def parse_claim(content: str) -> ParsedClaim:
    """Find '@member 5x' / '@member 5k' pairs and an optional price like 100k.

    * kills = the number written right after the member tag (5x, 9k, x5, 5 kills)
    * price = any other price-looking token in the message (100k, 1.5m, 200000)
    """
    result = ParsedClaim()
    if not content:
        return result

    for m in _CLAIM_RE.finditer(content):
        kills = int(m.group("k1") or m.group("k2"))
        if kills <= 0:
            continue
        uid = int(m.group("uid"))
        result.entries[uid] = min(result.entries.get(uid, 0) + kills, MAX_KILLS)

    if not result.entries:
        return result

    rest = _CLAIM_RE.sub(" ", content)
    rest = _URL_RE.sub(" ", rest)
    rest = _TAG_RE.sub(" ", rest)
    result.price = _find_price(rest)
    return result


def resolve_price(message_price: int | None, target_default: int | None, fallback: int) -> tuple[int, str]:
    """Pick the per-kill price. Returns (price, source)."""
    if message_price:
        return message_price, "message"
    if target_default:
        return target_default, "target"
    return fallback, "default"


# "<name words> <price list>"   e.g.  alpha team 50000, 1000000
_TARGET_ARGS = re.compile(r"^(?P<name>.+?)\s+(?P<prices>\d[\dkKmM.]*(?:\s*,\s*\d[\dkKmM.]*)*)\s*$")
_BROKEN_PRICE_TAIL = re.compile(r"^\d[\w.]*,")


def split_target_args(args: str) -> tuple[str, list[int] | None, bool]:
    """Split '??target' text into (name, prices, ok).

    'alpha'                   -> ('alpha', None, True)
    'alpha team 50000'        -> ('alpha team', [50000], True)
    'alpha 50000,1000000'     -> ('alpha', [50000, 1000000], True)
    'alpha 50k,oops'          -> ok=False  (a price list was clearly meant but is invalid)
    """
    args = (args or "").strip()
    m = _TARGET_ARGS.match(args)
    if m:
        prices = parse_price_list(m.group("prices"))
        if prices is None:
            return args, None, False
        return m.group("name").strip(), prices, True
    last = args.split()[-1] if args.split() else ""
    if len(args.split()) > 1 and _BROKEN_PRICE_TAIL.match(last):
        return args, None, False
    return args, None, True
