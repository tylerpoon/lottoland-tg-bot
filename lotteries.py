"""Config for the lotteries tracked by the fantasy lottery bot.

Each entry has:
  key:        short identifier used in DB / logs
  name:       display name
  slug:       lottodatabase.com URL slug
  region:     "american" or "canadian" (also a URL segment)
  mains:      number of main balls drawn
  main_max:   highest main-ball number
  bonus_max:  highest bonus-ball number, or None if this lottery has no bonus
  multiplier: point multiplier (5 for Mega Millions and Powerball; else 1)
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Lottery:
    key: str
    name: str
    slug: str
    region: str
    mains: int
    main_max: int
    bonus_max: int | None
    multiplier: int

    @property
    def details_url(self) -> str:
        return (
            f"https://www.lottodatabase.com/lotto-database/"
            f"{self.region}-lotteries/{self.slug}/details"
        )


LOTTERIES: list[Lottery] = [
    Lottery("mega_millions", "Mega Millions", "megamillions", "american", 5, 70, 24, 5),
    Lottery("powerball", "Powerball", "powerball", "american", 5, 69, 26, 5),
    Lottery("mil_for_life", "Millionaire for Life", "millionaire-for-life", "american", 5, 58, 5, 1),
    Lottery("the_pick", "The Pick", "thepick", "american", 6, 44, None, 1),
    Lottery("superlotto", "SuperLotto Plus", "superlotto-plus", "american", 5, 47, 27, 1),
    Lottery("colorado", "Colorado Lotto", "colorado-lotto", "american", 6, 40, None, 1),
    Lottery("florida", "Florida Lotto", "florida-lotto", "american", 6, 53, None, 1),
    Lottery("hoosier", "Hoosier Lotto", "hoosier-lotto", "american", 6, 46, None, 1),
    Lottery("michigan_47", "Michigan Lotto 47", "michigan-lotto-47", "american", 6, 47, None, 1),
    Lottery("pick_6", "Pick-6", "pick-6", "american", 6, 46, None, 1),
    Lottery("ny_lotto", "New York Lotto", "new-york-lotto", "american", 6, 59, 59, 1),
    Lottery("classic_ohio", "Classic Lotto", "classic-lotto", "american", 6, 49, None, 1),
    Lottery("match_6", "Match 6", "match-6", "american", 6, 49, None, 1),
    Lottery("lotto_texas", "Lotto Texas", "lotto-texas", "american", 6, 54, None, 1),
    Lottery("bank_million", "Bank A Million", "bank-a-million", "american", 6, 40, 40, 1),
    Lottery("washington", "Washington Lotto", "washingtonlotto", "american", 6, 49, None, 1),
    Lottery("daily_grand", "Daily Grand", "daily-grand", "canadian", 5, 49, 7, 1),
    Lottery("lotto_649", "Lotto 6/49", "lotto-649", "canadian", 6, 49, 49, 1),
    Lottery("atlantic_49", "Atlantic 49", "atlantic-49", "canadian", 6, 49, 49, 1),
    Lottery("bc_49", "BC/49", "bc-49", "canadian", 6, 49, 49, 1),
    Lottery("lottario", "Lottario", "lottario", "canadian", 6, 45, 45, 1),
    Lottery("ontario_49", "Ontario 49", "ontario-49", "canadian", 6, 49, 49, 1),
    Lottery("quebec_49", "Quebec 49", "quebec-49", "canadian", 6, 49, 49, 1),
    Lottery("western_649", "Western 649", "western-649", "canadian", 6, 49, 49, 1),
]

BY_KEY: dict[str, Lottery] = {l.key: l for l in LOTTERIES}
