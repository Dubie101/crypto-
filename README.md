# Crypto

Mike's crypto market research workspace: live book watchers, trade-flow taps, rotation-pattern scans, and latent-state ("heartbeat") experiments on Coinbase Advanced Trade public market data.

## Layout

- `book_watcher/` — the instrument lane. 1-second tick capture (`tick_capture.py`: mid, spread, top-of-book imbalance per product), public websocket book tap (`ws_tap.py`: level2 + trade prints → 30s aggregate buckets, `ws_analyze.py` → leader/activity/rotation readouts), all-asset trade-print tap (`all_trades_tap.py`: 402 USD products, per-second buy/sell notionals), peak-dislocation paper trader (`peak_watcher.py`), pump.fun birth watcher (`pump_watcher.py`), rotation scans (`rotation_matched_all.py`, `rotation_exact.py`, `rotation_scan1_rerun.py`).
- `latent_heartbeat/` — latent market-state experiments. v3: PCA factor on 1s returns (stable 4-asset cluster factor XPL/VET/KITE/2Z; blind leaders-board test failed). v4: 30s state-persistence model, R²(h) decay, cross-observable (mid/spread/imbalance/flow) comparison. Verdict: contemporaneous covariance without meaningful temporal persistence — quote coordination, not a latent market state.
- `rotation/` — rotation-hunt result files (small).
- `vector_align/` — vector-alignment backtests.
- `crons/` — scheduled-job specs (watchdogs, taps, daily rotation hunt, morning briefing).
- `snapshots/` — dashboard snapshot(s).
- `GOAL.md` — goal notes.

## Deliberately excluded

- Captured market data (`ticks/`, `trades_all/`, `ws_buckets.jsonl`, `candles/`, ~350MB and growing) — stays local.
- Secrets: API keys, `.env` files, wallet keystores. Code references key paths at runtime; no secret values are committed.
- Logs, `__pycache__`, ephemeral watcher state.

## Notes

- All market data is public (no authentication). Nothing here places orders.
- Read-only by default; see each script's header for its role.
