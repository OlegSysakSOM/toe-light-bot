# Cloudflare Worker — Telegram webhook, Cron and D1 PNG cache (no R2)

This Worker handles Telegram messages/buttons, runs the background schedule trigger every 10 minutes, and returns the latest rendered PNG directly from D1.

## Existing binding
- `DB`: the existing D1 database `toe-public-bot-db`.
- `schedule_images`: two PNG BLOB versions per unique address/queue.

## Update steps
1. Install Node.js LTS.
2. In this directory run `npm.cmd install` and `npx.cmd wrangler login`.
3. Keep the existing `database_id` in `wrangler.toml`; do not create a new D1 database.
4. Apply the additive schema:
   ```powershell
   npx.cmd wrangler d1 execute toe-public-bot-db --remote --file=./schema.sql
   ```
5. Set `CACHE_UPLOAD_SECRET` with `npx.cmd wrangler secret put CACHE_UPLOAD_SECRET`.
6. Deploy with `npx.cmd wrangler deploy`.
7. Add the same `CACHE_UPLOAD_SECRET` and `WORKER_URL` to GitHub Actions Secrets.

See [SETUP_CF_CRON_D1_CACHE.md](../SETUP_CF_CRON_D1_CACHE.md) for the full migration guide.


## Version 2.6.1 — lower D1 write amplification

- Removed secondary indexes not used by Worker queries and the obsolete address-index staging table.
- Cached PNG requests avoid updating a user row when the snapshot/status is already current.
- Worker logs D1 `rows_read` and `rows_written` metadata for cache and Telegram update writes.
- See the root `CHANGELOG_2.6.1.md` for details.
