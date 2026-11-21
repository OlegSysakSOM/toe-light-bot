# Security notes

- Never commit `.env`, Telegram tokens, or the Fernet encryption key.
- `state.enc` is encrypted before it is committed. Keep `STATE_ENCRYPTION_KEY` private and backed up.
- The repository code is public; user subscriptions and Telegram chat IDs are inside the encrypted state file.
- The bot processes only private Telegram chats.
- If the bot token is exposed, revoke it with BotFather and update the GitHub Secret immediately.
