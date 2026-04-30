"""Fantasy lottery Telegram bot — single-process entrypoint.

Run:
    LOTTO_BOT_TOKEN=... LOTTO_ADMINS=123456,789012 uv run python bot.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import random
from html import escape
from typing import Sequence
from zoneinfo import ZoneInfo

import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    User,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import db
import scoring
from lotteries import BY_KEY, LOTTERIES, Lottery
from scraper import Draw, fetch_recent_draws

# ---------- Config ----------
TOKEN = os.environ["LOTTO_BOT_TOKEN"]
ADMINS: set[int] = {
    int(x) for x in os.environ.get("LOTTO_ADMINS", "").split(",") if x.strip()
}
TZ = ZoneInfo(os.environ.get("LOTTO_TZ", "America/New_York"))
DAILY_HOUR = int(os.environ.get("LOTTO_DAILY_HOUR", "9"))

POOL_MIN, POOL_MAX = 1, 70
ROSTER_SIZE = 6
MIN_PLAYERS, MAX_PLAYERS = 2, 8  # PLAN says 5-8; 2 allowed for testing

log = logging.getLogger("lotto_bot")


# ---------- Helpers ----------

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


def snake_pick_order(order: list[int], rounds: int = ROSTER_SIZE) -> list[int]:
    picks: list[int] = []
    for r in range(rounds):
        picks.extend(order if r % 2 == 0 else list(reversed(order)))
    return picks


def parse_numbers(s: str) -> list[int]:
    out: list[int] = []
    for part in s.replace(",", " ").split():
        n = int(part)
        if not POOL_MIN <= n <= POOL_MAX:
            raise ValueError(f"{n} outside {POOL_MIN}..{POOL_MAX}")
        out.append(n)
    return out


def in_lock_window() -> bool:
    lg = db.get_league()
    start = dt.time.fromisoformat(lg["lock_start"])
    end = dt.time.fromisoformat(lg["lock_end"])
    now = dt.datetime.now(TZ).time()
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def mention(name: str, tg_user_id: int) -> str:
    return f'<a href="tg://user?id={tg_user_id}">{escape(name)}</a>'


def fmt_points(p: float) -> str:
    if abs(p - round(p)) < 1e-9:
        return f"{int(round(p)):,}"
    return f"{p:,.2f}"


async def reply(update: Update, text: str, **kwargs) -> None:
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, **kwargs)


def require_season_state(expected: str):
    async def wrap(update: Update, _ctx):
        lg = db.get_league()
        if lg["state"] != expected:
            await reply(update, f"Command not available in state <b>{lg['state']}</b>.")
            return False
        return True
    return wrap


# ---------- Admin / setup commands ----------

async def cmd_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await reply(update, "Only an admin can run /setup.")
        return
    chat = update.effective_chat
    db.set_group_chat(chat.id)
    await reply(
        update,
        f"League chat set to <b>{escape(chat.title or str(chat.id))}</b>. "
        "Players can now /join, then an admin runs /startdraft.",
    )


async def cmd_setlock(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await reply(update, "Only an admin can set the lock window.")
        return
    if len(ctx.args) != 2:
        await reply(update, "Usage: <code>/setlock HH:MM HH:MM</code>")
        return
    try:
        dt.time.fromisoformat(ctx.args[0])
        dt.time.fromisoformat(ctx.args[1])
    except ValueError:
        await reply(update, "Times must be HH:MM in 24-hour format.")
        return
    db.set_lock_window(ctx.args[0], ctx.args[1])
    await reply(update, f"Lock window set to {ctx.args[0]}–{ctx.args[1]} {TZ.key}.")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lg = db.get_league()
    players = db.list_players()
    lines = [
        f"<b>State:</b> {lg['state']}",
        f"<b>Players:</b> {len(players)}",
        f"<b>Lock window:</b> {lg['lock_start']}–{lg['lock_end']} {TZ.key}",
        f"<b>In lock now:</b> {in_lock_window()}",
    ]
    if lg["state"] == "drafting":
        order = json.loads(lg["draft_order"])
        lines.append(f"<b>Draft pick index:</b> {lg['draft_pick_index']}/{len(order)*ROSTER_SIZE}")
    await reply(update, "\n".join(lines))


# ---------- Player commands ----------

async def cmd_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lg = db.get_league()
    if lg["state"] != "pre-draft":
        await reply(update, "League is past pre-draft; /join is closed.")
        return
    user: User = update.effective_user
    if db.get_player_by_tg(user.id):
        await reply(update, "You're already in.")
        return
    if len(db.list_players()) >= MAX_PLAYERS:
        await reply(update, f"League full ({MAX_PLAYERS} max).")
        return
    name = " ".join(ctx.args) if ctx.args else (user.full_name or user.username or "Player")
    db.add_player(user.id, name)
    await reply(update, f"{mention(name, user.id)} joined. ({len(db.list_players())} players so far)")


async def cmd_players(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    players = db.list_players()
    if not players:
        await reply(update, "No players yet. /join to be first.")
        return
    lines = ["<b>Players:</b>"]
    for i, p in enumerate(players, 1):
        lines.append(f"  {i}. {mention(p['name'], p['tg_user_id'])}")
    await reply(update, "\n".join(lines))


async def cmd_startdraft(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await reply(update, "Only an admin can start the draft.")
        return
    lg = db.get_league()
    if lg["state"] != "pre-draft":
        await reply(update, f"Can't start draft from state <b>{lg['state']}</b>.")
        return
    players = db.list_players()
    if len(players) < MIN_PLAYERS:
        await reply(update, f"Need at least {MIN_PLAYERS} players.")
        return
    order = [p["id"] for p in players]
    random.shuffle(order)
    db.start_draft(order)
    by_id = {p["id"]: p for p in players}
    lines = ["🎲 <b>Draft started.</b> Snake order:"]
    for i, pid in enumerate(order, 1):
        lines.append(f"  {i}. {mention(by_id[pid]['name'], by_id[pid]['tg_user_id'])}")
    full = snake_pick_order(order)
    first = by_id[full[0]]
    lines.append(f"\nOn the clock: {mention(first['name'], first['tg_user_id'])} — /pick &lt;n&gt;")
    await reply(update, "\n".join(lines))


async def cmd_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lg = db.get_league()
    if lg["state"] != "drafting":
        await reply(update, "Not in drafting state.")
        return
    if len(ctx.args) != 1:
        await reply(update, "Usage: <code>/pick &lt;number&gt;</code>")
        return
    try:
        n = int(ctx.args[0])
    except ValueError:
        await reply(update, "Pick must be an integer.")
        return
    if not POOL_MIN <= n <= POOL_MAX:
        await reply(update, f"Number must be {POOL_MIN}–{POOL_MAX}.")
        return
    user = update.effective_user
    me = db.get_player_by_tg(user.id)
    if not me:
        await reply(update, "You're not in the league.")
        return
    order = json.loads(lg["draft_order"])
    full = snake_pick_order(order)
    idx = lg["draft_pick_index"]
    if idx >= len(full):
        await reply(update, "Draft already complete.")
        return
    if full[idx] != me["id"]:
        current_pid = full[idx]
        current = db.get_player(current_pid)
        await reply(update, f"Not your turn — it's {mention(current['name'], current['tg_user_id'])}.")
        return
    if db.owner_of(n) is not None:
        await reply(update, f"{n} is already taken.")
        return
    db.record_pick(me["id"], n)
    # Advance / announce
    new_idx = idx + 1
    if new_idx >= len(full):
        db.finish_draft()
        await reply(update, f"{mention(me['name'], user.id)} picks <b>{n}</b>.\n\n🏁 Draft complete — season is live.")
        return
    next_pid = full[new_idx]
    nxt = db.get_player(next_pid)
    await reply(
        update,
        f"{mention(me['name'], user.id)} picks <b>{n}</b>.\n"
        f"On the clock: {mention(nxt['name'], nxt['tg_user_id'])}",
    )


async def cmd_draft(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lg = db.get_league()
    if lg["state"] != "drafting":
        await reply(update, f"State: <b>{lg['state']}</b>")
        return
    order = json.loads(lg["draft_order"])
    full = snake_pick_order(order)
    idx = lg["draft_pick_index"]
    current = db.get_player(full[idx])
    total = len(full)
    round_num = idx // len(order) + 1
    lines = [
        f"<b>Draft round {round_num}/{ROSTER_SIZE}</b> — pick {idx+1}/{total}",
        f"On the clock: {mention(current['name'], current['tg_user_id'])}",
    ]
    # Upcoming 5
    upcoming = [db.get_player(p) for p in full[idx+1:idx+6]]
    if upcoming:
        lines.append("Up next: " + ", ".join(escape(p["name"]) for p in upcoming))
    await reply(update, "\n".join(lines))


def number_score_breakdown() -> tuple[dict[int, float], dict[tuple[int, int], float]]:
    """Attribute points to individual numbers.

    Splits each tier's main-ball points equally across the numbers that
    matched in that draw. Bonus contribution goes to the bonus number.
    Returns (by_number, by_player_number) — the latter is per scoring player,
    so points stay with whoever owned the number at score time.
    """
    by_number: dict[int, float] = {}
    by_player_number: dict[tuple[int, int], float] = {}
    for r in db.all_scores_with_draw():
        mult = BY_KEY[r["lottery_key"]].multiplier
        mains: list[int] = json.loads(r["main_matches"])
        bonus_pts = (scoring.BONUS_POINTS * mult) if r["bonus_match"] else 0.0
        main_pts = r["points"] - bonus_pts
        if mains:
            share = main_pts / len(mains)
            for n in mains:
                by_number[n] = by_number.get(n, 0.0) + share
                by_player_number[(r["player_id"], n)] = (
                    by_player_number.get((r["player_id"], n), 0.0) + share
                )
        if r["bonus_match"] and r["draw_bonus"] is not None:
            n = r["draw_bonus"]
            by_number[n] = by_number.get(n, 0.0) + bonus_pts
            by_player_number[(r["player_id"], n)] = (
                by_player_number.get((r["player_id"], n), 0.0) + bonus_pts
            )
    return by_number, by_player_number


async def cmd_roster(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    rosters = db.all_rosters()
    players = {p["id"]: p for p in db.list_players()}
    _, by_pn = number_score_breakdown()
    if not rosters:
        await reply(update, "No rosters yet.")
        return
    lines: list[str] = []
    for pid, p in players.items():
        if lines:
            lines.append("")
        lines.append(f"<b>{escape(p['name'])}</b>")
        nums = rosters.get(pid, [])
        if not nums:
            lines.append("  (none)")
            continue
        for n in nums:
            pts = by_pn.get((pid, n), 0.0)
            lines.append(f"  <code>{n:>2}</code> — {fmt_points(pts)}")
    await reply(update, "\n".join(lines))


async def cmd_topscorers(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    by_number, _ = number_score_breakdown()
    if not by_number:
        await reply(update, "No scores yet.")
        return
    items = sorted(by_number.items(), key=lambda kv: -kv[1])
    lines = ["<b>Top scoring numbers:</b>"]
    for n, pts in items[:10]:
        owner_pid = db.owner_of(n)
        owner_str = ""
        if owner_pid is not None:
            owner = db.get_player(owner_pid)
            if owner:
                owner_str = f" — {escape(owner['name'])}"
        lines.append(f"  {n}: {fmt_points(pts)}{owner_str}")
    await reply(update, "\n".join(lines))


async def cmd_freeagents(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    fa = db.free_agents(POOL_MIN, POOL_MAX)
    if not fa:
        await reply(update, "No free agents.")
        return
    await reply(update, "<b>Free agents:</b> " + ", ".join(str(n) for n in fa))


async def cmd_standings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.standings()
    if not rows:
        await reply(update, "No players yet.")
        return
    lines = ["<b>Standings:</b>"]
    for i, (_pid, name, total) in enumerate(rows, 1):
        lines.append(f"  {i}. {escape(name)} — {fmt_points(total)}")
    await reply(update, "\n".join(lines))


async def cmd_recent(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    key = ctx.args[0] if ctx.args else None
    if key and key not in BY_KEY:
        await reply(update, f"Unknown lottery key. Try: {', '.join(l.key for l in LOTTERIES)}")
        return
    rows = db.recent_draws(limit=10, lottery_key=key)
    if not rows:
        await reply(update, "No draws recorded yet.")
        return
    lines = ["<b>Recent draws:</b>"]
    for r in rows:
        mains = " ".join(str(n) for n in json.loads(r["mains"]))
        bonus = f" + {r['bonus']}" if r["bonus"] is not None else ""
        lt = BY_KEY[r["lottery_key"]]
        lines.append(f"  {r['draw_date']} {lt.name}: {mains}{bonus}")
    await reply(update, "\n".join(lines))


async def cmd_swap(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if in_lock_window():
        await reply(update, "Roster is locked right now.")
        return
    lg = db.get_league()
    if lg["state"] != "in-season":
        await reply(update, "Swaps are only allowed in-season.")
        return
    if len(ctx.args) != 2:
        await reply(update, "Usage: <code>/swap &lt;drop&gt; &lt;add&gt;</code>")
        return
    me = db.get_player_by_tg(update.effective_user.id)
    if not me:
        await reply(update, "You're not in the league.")
        return
    try:
        drop, add = int(ctx.args[0]), int(ctx.args[1])
    except ValueError:
        await reply(update, "Numbers must be integers.")
        return
    if not (POOL_MIN <= drop <= POOL_MAX and POOL_MIN <= add <= POOL_MAX):
        await reply(update, f"Numbers must be {POOL_MIN}–{POOL_MAX}.")
        return
    if drop == add:
        await reply(update, "Drop and add must differ.")
        return
    err = db.swap_numbers(me["id"], drop, add)
    if err:
        await reply(update, err)
        return
    await reply(update, f"{mention(me['name'], update.effective_user.id)}: dropped <b>{drop}</b>, added <b>{add}</b>.")


async def cmd_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if in_lock_window():
        await reply(update, "Roster is locked right now.")
        return
    lg = db.get_league()
    if lg["state"] != "in-season":
        await reply(update, "Trades are only allowed in-season.")
        return
    msg = update.effective_message
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await reply(update, "Reply to the other player's message with: <code>/trade &lt;give&gt; &lt;get&gt;</code>")
        return
    if len(ctx.args) != 2:
        await reply(update, "Usage: <code>/trade &lt;give_nums&gt; &lt;get_nums&gt;</code> (comma-separated)")
        return
    me = db.get_player_by_tg(update.effective_user.id)
    target_user = msg.reply_to_message.from_user
    target = db.get_player_by_tg(target_user.id)
    if not me or not target:
        await reply(update, "Both users must be league players.")
        return
    if me["id"] == target["id"]:
        await reply(update, "Can't trade with yourself.")
        return
    try:
        give = parse_numbers(ctx.args[0])
        get = parse_numbers(ctx.args[1])
    except ValueError as e:
        await reply(update, f"Bad numbers: {e}")
        return
    if not give or not get:
        await reply(update, "Must give and get at least one number each.")
        return
    if len(give) != len(get):
        await reply(update, "Trade must be balanced (|give| == |get|) to keep 6-number rosters.")
        return
    if set(give) & set(get):
        await reply(update, "Same number on both sides of the trade.")
        return
    my_roster = set(db.roster_for(me["id"]))
    their_roster = set(db.roster_for(target["id"]))
    bad_give = [n for n in give if n not in my_roster]
    bad_get = [n for n in get if n not in their_roster]
    if bad_give:
        await reply(update, f"You don't own: {bad_give}")
        return
    if bad_get:
        await reply(update, f"They don't own: {bad_get}")
        return
    trade_id = db.propose_trade(me["id"], target["id"], give, get)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"trade:accept:{trade_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"trade:reject:{trade_id}"),
    ]])
    text = (
        f"<b>Trade #{trade_id}</b>\n"
        f"{mention(me['name'], update.effective_user.id)} offers {give} "
        f"for {mention(target['name'], target_user.id)}'s {get}.\n\n"
        f"{mention(target['name'], target_user.id)} — accept?"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    me = db.get_player_by_tg(update.effective_user.id)
    pending = db.list_pending_trades()
    if me:
        pending = [t for t in pending if me["id"] in (t["proposer_id"], t["target_id"])]
    if not pending:
        await reply(update, "No pending trades.")
        return
    lines = ["<b>Pending trades:</b>"]
    for t in pending:
        p = db.get_player(t["proposer_id"])
        q = db.get_player(t["target_id"])
        give = json.loads(t["give_nums"])
        get = json.loads(t["get_nums"])
        lines.append(f"  #{t['id']}: {escape(p['name'])} {give} ↔ {escape(q['name'])} {get}")
    await reply(update, "\n".join(lines))


async def cmd_cancel_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if len(ctx.args) != 1:
        await reply(update, "Usage: <code>/cancel_trade &lt;id&gt;</code>")
        return
    try:
        trade_id = int(ctx.args[0])
    except ValueError:
        await reply(update, "Trade id must be an integer.")
        return
    t = db.get_trade(trade_id)
    if not t or t["status"] != "pending":
        await reply(update, "No such pending trade.")
        return
    me = db.get_player_by_tg(update.effective_user.id)
    if not me or me["id"] != t["proposer_id"]:
        await reply(update, "Only the proposer can cancel.")
        return
    db.resolve_trade(trade_id, "cancelled")
    await reply(update, f"Trade #{trade_id} cancelled.")


async def on_trade_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    try:
        _, action, raw_id = q.data.split(":")
        trade_id = int(raw_id)
    except (ValueError, AttributeError):
        return
    t = db.get_trade(trade_id)
    if not t or t["status"] != "pending":
        await q.edit_message_text("Trade no longer pending.")
        return
    clicker = db.get_player_by_tg(q.from_user.id)
    if not clicker or clicker["id"] != t["target_id"]:
        await q.answer("Only the trade target can respond.", show_alert=True)
        return
    if in_lock_window():
        await q.answer("Roster is locked right now.", show_alert=True)
        return

    if action == "reject":
        db.resolve_trade(trade_id, "rejected")
        await q.edit_message_text(f"Trade #{trade_id} rejected.")
        return

    if action == "accept":
        err = db.execute_trade_atomic(t)
        if err:
            db.resolve_trade(trade_id, "rejected")
            await q.edit_message_text(f"Trade #{trade_id} failed: {err}")
            return
        db.resolve_trade(trade_id, "accepted")
        proposer = db.get_player(t["proposer_id"])
        target = db.get_player(t["target_id"])
        give = json.loads(t["give_nums"])
        get = json.loads(t["get_nums"])
        await q.edit_message_text(
            f"✅ Trade #{trade_id} done: "
            f"{escape(proposer['name'])} gets {get}, "
            f"{escape(target['name'])} gets {give}."
        )


# ---------- Daily scrape + score + post ----------

async def daily_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lg = db.get_league()
    chat_id = lg["group_chat_id"]
    if chat_id is None:
        log.warning("daily_tick: no group chat set, skipping post")
        return
    if lg["state"] != "in-season":
        log.info("daily_tick: league not in-season (%s), skipping", lg["state"])
        return

    new_draw_ids: list[tuple[Lottery, int, Draw]] = []
    owners = db.owner_map()
    season_start = dt.date.fromisoformat((lg["season_started_at"] or "1970-01-01")[:10])

    with httpx.Client(headers={"User-Agent": "lotto-land-bot/0.1"}, timeout=20) as client:
        for lottery in LOTTERIES:
            try:
                draws = fetch_recent_draws(lottery, client=client)
            except Exception as exc:
                log.warning("scrape failed %s: %s", lottery.key, exc)
                continue
            for d in draws:
                if d.draw_date < season_start:
                    continue
                draw_id = db.record_draw(d.lottery_key, d.draw_date, d.mains, d.bonus)
                if draw_id is None:
                    continue  # already recorded
                new_draw_ids.append((lottery, draw_id, d))

    if not new_draw_ids:
        await ctx.bot.send_message(chat_id=chat_id, text="No new draws in the last scan.")
        return

    players_by_id = {p["id"]: p for p in db.list_players()}
    blocks: list[str] = []
    new_draw_ids.sort(key=lambda t: (t[2].draw_date, t[0].key))

    for lottery, draw_id, d in new_draw_ids:
        mains = " ".join(str(n) for n in d.mains)
        bonus_str = f" + <b>{d.bonus}</b>" if d.bonus is not None else ""
        mult = f" ({lottery.multiplier}×)" if lottery.multiplier != 1 else ""
        block = [f"<b>{escape(lottery.name)}</b>{mult} — {d.draw_date}",
                 f"  {mains}{bonus_str}"]

        player_scores = scoring.score_draw(lottery, d, owners)
        if player_scores:
            for ps in sorted(player_scores, key=lambda s: -s.points):
                db.record_score(draw_id, ps.player_id, ps.points, ps.main_matches, ps.bonus_match)
                p = players_by_id.get(ps.player_id)
                if not p:
                    continue
                detail = ",".join(str(n) for n in ps.main_matches)
                if ps.bonus_match:
                    detail = (detail + "+B") if detail else "B"
                block.append(f"  • {escape(p['name'])}: +{fmt_points(ps.points)} ({detail})")
        else:
            block.append("  • no matches")
        blocks.append("\n".join(block))

    # Standings
    stand = db.standings()
    stand_lines = ["<b>Standings:</b>"]
    for i, (_pid, name, total) in enumerate(stand, 1):
        stand_lines.append(f"  {i}. {escape(name)} — {fmt_points(total)}")

    text = "🎰 <b>Daily summary</b>\n\n" + "\n\n".join(blocks) + "\n\n" + "\n".join(stand_lines)
    # Telegram has a 4096-char message limit; chunk if needed.
    for chunk in _chunks(text, 3800):
        await ctx.bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.HTML)


def _chunks(text: str, n: int) -> list[str]:
    if len(text) <= n:
        return [text]
    out, buf = [], []
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > n and buf:
            out.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        out.append("\n".join(buf))
    return out


async def cmd_end_season(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await reply(update, "Only an admin can end the season.")
        return
    lg = db.get_league()
    if lg["state"] == "ended":
        await reply(update, "Season already ended.")
        return
    if lg["state"] != "in-season":
        await reply(update, f"Can't end the season from state <b>{lg['state']}</b>.")
        return
    if not ctx.args or ctx.args[0].lower() != "confirm":
        await reply(
            update,
            "This ends the season, locks all rosters, cancels pending trades, "
            "and crowns a winner. Run <code>/end_season confirm</code> to proceed.",
        )
        return

    stand = db.standings()
    winner_pid: int | None = None
    winner_name = "—"
    winner_total = 0.0
    tied: list[tuple[int, str, float]] = []
    if stand:
        top_total = stand[0][2]
        tied = [row for row in stand if row[2] == top_total]
        if len(tied) == 1:
            winner_pid, winner_name, winner_total = tied[0]

    db.end_season(winner_pid)

    lines = ["🏁 <b>Season ended.</b>", ""]
    if winner_pid is not None:
        winner = db.get_player(winner_pid)
        lines.append(
            f"🏆 <b>Winner:</b> {mention(winner_name, winner['tg_user_id'])} — {fmt_points(winner_total)}"
        )
    elif tied:
        names = ", ".join(escape(n) for _pid, n, _t in tied)
        lines.append(f"🤝 <b>Tie</b> at {fmt_points(tied[0][2])}: {names}")
    else:
        lines.append("No scores recorded — no winner.")

    lines.append("\n<b>Final standings:</b>")
    for i, (_pid, name, total) in enumerate(stand, 1):
        lines.append(f"  {i}. {escape(name)} — {fmt_points(total)}")
    await reply(update, "\n".join(lines))


async def cmd_runscore_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: trigger the daily job immediately (useful for debugging)."""
    if not is_admin(update.effective_user.id):
        await reply(update, "Admins only.")
        return
    await reply(update, "Running scrape + score now...")
    await daily_tick(ctx)


HELP_PLAYER = [
    ("/join [name]", "Join the league (pre-draft only)"),
    ("/players", "List players"),
    ("/pick &lt;n&gt;", "Draft a number on your turn"),
    ("/draft", "Show draft progress and who's on the clock"),
    ("/roster", "Show everyone's numbers"),
    ("/freeagents", "List unowned numbers"),
    ("/standings", "Show current standings"),
    ("/topscorers", "Top-scoring numbers since season start"),
    ("/recent [key]", "Last 10 draws (optionally for one lottery)"),
    ("/swap &lt;drop&gt; &lt;add&gt;", "Drop one of your numbers, pick up a free agent"),
    ("/trade &lt;give&gt; &lt;get&gt;", "Reply to a player to propose a trade"),
    ("/trades", "List your pending trades"),
    ("/cancel_trade &lt;id&gt;", "Cancel a trade you proposed"),
    ("/status", "League state, player count, lock window"),
    ("/help", "Show this message"),
]

HELP_ADMIN = [
    ("/setup", "Bind current group chat as the league chat"),
    ("/startdraft", "Randomize order and begin the snake draft"),
    ("/setlock HH:MM HH:MM", "Change the roster-lock window"),
    ("/runscore_now", "Trigger the scrape+score+post job immediately"),
    ("/end_season confirm", "End the season and crown a winner"),
]


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["<b>Commands:</b>"]
    for cmd, desc in HELP_PLAYER:
        lines.append(f"  <code>{cmd}</code> — {desc}")
    if is_admin(update.effective_user.id):
        lines.append("\n<b>Admin:</b>")
        for cmd, desc in HELP_ADMIN:
            lines.append(f"  <code>{cmd}</code> — {desc}")
    await reply(update, "\n".join(lines))


# ---------- Entry point ----------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_schema()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("setlock", cmd_setlock))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CommandHandler("players", cmd_players))
    app.add_handler(CommandHandler("startdraft", cmd_startdraft))
    app.add_handler(CommandHandler("pick", cmd_pick))
    app.add_handler(CommandHandler("draft", cmd_draft))
    app.add_handler(CommandHandler("roster", cmd_roster))
    app.add_handler(CommandHandler("freeagents", cmd_freeagents))
    app.add_handler(CommandHandler("standings", cmd_standings))
    app.add_handler(CommandHandler("topscorers", cmd_topscorers))
    app.add_handler(CommandHandler("recent", cmd_recent))
    app.add_handler(CommandHandler("swap", cmd_swap))
    app.add_handler(CommandHandler("trade", cmd_trade))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("cancel_trade", cmd_cancel_trade))
    app.add_handler(CommandHandler("end_season", cmd_end_season))
    app.add_handler(CommandHandler("runscore_now", cmd_runscore_now))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CallbackQueryHandler(on_trade_callback, pattern=r"^trade:"))

    app.job_queue.run_daily(
        daily_tick,
        time=dt.time(hour=DAILY_HOUR, tzinfo=TZ),
        name="daily_tick",
    )

    log.info("lotto-land-tg-bot starting; admins=%s, tz=%s", ADMINS, TZ.key)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
