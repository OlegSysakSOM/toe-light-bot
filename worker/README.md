# Cloudflare Worker — Telegram webhook

This Worker handles Telegram messages/buttons immediately. Cloudflare Cron triggers the existing Python/GitHub Actions refresh workflow every 10 minutes. The latest rendered PNG is cached in a private R2 bucket so «📊 Мій графік» can return it without waiting for a new GitHub runner.

## Create D1 and deploy

1. Install Node.js LTS.
2. In this directory, run `npm install` and `npx wrangler login`.
3. Run `npx wrangler d1 create toe-public-bot-db` and copy the returned `database_id` into `wrangler.toml`.
4. Run `npx wrangler d1 execute toe-public-bot-db --remote --file=./schema.sql`.
5. Run `npx wrangler secret put TELEGRAM_BOT_TOKEN` and paste the existing BotFather token.
6. Generate two separate random values locally and set them using `npx wrangler secret put WEBHOOK_SECRET` and `npx wrangler secret put ADMIN_SECRET`.
7. Run `npx wrangler deploy`.
8. Register the webhook using the deployed worker URL:

PowerShell:

```powershell
$worker = "https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev"
$admin = Read-Host "Paste ADMIN_SECRET"
Invoke-RestMethod -Method Post -Uri "$worker/admin/register-webhook" -Headers @{ Authorization = "Bearer $admin" }
Invoke-RestMethod -Method Get -Uri "$worker/health"
```

## Worker secrets

- `TELEGRAM_BOT_TOKEN`
- `WEBHOOK_SECRET` — Telegram webhook secret token (1–256 characters; use a random URL-safe string)
- `ADMIN_SECRET` — protects the register-webhook endpoint; use a different random value

Do not commit secrets to Git or put them in `wrangler.toml`.

## D1 binding

`wrangler.toml` binds D1 as `DB`. The schema is in `schema.sql`.


For the Cron + private R2 cache migration and exact deployment steps, see [`SETUP_CF_CRON_R2_CACHE.md`](../SETUP_CF_CRON_R2_CACHE.md) in the repository root.
