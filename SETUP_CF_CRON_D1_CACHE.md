# Повне оновлення: Cloudflare Cron + PNG-кеш у D1 (без R2)

Ця інструкція призначена для вже розгорнутого бота з Worker `toe-public-bot` та D1 `toe-public-bot-db`. Вона не створює нову базу й не видаляє користувачів або адреси. Cloudflare R2 не використовується.

## Що змінюється

- Cloudflare Cron запускає фонову перевірку на хвилинах **00, 10, 20, 30, 40, 50** кожної години (UTC).
- Worker викликає наявний GitHub Actions workflow `check-schedule.yml`.
- Python, запит до TOE, порівняння 48 півгодинних слотів, старий PNG-рендерер і логіка сповіщень при реальній зміні залишаються.
- Кожна успішна перевірка формує PNG і зберігає його в наявній D1 як BLOB.
- Для кожної унікальної адреси/черги D1 зберігає лише дві версії: `latest_png` та `previous_png`; старіша перезаписується.
- Кнопка **📊 Мій графік** віддає свіжий PNG безпосередньо з D1, без очікування нового GitHub runner. Якщо кеш відсутній або старший за 15 хвилин, залишається резервний on-demand workflow.
- Якщо графік не змінився — бот нічого не надсилає. Якщо реальні інтервали змінилися — надсилає нове фото. Попередні повідомлення не редагуються.
- **R2 не використовується. Не створюйте bucket.**

> Важливо: Cloudflare Cron керує часом запуску, але Python-запит до TOE та генерація PNG поки виконуються на GitHub Actions runner. Це дозволяє зберегти перевірений старий рендерер без переписування його на JavaScript.

## 1. Злити pull request

1. Відкрий pull request із гілки `d1-png-cache-cron-minute-alignment` у `main`.
2. Дочекайся завершення автоматичних перевірок.
3. Якщо всі перевірки зелені, натисни **Merge pull request → Confirm merge**.

До злиття та подальшого розгортання Worker робоча production-версія не змінюється.

## 2. Оновити локальні файли

У PowerShell:

```powershell
cd C:\Users\olegs\Desktop\toe_bot
git pull origin main
Get-Content .\VERSION
```

Очікувана версія: `2.6.0-d1-png-cache-cron-00`.

## 3. Додати таблицю кешу в наявну D1

Перейди до папки Worker:

```powershell
cd .\worker
npx.cmd wrangler whoami
```

Переконайся, що Wrangler підключений до правильного Cloudflare-акаунта.

Застосуй адитивну схему:

```powershell
npx.cmd wrangler d1 execute toe-public-bot-db --remote --file=./schema.sql
```

Це додає тільки таблицю `schedule_images` та індекс. Таблиці `users`, `address_index`, `processed_updates`, `bot_meta` не видаляються й не очищаються. **Не створюй нову D1 і не змінюй наявний `database_id`.**

## 4. Створити секрет для завантаження PNG

Згенеруй випадковий секрет:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Збережи значення в менеджері паролів. Потім у папці `worker` виконай:

```powershell
npx.cmd wrangler secret put CACHE_UPLOAD_SECRET
```

Коли Wrangler попросить значення, встав згенерований секрет. Символи можуть не відображатися — це нормально.

## 5. Додати два GitHub Actions Secrets

GitHub → репозиторій `OlegSysakSOM/toe-light-bot` → **Settings → Secrets and variables → Actions**.

Створи або онови:

| Secret | Значення |
|---|---|
| `WORKER_URL` | Базова адреса чинного Worker, без `/health` і без кінцевого `/` |
| `CACHE_UPLOAD_SECRET` | Те саме значення, яке щойно збережено у Worker |

Залиш без змін наявні секрети:
- `TELEGRAM_BOT_TOKEN`
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_D1_DATABASE_ID`

У Cloudflare Worker має залишатися `GITHUB_ACTIONS_TOKEN`: Cron використовує його, щоб запускати GitHub Actions workflow.

## 6. Розгорнути Worker

У папці `worker` виконай:

```powershell
npx.cmd wrangler deploy
```

У `worker/wrangler.toml` тепер має бути лише D1 binding `DB`, без `[[r2_buckets]]`, а розклад:

```toml
[triggers]
crons = ["0,10,20,30,40,50 * * * *"]
```

Telegram webhook повторно реєструвати не потрібно, оскільки URL Worker не змінюється.

## 7. Заповнити кеш першим запуском

GitHub → **Actions → TOE public bot — refresh schedules → Run workflow**.

Запусти один раз вручну й дочекайся зеленого завершення. Це отримає графіки, сформує PNG та створить перші BLOB-и в D1. Після цього планові запуски ініціюватиме Cloudflare Cron.

## 8. Перевірити результат

### Worker health

Відкрий:

```text
https://ТВІЙ-WORKER.workers.dev/health
```

Очікуються поля:
- `ok: true`
- `cache_enabled: true`
- `cache_backend: "d1_blob"`
- `cron_enabled: true`
- `version: "2.6.0"`

Якщо `cache_enabled: false`, перевір, чи виконано міграцію D1 з кроку 3.

### Перевірка D1

Cloudflare → **D1 → toe-public-bot-db → Console**. Виконай:

```sql
SELECT address_key, group_name,
       length(latest_png) AS latest_bytes,
       length(previous_png) AS previous_bytes,
       latest_cached_at, previous_cached_at
FROM schedule_images;
```

Після першого refresh має бути `latest_png`. Після наступного успішного refresh з'явиться і `previous_png`.

### Перевірка Cron

Cloudflare → **Workers & Pages → toe-public-bot → Triggers**. Переконайся, що встановлено:

```text
0,10,20,30,40,50 * * * *
```

У подіях Worker мають з'являтися виклики `scheduled`. GitHub Actions уже не повинен мати власного cron для refresh — він запускається Worker-ом.

### Перевірка Telegram

Натисни **📊 Мій графік**. Якщо PNG у D1 свіжий (не старший за 15 хвилин), Worker надішле його напряму з кешу, без нового GitHub runner. Після цього фонове оновлення не повинно дублювати повідомлення про ту саму зміну: натискання кнопки синхронізує baseline підписки з виданим знімком.

## Безкоштовні обмеження D1

- D1 BLOB має бути меншим за 2 MB; uploader обмежує PNG до 1.95 MB.
- Один унікальний address/queue оновлює один рядок кешу до 144 разів на добу. За денного ліміту 100 000 записів це теоретично близько 694 унікальних комбінацій до врахування інших записів. Стеж за **D1 → Metrics → Row Writes**.
- Якщо активних унікальних адрес/черг стане багато, безкоштовна квота записів D1 може стати обмеженням. У такому випадку треба буде зменшити частоту запису незмінних зображень або вибрати інше сховище; зараз платне сховище не підключається.

## Відкат

Якщо новий Worker не працює, поверни попередню Worker version у Cloudflare або зроби revert merge-коміту. Таблиця `schedule_images` адитивна й не заважає старому Worker. Не видаляй D1, користувачів, адреси чи наявні секрети.


## Діагностика D1 Rows Written і помилок Telegram (версія 2.6.1)

У попередній версії повна синхронізація приблизно 11 784 адрес переписувала всю таблицю через staging (додавання в staging, видалення основного індексу, повторне додавання всіх адрес, очищення staging). Через secondary-index writes це створювало надмірне навантаження на D1.

У 2.6.1 синхронізація порівнює адреси з чинним індексом:
- незмінений довідник — нуль D1 writes;
- змінені/нові адреси — тільки відповідні upsert;
- видалені адреси — тільки точкові delete.

Після оновлення schema.sql також видаляє невикористані індекси та застарілу staging-таблицю. Таблиці користувачів, живий адресний індекс і PNG-кеш залишаються.

### Як побачити фактичні записи

1. Запусти **Sync TOE address directory to Cloudflare D1** вручну.
2. Відкрий лог кроку завантаження адрес.
3. Наприкінці шукай `rows_read=...` та `rows_written=...`. Якщо дані не змінилися, очікуй `rows_written=0`.
4. Запусти **TOE public bot — refresh schedules** і шукай ті самі поля у рядку «Фоновий цикл».
5. Для Worker cache writes відкрий Cloudflare → Workers & Pages → toe-public-bot → Logs; шукай записи `D1 usage cache schedule PNG`.

D1 Free має денні квоти. Якщо після цього оновлення quota все одно швидко вичерпується, звір D1 Metrics за часом із логами та перевір, чи інші Worker/проєкти в цьому Cloudflare account не пишуть у цю ж базу/акаунт.

### Помилки Telegram 403

`user is deactivated` та `bot was blocked by the user` — це постійно недоступні приватні чати, а не проблема джерела TOE чи D1. У 2.6.1 така підписка автоматично вимикається, щоб не валити весь 10-хвилинний цикл. Тимчасові Telegram errors і помилки TOE/D1 залишаються видимими та повторюваними.
