# TOE Public Bot 2.2.0 — webhook + 10-minute outage refresh

This release separates fast Telegram interaction from schedule refresh:

- Cloudflare Worker handles Telegram webhook updates and inline/reply buttons in real time.
- Cloudflare D1 stores users, their private chat subscriptions and the public TOE address directory.
- GitHub Actions runs `schedule_job.py` every 10 minutes, downloads/compares outage schedules and sends/edits personalized photos.
- A separate workflow imports/refreshes the TOE public address directory in D1 weekly and on manual request.

## Full deployment guide

Read `DEPLOY_CLOUDFLARE.md` from top to bottom. Do not use the old 2.1 workflow that runs `python bot.py --once`: it polls Telegram with `getUpdates` and deletes webhook configuration. The new scheduled workflow runs `schedule_job.py` only.

## Button menu

The persistent keyboard always shows all actions, including during address entry. It no longer collapses to only Cancel and Help:

- Add address
- Change address
- My schedule
- Pause/resume notifications
- Cancel current address entry
- Help

If the old 2.1 bot showed only Cancel and Help, it was in an unfinished address-entry flow. Press Cancel once or deploy 2.2.0; the new keyboard always exposes the full menu.

## Required GitHub Actions secrets

- `TELEGRAM_BOT_TOKEN`
- `CLOUDFLARE_API_TOKEN` — Cloudflare API token with D1 Edit permission for this account
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_D1_DATABASE_ID`

The old `STATE_ENCRYPTION_KEY` is no longer used by the 2.2 architecture. Keep it temporarily until the new setup is verified; after migration, the live subscriptions reside in Cloudflare D1.

## Limits and reliability

Telegram button/text updates are delivered to the Worker using a webhook, normally within seconds, subject to Telegram/network/Cloudflare latency. No free provider can guarantee a hard maximum of 5 seconds. Schedule refresh is started by GitHub Actions every 10 minutes; scheduled runs can be delayed. User subscriptions live in D1, not in the public Git repository.
