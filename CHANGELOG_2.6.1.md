# TOE Public Bot 2.6.1 — D1 write optimization and resilient Telegram delivery

## Why this patch exists

The previous address-directory sync rewrote the complete ~11.8k-row D1 address index even when the TOE directory had not changed:
- insert every address into a staging table;
- delete the live address index;
- insert every address back into the live index;
- delete the staging rows.

D1 also accounts for secondary-index writes, so this created substantial write amplification. The previous refresh also treated a permanent Telegram 403 for a deactivated/blocked private chat as a fatal error for the entire workflow.

## Changes

- Address sync now builds a deterministic SHA-256 and compares normalized source rows against the current D1 address index.
- If the address directory is unchanged, the sync exits without any D1 writes.
- If it changed, only new/changed addresses are upserted and only removed source IDs are deleted in batches.
- Address sync logs exact D1 API query count, rows read, and rows written.
- The 10-minute schedule job logs exact D1 API query count, rows read, and rows written.
- Worker logs D1 usage for PNG cache updates, cached-photo snapshot sync, and processed Telegram updates.
- Telegram 403 errors indicating a deactivated account or a bot blocked by a user now pause only that subscription. The rest of the user list continues, and the background run does not fail because of that permanently unreachable chat.
- Transient Telegram errors remain retryable and keep the previous snapshot so a later check can retry the change alert.
- A cached-photo request no longer issues a redundant users-table UPDATE when the user's snapshot and status are already current.
- Removed unused D1 indexes `idx_users_address`, `idx_address_city_street`, and `idx_schedule_images_updated_at`.
- Removed the obsolete `address_index_staging` table and the unused per-cycle `bot_meta.last_schedule_check_at` write.

## Tests

New tests cover permanent Telegram 403 classification and subscription pause, unchanged/changed address-index diffs, D1 usage metadata aggregation, and avoiding a redundant user UPDATE when serving a current cached PNG.

The original PNG design, 48 half-hour slot comparison, Cloudflare Cron cadence (00/10/20/30/40/50), D1 BLOB image cache, and changed-only notifications remain unchanged.
