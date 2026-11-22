# Перехід на Cloudflare Cron і швидкий кеш графіків

Ця інструкція стосується гілки `cloudflare-cron-and-fast-schedule-cache` і застосовується **після злиття pull request у `main`**.

## Що змінюється

- Cloudflare Worker запускає регулярну перевірку кожні 10 хвилин через Cron Trigger.
- Worker викликає існуючий GitHub Actions workflow `check-schedule.yml`. Python, отримання даних TOE, порівняння інтервалів і генерація PNG залишаються без переписування.
- GitHub Actions більше не має власного `schedule` для цього workflow; ручний `workflow_dispatch` залишається для тестів.
- Після кожної успішної перевірки PNG зберігається у приватному Cloudflare R2.
- Кнопка «📊 Мій графік» надсилає свіжий кешований PNG без запуску нового GitHub runner. Кеш старший за 15 хвилин не використовується: у такому разі працює попередній резервний сценарій через GitHub Actions.

## 1. Злити зміни

1. Відкрий pull request з гілки `cloudflare-cron-and-fast-schedule-cache` у `main`.
2. Переглянь перелік файлів і переконайся, що зміни стосуються лише планувальника, PNG-кешу, Worker та документації.
3. Злий pull request у `main`. Не видаляй гілку, доки не завершиш перевірку після розгортання.

До цього моменту production-бот продовжує працювати за старою схемою.

## 2. Створити приватний R2 bucket

У PowerShell відкрий папку `worker` локальної копії репозиторію:

```powershell
cd шлях\до\toe-light-bot\worker
npm install
npx wrangler login
npx wrangler r2 bucket create toe-public-schedule-cache
```

Bucket має називатися точно `toe-public-schedule-cache`. Не вмикай Public Development URL і не роби bucket публічним: доступ здійснюється лише через Worker binding.

## 3. Створити спільний секрет для завантаження PNG

Згенеруй випадковий секрет локально:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Збережи його у менеджері паролів. Це **одне й те саме значення** для Cloudflare Worker і GitHub Actions.

У папці `worker` виконай:

```powershell
npx wrangler secret put CACHE_UPLOAD_SECRET
```

Встав згенерований секрет у запит Wrangler.

Не створюй нове значення для GitHub: використовуй точно те саме.

## 4. Перевірити GitHub Secrets

Відкрий GitHub → репозиторій `OlegSysakSOM/toe-light-bot` → Settings → Secrets and variables → Actions.

Перевір наявність таких secrets:

| Secret | Значення |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Наявний токен Telegram-бота |
| `CLOUDFLARE_API_TOKEN` | Наявний Cloudflare API token із доступом до D1 |
| `CLOUDFLARE_ACCOUNT_ID` | Наявний Account ID |
| `CLOUDFLARE_D1_DATABASE_ID` | Наявний ID D1 |
| `WORKER_URL` | Повна базова адреса чинного Worker, наприклад `https://назва.піддомен.workers.dev`, без завершального слеша |
| `CACHE_UPLOAD_SECRET` | Той самий секрет, який щойно встановлено в Worker |

Не змінюй наявні Telegram і D1 secrets. Поточний `GITHUB_ACTIONS_TOKEN` у Cloudflare Worker також залиш без змін: він уже використовується для запуску workflow за запитом і потрібен Cron Trigger.

## 5. Розгорнути Worker

У тій самій папці `worker` виконай:

```powershell
npx wrangler deploy
```

Файл `wrangler.toml` уже містить:
- R2 binding `SCHEDULE_CACHE`;
- Cron Trigger `*/10 * * * *` (кожні 10 хвилин за UTC).

Повторно реєструвати Telegram webhook або створювати нову D1 базу не потрібно.

## 6. Заповнити кеш першим запуском

Після успішного розгортання відкрий GitHub → Actions → **TOE public bot — refresh schedules** → Run workflow.

Це одноразово запускає наявну перевірку вручну. Вона отримає графіки, збере PNG і завантажить зображення в R2. Наступні перевірки має ініціювати Cloudflare Cron автоматично.

У разі відсутності активних підписок PNG-кеш не наповниться, доки не буде хоча б однієї активної адреси.

## 7. Перевірити роботу

1. Відкрий адресу чинного Worker із закінченням `/health`. Відповідь має містити `ok: true`, `cache_enabled: true`, `cron_enabled: true` і `version: "2.5.0"`.
2. У Cloudflare Dashboard відкрий Workers & Pages → `toe-public-bot` → Settings → Trigger Events / Cron Events. Перевір, що з'являються запуски кожні 10 хвилин. Перші події можуть відображатися із затримкою.
3. У Cloudflare відкрий R2 → `toe-public-schedule-cache`. Після першого успішного refresh там мають з'явитися об'єкти з префіксом `schedules/`.
4. У Telegram натисни «📊 Мій графік». Якщо кеш уже свіжий (не старший за 15 хвилин), PNG має прийти напряму з Worker, без запуску нового workflow.
5. Якщо кешу ще немає або він застарів, спрацює старий резервний сценарій через GitHub Actions; перше натискання після розгортання може й надалі тривати близько 15 секунд. Після наповнення кешу наступні натискання мають бути значно швидшими.

## 8. Де дивитися помилки

- Cloudflare → Worker → Logs: помилки dispatch GitHub workflow, Telegram sendPhoto або завантаження кешу.
- Cloudflare → Worker → Settings → Cron Events: історія викликів scheduled handler.
- GitHub → Actions → TOE public bot — refresh schedules: чи завершилась сама перевірка графіків.
- R2 bucket: чи з'являються PNG у `schedules/`.

## Безпека та відкат

- Не надсилай токени й секрети в чат і не додавай їх у Git.
- Не видаляй наявні secrets, D1, Worker або старі workflow.
- Для відкату можна повернути попередню версію Worker і workflow з GitHub PR/revert. Поки перевіряєш нову схему, не видаляй старий on-demand workflow: він є резервним шляхом для кнопки.
