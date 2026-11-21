# Deployment

Follow `DEPLOY_CLOUDFLARE.md` for full setup. Summary:

- Telegram UI/webhook and user subscriptions: Cloudflare Worker + D1.
- Public TOE address directory: synchronized to D1 using GitHub Actions.
- Schedule updates/photos: Python/GitHub Actions every 10 minutes.
- Do not run `python bot.py --once` in production; it removes the webhook. The production scheduled job is `python schedule_job.py`.
