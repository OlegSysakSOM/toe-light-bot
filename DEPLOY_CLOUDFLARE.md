# Повне розгортання TOE Public Bot 2.2.0

Ця версія прибирає повільне опитування Telegram з GitHub Actions. Cloudflare Worker приймає Telegram webhook та обробляє кнопки майже одразу. GitHub Actions окремо перевіряє графіки кожні 10 хвилин і генерує PNG на Python/Pillow.

> Не запускайте одночасно старий workflow `python bot.py --once`: він використовує застарілий `getUpdates` і не є production entry point. Старий `bot.py` більше не є точкою входу production.

## A. Cloudflare: створити D1

1. У Cloudflare Dashboard відкрийте **Storage & databases → D1 SQL Database** (або **Workers & Pages → D1**).
2. Натисніть **Create database**, назва: `toe-public-bot-db`.
3. Скопіюйте **Database ID** з картки створеної бази.
4. Скопіюйте **Account ID** з Cloudflare Dashboard (Overview / Account details).

## B. Cloudflare: створити Worker

Потрібен Node.js LTS на Windows: https://nodejs.org/

1. Відкрийте PowerShell у папці проєкту: `C:\Users\olegs\Desktop\toe_bot\worker`.
2. Відредагуйте `wrangler.toml`: замініть `REPLACE_WITH_D1_DATABASE_ID` на Database ID з кроку A.
3. Виконайте:

```powershell
npm install
npx wrangler login
npx wrangler d1 execute toe-public-bot-db --remote --file=./schema.sql
```

Якщо команда D1 повідомить, що база не знайдена, перевірте `database_name` та `database_id` у `wrangler.toml`.

4. Згенеруйте окремі секрети локально (вивід не публікуйте):

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Згенеруйте два різні значення: перше для `WEBHOOK_SECRET`, друге для `ADMIN_SECRET`.

5. Додайте секрети Worker по черзі:

```powershell
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put WEBHOOK_SECRET
npx wrangler secret put ADMIN_SECRET
```

Вставте токен BotFather і два випадкові значення у відповідні запити. Не записуйте їх у файли та не надсилайте нікому.

6. Опублікуйте Worker:

```powershell
npx wrangler deploy
```

Збережіть HTTPS URL Worker, наприклад `https://toe-public-bot.<твій-піддомен>.workers.dev`.

## C. Прив'язати D1 у Cloudflare

`wrangler.toml` уже містить binding:

```toml
[[d1_databases]]
binding = "DB"
database_name = "toe-public-bot-db"
database_id = "ВАШ_DATABASE_ID"
```

Переконайтеся, що саме ця база прив'язана як `DB`. Після `wrangler deploy` відкрийте адресу `https://...workers.dev/health`. Має повернути JSON з `ok: true`; `address_records` поки може бути 0.

## D. Зареєструвати Telegram webhook

1. У PowerShell задайте URL вашого Worker (підставте свій):

```powershell
$worker = "https://toe-public-bot.YOUR-SUBDOMAIN.workers.dev"
$admin = Read-Host "Введіть ADMIN_SECRET"
Invoke-RestMethod -Method Post -Uri "$worker/admin/register-webhook" -Headers @{ Authorization = "Bearer $admin" }
```

2. Має повернути `ok: true` і URL `/telegram/webhook`.
3. Перевірте health:

```powershell
Invoke-RestMethod -Method Get -Uri "$worker/health"
```

Після цього Telegram надсилатиме команди та натискання кнопок до Cloudflare Worker. Не запускайте стару версію `bot.py`, оскільки вона використовує застарілий режим `getUpdates`.

## E. Створити Cloudflare API Token для GitHub

Цей токен потрібен лише GitHub Actions, щоб читати/оновлювати D1.

1. Cloudflare → **My Profile → API Tokens → Create Token → Create Custom Token**.
2. Додайте дозволи для потрібного account: **D1 Read** та **D1 Write** (або D1 Edit, якщо інтерфейс показує об’єднаний дозвіл).
3. Обмежте ресурс тільки своїм Cloudflare account.
4. Створіть токен і скопіюйте один раз. Не вставляйте його в код.

## F. Додати GitHub Secrets

У GitHub репозиторії `OlegSysakSOM/toe-light-bot` відкрийте **Settings → Secrets and variables → Actions** та додайте/перевірте:

| Secret | Значення |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Той самий токен BotFather |
| `CLOUDFLARE_API_TOKEN` | API token з кроку E |
| `CLOUDFLARE_ACCOUNT_ID` | Account ID з кроку A |
| `CLOUDFLARE_D1_DATABASE_ID` | Database ID з кроку A |

Старий `STATE_ENCRYPTION_KEY` можна залишити поки для резерву, але версія 2.2 більше не читає `state.enc` як джерело підписок. Нові підписки зберігаються у приватній D1 базі.

## G. Завантажити довідник адрес TOE в D1

1. Переконайтеся, що оновлений код 2.2.0 вже на GitHub default branch `main`.
2. Відкрийте **Actions → Sync TOE address directory to Cloudflare D1**.
3. Натисніть **Run workflow**.
4. Дочекайтеся зеленого завершення. Перше завантаження проходить до сотень сторінок TOE і може зайняти деякий час.
5. Перевірте `https://ВАШ-WORKER/health`: поле `address_records` має бути більше нуля.

Цей workflow повторюється автоматично щотижня. Його можна запустити вручну після зміни довідника.

## H. Увімкнути оновлення графіків кожні 10 хвилин

Файл `.github/workflows/check-schedule.yml` уже налаштований на `2,12,22,32,42,52` хвилини кожної години. Він запускає `python schedule_job.py`, а не `python bot.py --once`.

GitHub repository → **Settings → Actions → General → Workflow permissions** → увімкніть Read and write, якщо цього вимагає ваша конфігурація (ця нова задача напряму комітів у Git не робить, тому в ідеалі достатньо read-only).

У вкладці Actions вручну запустіть **TOE public bot — refresh schedules** для першого тесту. Після цього графіки перевірятимуться за розкладом кожні 10 хвилин. Реальний старт GitHub може затримуватися.

## I. Оновити код у GitHub

У папці локального репозиторію `toe_bot` скопіюйте вміст нового архіву поверх старого. Збережіть `.git` і локальний `.env`. Не копіюйте `.env.example` поверх `.env`.

Потім:

```powershell
git add -A
git status --short
```

Переконайтеся, що `.env` не потрапляє до staging. Відправте:

```powershell
git commit -m "Move bot interactions to Cloudflare webhook and refresh schedules every 10 minutes"
git pull --rebase origin main
git push origin main
```

Якщо pull повідомляє про конфлікт, не використовуйте `--force`; спочатку розберіть конфлікт.

## J. Перевірка кнопок і підписки

Якщо у старій версії 2.1 ви бачите тільки «❌ Скасувати» та «ℹ️ Допомога», це означає, що бот вважає, що ви ще вводите адресу (`flow` не завершений). У старій версії натисніть «Скасувати», щоб вийти з цього стану. У версії 2.2 повне меню показується завжди, навіть під час введення адреси.

1. Відкрийте Telegram-бота й надішліть `/start`.
2. Постійна клавіатура завжди показує всі кнопки: Додати адресу, Змінити адресу, Мій графік, Вимкнути/Увімкнути сповіщення, Скасувати, Допомога. Вона не зникає під час введення адреси.
3. Натисніть **Додати адресу**, введіть населений пункт, вулицю та номер будинку.
4. Якщо є кілька збігів, виберіть адресу inline-кнопкою.
5. Після активації найближчий GitHub schedule job надішле персональний графік (до приблизно 10 хвилин, без гарантії точного часу).
6. Попросіть іншого користувача відкрити бота, натиснути Start і підключити іншу адресу. У кожного буде окремий запис у D1 та окреме фото.

## Діагностика

- **Worker `/health` 404:** перевірте `npx wrangler deploy` та Worker URL.
- **Webhook 401:** Telegram webhook secret не збігається з Worker secret; повторіть `npx wrangler secret put WEBHOOK_SECRET` і ще раз викличте `/admin/register-webhook`.
- **Не відповідає бот:** перевірте Deploy Worker у Cloudflare, Worker logs і webhook URL; перевірте, чи не запускається старий `bot.py`, який використовує legacy `getUpdates`.
- **`address_records: 0`:** запустіть workflow синхронізації адрес і дочекайтеся завершення.
- **GitHub schedule job не отримує D1:** перевірте `CLOUDFLARE_API_TOKEN` permissions та всі три `CLOUDFLARE_*` secrets.
- **GitHub job червоний:** відкрийте лог кроку `Refresh every active user's outage schedule`.

## Обмеження

Webhook відповідає без очікування наступного GitHub запуску; зазвичай реакція займає секунди, але твердо гарантувати максимум 5 секунд не можна. Оновлення графіків запускаються приблизно кожні 10 хвилин через GitHub Actions, і їхній старт може запізнюватися. Cloudflare Workers Free має денний ліміт запитів і CPU; D1 має ліміти рядків читання/запису. Якщо ліміти перевищені, Cloudflare відхилятиме частину запитів до скидання добового ліміту.
