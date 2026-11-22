# Deployment

Follow [SETUP_CF_CRON_D1_CACHE.md](SETUP_CF_CRON_D1_CACHE.md) for the current deployment.

- Telegram webhook and user/address state: Cloudflare Worker + existing D1.
- Background trigger: Cloudflare Cron at minute 00, 10, 20, 30, 40, 50 (UTC).
- TOE fetching, schedule comparison, and original PNG rendering: existing Python/GitHub Actions refresh job dispatched by the Worker.
- PNG cache: latest and previous BLOBs in existing D1. No Cloudflare R2.
- Do not run the legacy `python bot.py --once` polling entry point in production.
