"""Scrape recent lottery draws from lottodatabase.com.

The site renders each draw as:

    <h2>Recent Draws for <Lottery></h2>
    <div class="section group"><div class="col s_12_12">
      Saturday, April 18, 2026<br>
      <div class="extraspace">
        <span class="white ball">24</span>...<span class="white ball">61</span>
        <span class="red ball">1<br><span class="bonus">Powerball</span></span>
      </div>
    </div></div>

Lotteries without a bonus ball omit the `red ball` span entirely.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup, Tag

from lotteries import Lottery


@dataclass(frozen=True)
class Draw:
    lottery_key: str
    draw_date: dt.date
    mains: tuple[int, ...]
    bonus: int | None


def fetch_recent_draws(lottery: Lottery, *, client: httpx.Client | None = None) -> list[Draw]:
    """Return the (up to 5) most recent draws currently shown on the details page."""
    owns_client = client is None
    if owns_client:
        client = httpx.Client(headers={"User-Agent": "lotto-land-bot/0.1"}, timeout=20)
    try:
        resp = client.get(lottery.details_url)
        resp.raise_for_status()
        return _parse_details_html(lottery, resp.text)
    finally:
        if owns_client:
            client.close()


def _parse_details_html(lottery: Lottery, html: str) -> list[Draw]:
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find(
        lambda tag: tag.name == "h2" and tag.get_text(strip=True).startswith("Recent Draws")
    )
    if heading is None:
        raise ValueError(f"Could not find 'Recent Draws' section for {lottery.name}")

    draws: list[Draw] = []
    for block in heading.find_all_next("div", class_="section"):
        inner = block.find("div", class_="col")
        if inner is None:
            continue
        extra = inner.find("div", class_="extraspace")
        if extra is None:
            continue
        draw = _parse_draw_block(lottery, inner, extra)
        if draw is not None:
            draws.append(draw)
    return draws


def _parse_draw_block(lottery: Lottery, inner: Tag, extra: Tag) -> Draw | None:
    date_text = ""
    for node in inner.children:
        if isinstance(node, str):
            date_text = node.strip()
            if date_text:
                break
    if not date_text:
        return None

    try:
        draw_date = dt.datetime.strptime(date_text, "%A, %B %d, %Y").date()
    except ValueError:
        return None

    mains = tuple(int(span.get_text(strip=True)) for span in extra.select("span.white.ball"))
    if len(mains) != lottery.mains:
        return None

    bonus: int | None = None
    if lottery.bonus_max is not None:
        bonus_span = next(
            (
                s
                for s in extra.select("span.ball")
                if "white" not in (s.get("class") or [])
            ),
            None,
        )
        if bonus_span is not None:
            label = bonus_span.find("span", class_="bonus")
            if label is not None:
                label.extract()
            try:
                bonus = int(bonus_span.get_text(strip=True))
            except ValueError:
                bonus = None

    return Draw(lottery_key=lottery.key, draw_date=draw_date, mains=mains, bonus=bonus)


if __name__ == "__main__":
    import sys
    from lotteries import LOTTERIES, BY_KEY

    targets = (
        [BY_KEY[k] for k in sys.argv[1:]] if len(sys.argv) > 1 else LOTTERIES
    )
    with httpx.Client(headers={"User-Agent": "lotto-land-bot/0.1"}, timeout=20) as client:
        for lottery in targets:
            try:
                draws = fetch_recent_draws(lottery, client=client)
            except Exception as exc:
                print(f"{lottery.name}: ERROR {exc}")
                continue
            print(f"\n{lottery.name} ({lottery.key})  multiplier={lottery.multiplier}x")
            for d in draws:
                mains = " ".join(f"{n:2d}" for n in d.mains)
                extra = f"  bonus={d.bonus}" if d.bonus is not None else ""
                print(f"  {d.draw_date}  {mains}{extra}")
