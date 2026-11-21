# TOE Public Bot 2.4.0 — оновлення та запуск

У цій версії графік повертається картинкою в новому мобільному дизайні. Кнопка **📊 Мій графік** щоразу просить свіжі дані. Фоновий процес працює кожні 10 хвилин і надсилає фото лише тоді, коли реальні значення інтервалів змінилися.

## 1. Оновити репозиторій

Відкрийте PowerShell у локальній теці проєкту:

```powershell
git pull origin main
```

## 2. Створити GitHub token для Worker

Worker має запускати окремий GitHub Actions workflow після натискання кнопки «Мій графік».

На GitHub:
1. Відкрийте **Settings → Developer settings → Personal access tokens → Fine-grained tokens**.
2. Створіть token з Resource owner **OlegSysakSOM**.
3. У Repository access оберіть **Only select repositories → toe-light-bot**.
4. У Repository permissions встановіть **Actions → Read and write**. Інших додаткових дозволів не потрібно.
5. Згенеруйте та скопіюйте token. Не додавайте його до Git-репозиторію.

## 3. Зберегти token у Cloudflare Worker

Перейдіть у теку Worker:

```powershell
cd .\worker
npx.cmd wrangler secret put GITHUB_ACTIONS_TOKEN
```

Wrangler попросить значення secret. Вставте GitHub token у прихований запит.

## 4. Розгорнути Worker

У тій самій теці виконайте:

```powershell
npx.cmd wrangler deploy
```

Повторно реєструвати Telegram webhook не потрібно. Міграція D1 для цієї версії не потрібна. Існуючі secrets `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, `ADMIN_SECRET` та binding `DB` залишаються без змін.

## 5. Перевірити, що все підключено

У GitHub → **Actions** мають бути два workflow:
- **TOE public bot — send graph on request** — запускається натисканням «Мій графік».
- **TOE public bot — refresh schedules** — фонове порівняння кожні 10 хвилин.

У Telegram натисніть **📊 Мій графік**. Спочатку бот відповість, що формує картинку; після запуску GitHub runner надійде PNG. Затримка залежить від запуску hosted runner і не є миттєвою гарантією.

## 6. Нова логіка повідомлень

- Кнопка **Мій графік**: свіже звернення до TOE, нове PNG-повідомлення щоразу.
- Фонова перевірка: кожні 10 хвилин порівнює всі 48 півгодинних значень для кожної дати.
- Дані не змінилися: жодного повідомлення, жодного редагування старої картинки.
- Дані змінилися: одне нове PNG-повідомлення.
- Перша фонова перевірка лише створює початковий знімок без повідомлення.
- Картинка, отримана через кнопку, також оновлює знімок, тому фоновий процес не надсилатиме дубль за ті самі дані.

Якщо кнопка надсилає лише текст-підтвердження й після цього немає фото, перевірте **Actions → TOE public bot — send graph on request**. Якщо workflow не запускається взагалі, перевірте secret `GITHUB_ACTIONS_TOKEN` у Cloudflare Worker і повторіть `npx.cmd wrangler deploy`.

## 7. Перевірка коду

Для цього коміту GitHub Actions успішно виконав:
- Python compileall;
- **18 Python tests**;
- `node --check worker/src/index.js`;
- **2 Worker tests**.

Остання успішна перевірка: https://github.com/OlegSysakSOM/toe-light-bot/actions/runs/44913991910
