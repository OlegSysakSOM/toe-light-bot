# Security

- Never commit `.env`, `worker/.dev.vars`, Telegram bot tokens, Cloudflare API tokens, webhook secrets, admin secrets, or Cloudflare account credentials.
- Worker runtime secrets: `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, `ADMIN_SECRET`.
- GitHub Actions secrets: `TELEGRAM_BOT_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_D1_DATABASE_ID`.
- Create a least-privilege Cloudflare API token scoped to the target account and D1 Read + D1 Write only.
- D1 stores private Telegram chat IDs and address subscriptions. Do not share D1 query endpoints or access tokens.
- The public source code contains no credentials. The `address_index` table contains only the public address directory published by the electricity provider.
- Rotate `TELEGRAM_BOT_TOKEN` via BotFather if exposed; rotate `WEBHOOK_SECRET` and re-register the webhook if leaked.
- Keep `ADMIN_SECRET` private; it is required to invoke `/admin/register-webhook`.
