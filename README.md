# TOE Public Bot 2.4.0 — live PNG on button, changed-only background alerts

## How it works
- **📊 Мій графік** requests a fresh schedule from the TOE API and sends a PNG image using the mobile-first design based on the supplied reference.
- Every 10 minutes, GitHub Actions checks the active subscriptions in the background.
- The background process compares the complete dates and all 48 half-hour slots.
- If nothing changed, the bot sends nothing and does not edit any previous message.
- If the schedule really changed, the bot sends one new PNG photo.
- The first successful background check creates a silent baseline. To see the first image, press **📊 Мій графік**.
- A manual image request also updates the comparison baseline, preventing a duplicate automatic alert for the same data.

## Deployment
Read **DEPLOY_2.4.md** for the Worker token and redeployment steps.

GitHub Actions secrets:
- `TELEGRAM_BOT_TOKEN`
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_D1_DATABASE_ID`

Cloudflare Worker secret:
- `GITHUB_ACTIONS_TOKEN` — fine-grained PAT, restricted to this repository, with Actions: Read and write.

The PNG renderer uses the Inter font files in `fonts/`.

## Safe test notification

Run `.github/workflows/test-change-notification.yml` manually with your own active Telegram chat ID. It reads the subscription and live schedule, flips one slot only in an in-memory copy, uses the same snapshot comparator and photo delivery helper as the production automatic alert, and sends exactly one clearly marked test PNG to that chat. It performs no writes to D1 and does not notify other users.
