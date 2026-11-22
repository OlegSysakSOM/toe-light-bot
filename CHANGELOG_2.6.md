# TOE Public Bot 2.6.0 — D1 BLOB cache, no R2

- Removed the Cloudflare R2 binding and all R2 cache code.
- Stores exactly two rendered PNG versions per unique address/queue in the existing D1 database as BLOBs.
- The Telegram button reads and sends the newest fresh PNG directly from D1; successful delivery advances the user's snapshot baseline to prevent duplicate change alerts.
- Cloudflare Cron runs at minute 00, 10, 20, 30, 40 and 50 every hour (UTC).
- Existing Python TOE API fetching, original Pillow renderer, 48-slot comparison and changed-only notification logic remain.
- D1 cache upload failures are counted and make the refresh job fail visibly.
- Added Worker tests for D1 BLOB rotation, direct cached photo delivery, Cron dispatch, and absence of R2.
