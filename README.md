# TOE Public Bot 2.6.0 — Cloudflare Cron + D1 PNG cache (no R2)

## How it works
- Cloudflare Cron dispatches the existing Python/GitHub Actions refresh at minute **00, 10, 20, 30, 40, 50** every hour.
- Python fetches TOE schedules, compares all 48 half-hour slots, renders the original PNG design, and sends a new photo only when the schedule really changes.
- Every successful refresh uploads the latest rendered PNG to the existing Cloudflare D1 database as a BLOB.
- D1 keeps exactly two image versions per unique address/queue: `latest_png` and `previous_png`. The older image is overwritten each refresh.
- «📊 Мій графік» sends the most recent cached PNG directly from D1. If the cache is missing or older than 15 minutes, the existing on-demand workflow remains as fallback.
- No change = no automatic message. Old Telegram messages are never edited.

## No R2
This release does not use R2, does not need an R2 bucket, and has no R2 binding. The PNG cache uses the existing D1 database.

## Deployment
Follow [SETUP_CF_CRON_D1_CACHE.md](SETUP_CF_CRON_D1_CACHE.md). The migration is additive: it adds only the `schedule_images` table and preserves existing users and addresses.

Existing secrets remain:
- GitHub Actions: `TELEGRAM_BOT_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_D1_DATABASE_ID`, plus `WORKER_URL` and `CACHE_UPLOAD_SECRET`.
- Cloudflare Worker: `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, `ADMIN_SECRET`, `GITHUB_ACTIONS_TOKEN`, and `CACHE_UPLOAD_SECRET`.

## Free-plan constraints
The uploader limits each PNG BLOB to 1.95 MB (D1's per-value limit is 2 MB). Each unique address/queue can cause up to 144 D1 row writes per day; monitor D1 row-write metrics as the public bot grows.
