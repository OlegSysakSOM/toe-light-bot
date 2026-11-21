# TOE Public Bot 2.2.0

## Main changes

- Telegram UI now uses a Cloudflare Worker webhook. Button clicks and text input no longer wait for a GitHub Actions polling interval; typical response is within seconds, not a hard SLA.
- Cloudflare D1 stores private user subscriptions and the TOE address directory.
- The complete reply keyboard is always visible, including while a user enters an address. It no longer collapses to only Cancel and Help.
- Address lookup uses Unicode-normalized city/street columns in D1 and matches building/group records from the TOE public directory.
- GitHub Actions runs `schedule_job.py` every 10 minutes. The schedule job reads active subscriptions from D1, fetches TOE schedules, renders PNGs, sends a new photo when the graph changes, and edits the current photo when it does not.
- A separate weekly/manual GitHub workflow synchronizes the public TOE address directory into D1.
- No old `getUpdates` polling or webhook deletion is part of production workflows.
- Schedule persistence writes only changed subscription state to D1, reducing free-tier row writes; one global last-check timestamp is stored per run.

## Important migration notes

- The old `bot.py --once` entry point uses legacy `getUpdates`; do not run it after webhook deployment. Production schedule refresh is `python schedule_job.py`.
- `state.enc` and `STATE_ENCRYPTION_KEY` are no longer the production store. Cloudflare D1 is authoritative for new subscriptions. Keep the old state file/key as backup until 2.2 is fully verified.
- Users should send `/start` once after webhook setup and re-add their address if the old single-user state has not been migrated.
- GitHub schedule is every 10 minutes; GitHub can delay scheduled runs.
