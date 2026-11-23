# Розгортання TOE Public Bot — актуальна інструкція

Для поточної версії **2.6.1** використовуйте [SETUP_CF_CRON_D1_CACHE.md](SETUP_CF_CRON_D1_CACHE.md).

Поточна production-архітектура:
- Telegram webhook і кнопки — Cloudflare Worker.
- Регулярний запуск — Cloudflare Cron на хвилинах **00, 10, 20, 30, 40, 50** кожної години (UTC).
- Отримання графіка, порівняння 48 слотів, генерація старого PNG-дизайну й автоматичні сповіщення — наявний Python workflow GitHub Actions, який запускає Worker Cron.
- Кеш останніх двох PNG для кожної адреси/черги — наявна Cloudflare D1 база, тип BLOB.
- **Cloudflare R2 не використовується.** Не створюйте R2 bucket і не додавайте R2 binding.

Інструкція D1-кешу не створює нову базу: вона лише додає таблицю `schedule_images`.


## Оновлення 2.6.1 — зменшення записів D1 і стійкість Telegram

1. Оновіть локальний репозиторій: `git pull origin main`.
2. У теці `worker` застосуйте оновлену схему до наявної бази:
   `npx.cmd wrangler d1 execute toe-public-bot-db --remote --file=./schema.sql`.
   Ця міграція не видаляє users, address_index чи schedule_images. Вона прибирає лише застарілу address_index_staging та невикористані secondary indexes.
3. Нових секретів ця версія не потребує. Усі наявні секрети залишаються без змін.
4. Оновіть Worker: `npx.cmd wrangler deploy`.
5. Один раз запустіть workflow **Sync TOE address directory to Cloudflare D1** вручну. Якщо довідник не змінився, журнал має показати `rows_written=0` для синхронізації.
6. Запустіть **TOE public bot — refresh schedules** вручну один раз і перевірте його журнал.

У журналі address sync тепер є `queries`, `rows_read`, `rows_written`, а також кількість нових/оновлених/видалених адрес. У фоновому schedule job є власні `rows_read` та `rows_written` для D1 REST-запитів; Cloudflare Worker окремо логує записи кешу PNG.

Якщо Telegram повертає `403: user is deactivated` або `bot was blocked by the user`, бот призупиняє лише цю підписку. Інші підписки продовжують перевірятися. Користувач, який знову може писати боту, може натиснути «Увімкнути сповіщення».
