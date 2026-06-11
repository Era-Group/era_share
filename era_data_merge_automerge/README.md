# era_data_merge_automerge

This addon adds a production-safe auto-merge cron for Odoo Data Cleaning duplicates.

## Why
Your database blocks many Python opcodes in Scheduled Actions (safe_eval), so cron code in ir.cron cannot use try/except, with savepoints, imports, __name__, etc.
This addon keeps cron code to a thin wrapper (`model.cron_run()`), while all merge logic runs in a real Python model method.

## What it does
- Prevents concurrent runs using PostgreSQL advisory lock.
- Finds active Deduplication Models (optionally only automatic ones).
- Finds duplicate groups and merges them in batches.
- Uses DB savepoints per group (one failure won't stop the cron).

## Install
1. Copy addon folder `era_data_merge_automerge` into your Odoo addons path.
2. Update Apps list.
3. Install **ERA Data Cleaning Auto Merge (Safe)**.
4. The cron will be created automatically.

## Configuration (optional)
Set these System Parameters (Settings > Technical > Parameters > System Parameters):
- `data_merge_automerge.lock_key` (int) default 987654321
- `data_merge_automerge.batch_size` (int) default 50
- `data_merge_automerge.run_find_first` (bool) default False
- `data_merge_automerge.dedup_model` (str) default "" (auto-detect). Set this if your dedup model name is custom.
- `data_merge_automerge.group_model` (str) default "" (auto-detect). If you know your group model, set it for best reliability.
- `data_merge_automerge.target_model` (str) default "res.partner". Limits fallback scan to a business model.
- `data_merge_automerge.only_automatic` (bool) default True
- `data_merge_automerge.state_value` (str) default "duplicate"

## Notes
- Test on staging before enabling on production.
- If your model names differ across Odoo versions, set `data_merge_automerge.dedup_model` and `data_merge_automerge.group_model`.
- `data_merge_automerge.only_automatic=True` is enforced in both normal and fallback merge paths.
