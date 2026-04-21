"""Pure scoring engine.

Rules (locked in game_rules.md):
- Tiers are exclusive (top tier only).
- Main-ball match tiers: 1:1, 2:5, 3:40, 4:800, 5:50_000, complete (all mains): 20_000_000.
- Bonus ball ("powerball"/"megaball"/etc.) from the same drafted pool adds 0.25
  flat per draw; does not combine with main tier and does not promote a tier.
- Mega Millions and Powerball apply a 5x multiplier to EVERYTHING in the draw,
  including the bonus and complete match.
"""

from __future__ import annotations

from dataclasses import dataclass

from lotteries import Lottery
from scraper import Draw

TIER_POINTS: dict[int, float] = {0: 0, 1: 1, 2: 5, 3: 40, 4: 800, 5: 50_000}
COMPLETE_MATCH = 20_000_000
BONUS_POINTS = 0.25


@dataclass(frozen=True)
class PlayerScore:
    player_id: int
    points: float
    main_matches: list[int]
    bonus_match: bool


def score_draw(
    lottery: Lottery,
    draw: Draw,
    owner_of: dict[int, int],
) -> list[PlayerScore]:
    """Compute each player's score for a single draw.

    `owner_of` maps number -> player_id. Returns one PlayerScore per player who
    scored anything non-zero.
    """
    hits: dict[int, list[int]] = {}
    bonus_hit: dict[int, bool] = {}

    for n in draw.mains:
        pid = owner_of.get(n)
        if pid is not None:
            hits.setdefault(pid, []).append(n)

    if draw.bonus is not None:
        pid = owner_of.get(draw.bonus)
        if pid is not None:
            bonus_hit[pid] = True

    out: list[PlayerScore] = []
    for pid in set(hits) | set(bonus_hit):
        matched = sorted(hits.get(pid, []))
        n_main = len(matched)
        if n_main == lottery.mains and n_main > 0:
            base = COMPLETE_MATCH
        else:
            base = TIER_POINTS.get(n_main, 0)
        extra = BONUS_POINTS if bonus_hit.get(pid) else 0
        total = (base + extra) * lottery.multiplier
        out.append(
            PlayerScore(
                player_id=pid,
                points=total,
                main_matches=matched,
                bonus_match=bonus_hit.get(pid, False),
            )
        )
    return out
