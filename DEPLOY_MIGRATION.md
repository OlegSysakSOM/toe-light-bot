# Міграція на Cloudflare webhook + GitHub schedule

Для цієї версії використовуйте інструкцію `DEPLOY_CLOUDFLARE.md` повністю. Стара міграція 2.1 з polling через `getUpdates` більше не актуальна: у production Telegram updates отримує Cloudflare Worker через webhook, а GitHub Actions запускає лише `schedule_job.py` кожні 10 хвилин.

Не запускайте `python bot.py --once` після реєстрації webhook: старий CLI path використовує legacy `getUpdates` і вимикає webhook. Для оновлення графіків використовується `python schedule_job.py`.
