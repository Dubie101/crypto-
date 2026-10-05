---
id: quote-events-watchdog
title: Quote event logger watchdog
enabled: true
owner: goal:paper-trading-simulator
mode: task
schedule:
  kind: interval
  timezone: America/Edmonton
  at: 2026-10-05T01:46:51
  every: 5m
metadata:
  tags: [cron:automatic-interval-anchor]
  originating_chat_context_json: '{"chat_id":"bfd0e7c5-eb67-4358-82bc-b897f34b3639","origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false,"device_id":"a07182d56b04f883"}'
  presentation_locale: en-US
---
Quote-event logger watchdog. The logger (~/workspace/goals/paper-trading-simulator/hidden_files/book_watcher/quote_event_log.py) must run continuously; it logs every level2 delta, every mid change, and every individual trade print for XPL/VET/KITE/2Z to quote_events/events_YYYY-MM-DD.jsonl, feeding the Lane 3 book-level investigation (execution-mediated vs quote-mediated XPL repricing).

Check: read ~/workspace/goals/paper-trading-simulator/hidden_files/book_watcher/quote_events/heartbeat.txt (float unix timestamp -- parse with python, not bash arithmetic) and run `pgrep -f "[q]uote_event_log.py"` (bracket trick so pgrep cannot match its own command line).

If the heartbeat file is missing, older than 180 seconds, or no process matches:
1. Ensure the dependency (VM restarts sometimes wipe system pip installs, sometimes not -- verify, don't assume): `python3 -c "import websocket" 2>/dev/null || { pip install --break-system-packages websocket-client && python3 -c "import websocket" || { echo "FATAL: websocket-client install did not take" >&2; exit 1; }; }`. The post-install import check is mandatory -- a quiet pip success with the module still missing must fail loudly, not proceed.
2. Clear stale processes with a pkill command that mentions the script name ONLY in bracketed form: `pkill -f "[q]uote_event_log"` -- never put the bare name elsewhere in the same command or pkill will SIGTERM your own shell. Run the pkill as its own step, separate from the relaunch command.
3. Start fresh in its own step: `cd ~/workspace/goals/paper-trading-simulator/hidden_files/book_watcher && (setsid nohup python3 -u quote_event_log.py > /dev/null 2> quote_events/watch.log < /dev/null &)` -- the parenthesized subshell exits on its own; never kill its session afterward, verify with pgrep/heartbeat instead.
4. Wait 30s, then verify: heartbeat.txt exists and is fresher than 60s, and today's quote_events/events_YYYY-MM-DD.jsonl grew within the last 60s.

Stay silent on normal success (already healthy, or restarted cleanly). Report only if the restart fails: no fresh heartbeat after 60s, events file not growing, or repeated websocket errors in quote_events/watch.log. Observations go to the daily log ~/memory/YYYY-MM-DD.md, never edit MEMORY.md.
