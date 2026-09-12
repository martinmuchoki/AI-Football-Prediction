from __future__ import annotations

import re
import unicodedata


def normalize_team_name(value: str) -> str:
    """Normalize club names for cross-provider identity matching.

    This is intentionally conservative. Aliases are explicit rather than fuzzy so
    a bad approximate match cannot silently merge two different clubs.
    """
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    tokens = [t for t in text.split() if t not in {"fc", "afc", "cf"}]
    compact = " ".join(tokens)
    aliases = {
        "manchester utd": "manchester united",
        "man utd": "manchester united",
        "man city": "manchester city",
        "spurs": "tottenham hotspur",
        "brighton": "brighton and hove albion",
        "coventry": "coventry city",
        "ipswich": "ipswich town",
        "leeds": "leeds united",
        "tottenham": "tottenham hotspur",
        "brighton hove albion": "brighton and hove albion",
        "brighton and hove albion": "brighton and hove albion",
        "nottm forest": "nottingham forest",
        "newcastle": "newcastle united",
        "wolves": "wolverhampton wanderers",
    }
    return aliases.get(compact, compact)


def canonical_fixture_key(*, competition: str, season: int, home: str, away: str) -> str:
    return "|".join(
        [
            competition.strip().lower(),
            str(int(season)),
            normalize_team_name(home),
            normalize_team_name(away),
        ]
    )
