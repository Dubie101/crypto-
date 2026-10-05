---
id: burst-structure-daily
title: Burst structure daily check
enabled: true
owner: goal:paper-trading-simulator
mode: task
schedule:
  kind: daily
  timezone: America/Edmonton
  time: 06:30:00
delivery:
  - chat_id: bfd0e7c5-eb67-4358-82bc-b897f34b3639
metadata:
  originating_chat_context_json: '{"chat_id":"bfd0e7c5-eb67-4358-82bc-b897f34b3639","origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false,"device_id":"a07182d56b04f883"}'
  presentation_locale: en-US
---
Daily burst-structure check — Mike's observed 3-traders-same-second quoting pattern (seen visually for 1+ weeks, confirmed instrumentally 2026-10-05).

Run: `python3 ~/workspace/goals/paper-trading-simulator/hidden_files/book_watcher/burst_monitor.py`

It reads the last 24h of quote_events, recomputes per-second burst structure, appends a row to burst_structure_log.jsonl, and prints JSON with an `alert` field that is non-null ONLY on structural change vs the trailing 7-day baseline (modal burst count shift, disagreement rate move > 15pts, or cross-burst spread widening > 2x).

Stay silent when `alert` is null. Report the alert text plus the day's row (modal bursts, disagreement rate, median spread, busy seconds) when non-null. If the quote_events directory has no data newer than 2 hours, say so once (the lane may be down) instead of reporting structure numbers.

Note: quote-event data accumulates ~1GB/day; do not add retention/deletion — that needs Mike's explicit per-item approval.
