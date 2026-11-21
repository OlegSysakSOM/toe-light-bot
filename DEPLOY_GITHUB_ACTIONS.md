# Швидкий запуск через GitHub Actions

1. Створіть бота через @BotFather і натисніть `/start`.
2. Дізнайтеся `TOE_ADDRESS` командою `python find_address.py "НАЗВА_НАСЕЛЕНОГО_ПУНКТУ" "НАЗВА_ВУЛИЦІ" --building "НОМЕР_БУДИНКУ"` або через заголовок `X-debug-key` на сайті.
3. Створіть GitHub-репозиторій і завантажте в нього вміст цієї папки. Не завантажуйте `.env` і не записуйте домашню адресу у відкриті файли репозиторію.
4. У Settings → Secrets and variables → Actions додайте п’ять секретів: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TOE_ADDRESS`, `ADDRESS_LABEL`, `STATE_ENCRYPTION_KEY`.
5. Згенеруйте ключ: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Збережіть його як `STATE_ENCRYPTION_KEY`.
6. У Actions дозвольте workflows, відкрийте «Оновлення графіка відключень» і натисніть Run workflow.

Перевірка запланована приблизно кожні 15 хвилин (на 7-й, 22-й, 37-й і 52-й хвилинах години). Фактичний старт може запізнюватися: GitHub Actions не гарантує точний час запуску.
