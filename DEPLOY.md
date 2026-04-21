# Deployment

Single long-running Python process. Runs on any Linux VM with systemd — Oracle
Cloud free tier, GCE e2-micro, Fly.io, Hetzner, etc.

## 1. Create the Telegram bot

1. DM [@BotFather](https://t.me/BotFather), send `/newbot`, pick a name/username.
   Save the API token.
2. DM [@userinfobot](https://t.me/userinfobot) to get your numeric Telegram
   user ID — that's what you'll set as the admin.

## 2. Install on the VM

```bash
# as a regular user (e.g. `lotto`)
git clone <repo-url> /srv/lotto-land-tg-bot
cd /srv/lotto-land-tg-bot
curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv isn't installed
uv sync
```

## 3. Configure

```bash
cp .env.example .env
chmod 600 .env
```

Fill in `.env`:

```
LOTTO_BOT_TOKEN=<token from BotFather>
LOTTO_ADMINS=<your Telegram user id>
LOTTO_DB_PATH=/srv/lotto-land-tg-bot/lotto.db
LOTTO_TZ=America/New_York
LOTTO_DAILY_HOUR=9
```

## 4. systemd service

Write `/etc/systemd/system/lotto-bot.service`:

```ini
[Unit]
Description=lotto-land-tg-bot
After=network.target

[Service]
Type=simple
User=lotto
WorkingDirectory=/srv/lotto-land-tg-bot
EnvironmentFile=/srv/lotto-land-tg-bot/.env
ExecStart=/home/lotto/.local/bin/uv run python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Adjust `User=` and the `uv` path if your layout differs. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lotto-bot
sudo journalctl -u lotto-bot -f       # watch logs
```

## 5. Run the league

1. Add the bot to your Telegram group chat.
2. Admin runs `/setup` in the group to bind it as the league chat.
3. Each player sends `/join`.
4. Admin runs `/startdraft` to randomize order and begin.
5. Players take turns with `/pick <n>` until the draft finishes.
6. From then on, daily summaries post automatically at `LOTTO_DAILY_HOUR` local
   time. Use `/swap`, `/trade`, `/standings`, `/roster` during the season.
7. When it's time, admin runs `/end_season confirm`.

## Operational notes

### Outage = permanently missed draws

Each lottery's details page shows only its **5 most recent** draws. The bot has
no backfill path beyond that. If the VM goes down for more than ~5 days — or if
Match 6 / Match 6-like high-frequency lotteries draw more than 5 times during
an outage — those draws are silently lost when the bot comes back up.

Implications:

- `Restart=always` handles crashes. The rest is VM uptime.
- Free tiers sometimes reclaim idle instances. Oracle Always Free and GCE
  e2-micro have been stable; check your provider's policy.
- If you know you'll be offline > a couple of days (maintenance, migration),
  just pause the season in your group and expect a gap.

### Backup (optional, recommended)

`lotto.db` is the entire league state — rosters, draws, scores, pending trades.
A nightly snapshot is cheap insurance:

```cron
0 3 * * * cd /srv/lotto-land-tg-bot && mkdir -p backups && sqlite3 lotto.db ".backup backups/lotto-$(date +\%F).db" && find backups -name 'lotto-*.db' -mtime +30 -delete
```

If the VM dies, restore by copying the latest `backups/lotto-YYYY-MM-DD.db`
back to `lotto.db` and restart the service.

## Updating

```bash
cd /srv/lotto-land-tg-bot
git pull
uv sync
sudo systemctl restart lotto-bot
```

Schema migrations are idempotent, so restart is safe.

## Admin cheatsheet

| Command | What it does |
| --- | --- |
| `/setup` | Bind current group chat as the league chat |
| `/startdraft` | Randomize order and begin the snake draft |
| `/setlock HH:MM HH:MM` | Change the roster-lock window (default 20:00–09:30) |
| `/runscore_now` | Trigger the scrape+score+post job immediately |
| `/end_season confirm` | End the season, crown a winner, cancel pending trades |
| `/status` | State, player count, lock window, current lock status |
