# Розгортання TOE Public Bot 2.1

Повна інструкція міграції: [DEPLOY_MIGRATION.md](DEPLOY_MIGRATION.md).

Коротко:
- Telegram-повідомлення та кнопки меню перевіряються GitHub Actions приблизно кожні 5 хвилин (на 3-й, 8-й, 13-й тощо хвилинах години).
- Графіки Тернопільобленерго перевіряються приблизно кожні 15 хвилин.
- У Telegram користувач додає та змінює адресу кнопками внизу чату.
- Для розгортання потрібні GitHub Secrets `TELEGRAM_BOT_TOKEN` і `STATE_ENCRYPTION_KEY`.
- Не змінюйте існуючий `STATE_ENCRYPTION_KEY`: він розшифровує `state.enc`.
