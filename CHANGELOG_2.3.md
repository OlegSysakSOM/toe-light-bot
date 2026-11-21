# TOE Public Bot 2.3.0

- Added live on-demand schedule retrieval when a user presses **📊 Мій графік**.
- Added an immediate live schedule attempt after a new address is activated; it no longer waits for the next 10-minute GitHub Actions run to see the first schedule.
- Worker fetches the official TOE schedule API for the user's address and queue, displays today's/tomorrow's half-hour intervals, and reports API errors explicitly (never implies that no outages exist).
- Added a short edge cache for identical address/queue/date requests to reduce duplicate source API load during bursts.
- The Telegram webhook acknowledges updates quickly; live fetch and message delivery run in `waitUntil` background work.
- Existing GitHub Actions job remains responsible for the regular 10-minute PNG schedule refresh/edit.
