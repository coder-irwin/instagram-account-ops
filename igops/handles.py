"""Handle suggestions. Availability is checked by a person on Instagram, not by code:
there is no supported API for it, and probing profile URLs in bulk is the kind of
automated access Instagram restricts."""

from __future__ import annotations

import re

HANDLE_RE = re.compile(r"^(?!.*\.\.)(?!\.)(?!.*\.$)[a-z0-9._]{1,30}$")


def valid(handle: str) -> bool:
    return bool(HANDLE_RE.match(handle))


def suggest(brand_name: str, n: int = 6) -> list[str]:
    base = re.sub(r"[^a-z0-9]+", "", brand_name.lower())
    words = [w for w in re.split(r"[^a-z0-9]+", brand_name.lower()) if w]
    joined = ".".join(words)
    cands = [
        base,
        joined,
        f"{base}.official",
        f"{base}_hq",
        f"{base}.team",
        f"the{base}",
        f"{words[0]}.{words[-1]}" if len(words) > 1 else f"{base}.co",
        f"{base}.shop",
    ]
    out = []
    for c in cands:
        c = c[:30].strip(".")
        if valid(c) and c not in out:
            out.append(c)
    return out[:n]
