# TOE Public Bot 2.4.0

- Rebuilt the PNG renderer around the supplied mobile reference: blue-purple gradient title, address/queue, split green/red/yellow hour tiles, current-time marker, outage interval chips, tomorrow publication state and legend.
- The **📊 Мій графік** button now triggers a separate GitHub Actions workflow that fetches current TOE data and sends the shared PNG renderer output.
- Background refresh runs every 10 minutes, compares complete day/48-slot snapshots, and sends a new PNG only for actual data changes.
- Unchanged schedules generate no Telegram message; no prior image is edited.
- First background fetch creates a silent baseline. A manual PNG request also updates that baseline.
- Added a fine-grained GitHub PAT requirement for Worker dispatch, plus deployment instructions.
- Updated CI tests for no-change/no-message, real-change/new-photo and 600px PNG output.
