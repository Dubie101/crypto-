---
id: divergence-forward-scorer
title: Divergence forward scorer (frozen rule)
enabled: true
owner: goal:paper-trading-simulator
mode: task
schedule:
  kind: interval
  timezone: America/Edmonton
  at: 2026-10-03T10:29:57
  every: 6h
delivery:
  - chat_id: bfd0e7c5-eb67-4358-82bc-b897f34b3639
metadata:
  tags: [cron:automatic-interval-anchor]
  originating_chat_context_json: '{"chat_id":"bfd0e7c5-eb67-4358-82bc-b897f34b3639","origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false,"device_id":"a07182d56b04f883"}'
  presentation_locale: en-US
---
Forward scoring for the FROZEN divergence branch-prediction rule (PROTOCOL.md in hidden_files/divergence_derivative/ — read it before touching anything; the rule, thresholds, windows, and classification are frozen by Mike and must not be altered, tuned, or extended).

Run: `python3 ~/workspace/goals/paper-trading-simulator/hidden_files/divergence_derivative/score_forward.py`

It self-tests episode detection against the 11 frozen historical onsets (aborts on mismatch — do not work around a self-test failure, report it), appends predictions for new post-freeze episodes, and fills in realized branches once an episode is 12h past onset. It writes only forward_ledger.json.

Stay silent when the JSON summary shows no new_predictions and no new_realizations. Report when either is non-empty: the episode onset time (UTC), leader/laggard, the two frozen dimensions separately (slope_in value and sign; leader_ret_30m value and sign), the prediction, and for realizations the predicted-vs-realized outcome plus the running score (correct/scored/target 30). Never present tonight's pre-freeze 08:44 episode as part of the forward score — it is excluded by the protocol.

If the tick lane has no data newer than 2 hours, say so once (the lane may be down) instead of reporting an empty ledger as a result.
