# Розгортання TOE Public Bot — актуальна інструкція

Для поточної версії **2.6.0** використовуйте [SETUP_CF_CRON_D1_CACHE.md](SETUP_CF_CRON_D1_CACHE.md).

Поточна production-архітектура:
- Telegram webhook і кнопки — Cloudflare Worker.
- Регулярний запуск — Cloudflare Cron на хвилинах **00, 10, 20, 30, 40, 50** кожної години (UTC).
- Отримання графіка, порівняння 48 слотів, генерація старого PNG-дизайну й автоматичні сповіщення — наявний Python workflow GitHub Actions, який запускає Worker Cron.
- Кеш останніх двох PNG для кожної адреси/черги — наявна Cloudflare D1 база, тип BLOB.
- **Cloudflare R2 не використовується.** Не створюйте R2 bucket і не додавайте R2 binding.

Інструкція D1-кешу не створює нову базу: вона лише додає таблицю `schedule_images`.
