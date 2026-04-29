"""SQLite persistence for the fantasy lottery bot.

One-file schema, sync sqlite3. At ~5-8 players and one daily scoring pass the
blocking calls are negligible even inside the asyncio event loop.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterable

DB_PATH = os.environ.get("LOTTO_DB_PATH", "lotto.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS league (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    group_chat_id     INTEGER,
    state             TEXT    NOT NULL DEFAULT 'pre-draft',  -- pre-draft | drafting | in-season | ended
    draft_order       TEXT,                                   -- JSON [player_id, ...]
    draft_pick_index  INTEGER NOT NULL DEFAULT 0,
    lock_start        TEXT    NOT NULL DEFAULT '20:00',
    lock_end          TEXT    NOT NULL DEFAULT '09:30',
    season_started_at TEXT,
    season_ended_at   TEXT,
    winner_player_id  INTEGER REFERENCES players(id)
);

CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id  INTEGER UNIQUE NOT NULL,
    name        TEXT    NOT NULL,
    joined_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS rosters (
    number      INTEGER PRIMARY KEY CHECK (number BETWEEN 1 AND 70),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    acquired_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_picks (
    pick_index INTEGER PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id),
    number     INTEGER NOT NULL,
    picked_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS draws (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lottery_key TEXT    NOT NULL,
    draw_date   TEXT    NOT NULL,
    mains       TEXT    NOT NULL,   -- JSON [int, ...]
    bonus       INTEGER,
    scraped_at  TEXT    NOT NULL,
    UNIQUE(lottery_key, draw_date)
);

CREATE TABLE IF NOT EXISTS scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    draw_id       INTEGER NOT NULL REFERENCES draws(id),
    player_id     INTEGER NOT NULL REFERENCES players(id),
    points        REAL    NOT NULL,
    main_matches  TEXT    NOT NULL,   -- JSON [int, ...]
    bonus_match   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(draw_id, player_id)
);

CREATE TABLE IF NOT EXISTS pending_trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    proposer_id  INTEGER NOT NULL REFERENCES players(id),
    target_id    INTEGER NOT NULL REFERENCES players(id),
    give_nums    TEXT    NOT NULL,    -- JSON [int, ...] proposer -> target
    get_nums     TEXT    NOT NULL,    -- JSON [int, ...] target -> proposer
    status       TEXT    NOT NULL DEFAULT 'pending',  -- pending | accepted | rejected | cancelled
    created_at   TEXT    NOT NULL,
    resolved_at  TEXT
);
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO league(id) VALUES (1)")
        # Idempotent additions for DBs created before these columns existed.
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(league)").fetchall()}
        for col, decl in (
            ("season_ended_at", "TEXT"),
            ("winner_player_id", "INTEGER REFERENCES players(id)"),
        ):
            if col not in existing:
                conn.execute(f"ALTER TABLE league ADD COLUMN {col} {decl}")


# ---------- League ----------

def get_league() -> sqlite3.Row:
    with tx() as conn:
        row = conn.execute("SELECT * FROM league WHERE id = 1").fetchone()
    if row is None:
        raise RuntimeError("League row missing — call init_schema() first")
    return row


def set_group_chat(chat_id: int) -> None:
    with tx() as conn:
        conn.execute("UPDATE league SET group_chat_id = ? WHERE id = 1", (chat_id,))


def set_lock_window(start: str, end: str) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE league SET lock_start = ?, lock_end = ? WHERE id = 1",
            (start, end),
        )


def start_draft(order: list[int]) -> None:
    with tx() as conn:
        conn.execute(
            """UPDATE league SET state = 'drafting',
                                 draft_order = ?,
                                 draft_pick_index = 0
               WHERE id = 1""",
            (json.dumps(order),),
        )


def record_pick(player_id: int, number: int) -> None:
    with tx() as conn:
        league = conn.execute("SELECT draft_pick_index FROM league WHERE id = 1").fetchone()
        idx = league["draft_pick_index"]
        conn.execute(
            "INSERT INTO draft_picks(pick_index, player_id, number, picked_at) VALUES (?,?,?,?)",
            (idx, player_id, number, _now()),
        )
        conn.execute(
            "INSERT INTO rosters(number, player_id, acquired_at) VALUES (?,?,?)",
            (number, player_id, _now()),
        )
        conn.execute(
            "UPDATE league SET draft_pick_index = draft_pick_index + 1 WHERE id = 1"
        )


def finish_draft() -> None:
    with tx() as conn:
        conn.execute(
            """UPDATE league
                 SET state = 'in-season',
                     season_started_at = ?
               WHERE id = 1""",
            (_now(),),
        )


def end_season(winner_player_id: int | None) -> None:
    with tx() as conn:
        conn.execute(
            """UPDATE league
                 SET state = 'ended',
                     season_ended_at = ?,
                     winner_player_id = ?
               WHERE id = 1""",
            (_now(), winner_player_id),
        )
        conn.execute(
            "UPDATE pending_trades SET status = 'cancelled', resolved_at = ? WHERE status = 'pending'",
            (_now(),),
        )


# ---------- Players ----------

def add_player(tg_user_id: int, name: str) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO players(tg_user_id, name, joined_at) VALUES (?,?,?)",
            (tg_user_id, name, _now()),
        )
        return cur.lastrowid


def get_player_by_tg(tg_user_id: int) -> sqlite3.Row | None:
    with tx() as conn:
        return conn.execute(
            "SELECT * FROM players WHERE tg_user_id = ?", (tg_user_id,)
        ).fetchone()


def get_player(player_id: int) -> sqlite3.Row | None:
    with tx() as conn:
        return conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()


def list_players() -> list[sqlite3.Row]:
    with tx() as conn:
        return conn.execute("SELECT * FROM players ORDER BY id").fetchall()


# ---------- Rosters ----------

def owner_of(number: int) -> int | None:
    with tx() as conn:
        row = conn.execute(
            "SELECT player_id FROM rosters WHERE number = ?", (number,)
        ).fetchone()
    return row["player_id"] if row else None


def roster_for(player_id: int) -> list[int]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT number FROM rosters WHERE player_id = ? ORDER BY number",
            (player_id,),
        ).fetchall()
    return [r["number"] for r in rows]


def all_rosters() -> dict[int, list[int]]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT number, player_id FROM rosters ORDER BY number"
        ).fetchall()
    out: dict[int, list[int]] = {}
    for r in rows:
        out.setdefault(r["player_id"], []).append(r["number"])
    return out


def owner_map() -> dict[int, int]:
    with tx() as conn:
        rows = conn.execute("SELECT number, player_id FROM rosters").fetchall()
    return {r["number"]: r["player_id"] for r in rows}


def free_agents(pool_min: int = 1, pool_max: int = 70) -> list[int]:
    owned = set(owner_map().keys())
    return [n for n in range(pool_min, pool_max + 1) if n not in owned]


def transfer(number: int, from_player_id: int, to_player_id: int) -> bool:
    """Atomic swap of ownership. Returns False if the number isn't owned by from_player_id."""
    with tx() as conn:
        cur = conn.execute(
            """UPDATE rosters
                 SET player_id = ?, acquired_at = ?
               WHERE number = ? AND player_id = ?""",
            (to_player_id, _now(), number, from_player_id),
        )
        return cur.rowcount == 1


def swap_numbers(player_id: int, drop_number: int, add_number: int) -> str | None:
    """Drop `drop_number` and pick up `add_number` in one transaction. Returns None on
    success or a human-readable reason on failure."""
    with tx() as conn:
        own = conn.execute(
            "SELECT player_id FROM rosters WHERE number = ?", (drop_number,)
        ).fetchone()
        if not own or own["player_id"] != player_id:
            return f"You don't own {drop_number}."
        taken = conn.execute(
            "SELECT player_id FROM rosters WHERE number = ?", (add_number,)
        ).fetchone()
        if taken:
            return f"{add_number} is already taken."
        conn.execute("DELETE FROM rosters WHERE number = ?", (drop_number,))
        conn.execute(
            "INSERT INTO rosters(number, player_id, acquired_at) VALUES (?,?,?)",
            (add_number, player_id, _now()),
        )
    return None


# ---------- Draws ----------

def record_draw(lottery_key: str, draw_date: dt.date, mains: Iterable[int], bonus: int | None) -> int | None:
    """Insert a draw; return its row id, or None if it was already recorded."""
    with tx() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO draws(lottery_key, draw_date, mains, bonus, scraped_at)
                   VALUES (?,?,?,?,?)""",
                (lottery_key, draw_date.isoformat(), json.dumps(list(mains)), bonus, _now()),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def draw_by_id(draw_id: int) -> sqlite3.Row | None:
    with tx() as conn:
        return conn.execute("SELECT * FROM draws WHERE id = ?", (draw_id,)).fetchone()


def recent_draws(limit: int = 10, lottery_key: str | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM draws"
    args: tuple = ()
    if lottery_key:
        q += " WHERE lottery_key = ?"
        args = (lottery_key,)
    q += " ORDER BY draw_date DESC, id DESC LIMIT ?"
    with tx() as conn:
        return conn.execute(q, args + (limit,)).fetchall()


# ---------- Scores ----------

def record_score(
    draw_id: int,
    player_id: int,
    points: float,
    main_matches: list[int],
    bonus_match: bool,
) -> None:
    with tx() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO scores(draw_id, player_id, points, main_matches, bonus_match)
               VALUES (?,?,?,?,?)""",
            (draw_id, player_id, points, json.dumps(main_matches), 1 if bonus_match else 0),
        )


def all_scores_with_draw() -> list[sqlite3.Row]:
    """Score rows joined with their draw's lottery_key and bonus number."""
    with tx() as conn:
        return conn.execute(
            """SELECT s.player_id, s.points, s.main_matches, s.bonus_match,
                      d.lottery_key, d.bonus AS draw_bonus
                 FROM scores s
                 JOIN draws d ON d.id = s.draw_id"""
        ).fetchall()


def standings() -> list[tuple[int, str, float]]:
    with tx() as conn:
        rows = conn.execute(
            """SELECT p.id, p.name, COALESCE(SUM(s.points), 0) AS total
                 FROM players p
                 LEFT JOIN scores s ON s.player_id = p.id
                GROUP BY p.id
                ORDER BY total DESC, p.name ASC"""
        ).fetchall()
    return [(r["id"], r["name"], r["total"]) for r in rows]


# ---------- Trades ----------

def propose_trade(proposer_id: int, target_id: int, give: list[int], get: list[int]) -> int:
    with tx() as conn:
        cur = conn.execute(
            """INSERT INTO pending_trades(proposer_id, target_id, give_nums, get_nums, created_at)
               VALUES (?,?,?,?,?)""",
            (proposer_id, target_id, json.dumps(give), json.dumps(get), _now()),
        )
        return cur.lastrowid


def get_trade(trade_id: int) -> sqlite3.Row | None:
    with tx() as conn:
        return conn.execute(
            "SELECT * FROM pending_trades WHERE id = ?", (trade_id,)
        ).fetchone()


def list_pending_trades() -> list[sqlite3.Row]:
    with tx() as conn:
        return conn.execute(
            "SELECT * FROM pending_trades WHERE status = 'pending' ORDER BY id"
        ).fetchall()


def resolve_trade(trade_id: int, status: str) -> None:
    assert status in {"accepted", "rejected", "cancelled"}
    with tx() as conn:
        conn.execute(
            "UPDATE pending_trades SET status = ?, resolved_at = ? WHERE id = ?",
            (status, _now(), trade_id),
        )


def execute_trade_atomic(trade: sqlite3.Row) -> str | None:
    """Apply a trade's number swaps atomically. Returns None on success or a
    human-readable reason if it can't be applied (e.g. someone no longer owns a number)."""
    give = json.loads(trade["give_nums"])
    get = json.loads(trade["get_nums"])
    proposer = trade["proposer_id"]
    target = trade["target_id"]
    with tx() as conn:
        for n in give:
            row = conn.execute(
                "SELECT player_id FROM rosters WHERE number = ?", (n,)
            ).fetchone()
            if not row or row["player_id"] != proposer:
                return f"Proposer no longer owns {n}."
        for n in get:
            row = conn.execute(
                "SELECT player_id FROM rosters WHERE number = ?", (n,)
            ).fetchone()
            if not row or row["player_id"] != target:
                return f"Target no longer owns {n}."
        now = _now()
        for n in give:
            conn.execute(
                "UPDATE rosters SET player_id = ?, acquired_at = ? WHERE number = ?",
                (target, now, n),
            )
        for n in get:
            conn.execute(
                "UPDATE rosters SET player_id = ?, acquired_at = ? WHERE number = ?",
                (proposer, now, n),
            )
    return None
