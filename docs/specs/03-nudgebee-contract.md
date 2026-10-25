# 03 — Nudge Bee contract

## Shape

Nudge Bee is a CloudOps gateway; its own runbook server is Temporal-based, so the
integration is a JSON POST, not an SDK. The adapter lives entirely in
`notify/nudgebee.py` behind the `Notifier` protocol, which means a change in their
endpoint shape touches one file.

```
NUDGEBEE_MODE=outbox    → out/notifications.jsonl      (default, cannot fail)
NUDGEBEE_MODE=slack     → SLACK_WEBHOOK_URL
NUDGEBEE_MODE=webhook   → NUDGEBEE_WEBHOOK_URL
```

Transport selection is env-driven, so the demo path and the integration path are the
same code.

## Request

`POST {NUDGEBEE_WEBHOOK_URL}` · `content-type: application/json`

```json
{
  "source": "geo-compliance-agent",
  "repo": "payments-api",
  "score": 25,
  "verdict": "CRITICAL EXPOSURE",
  "events": [
    {
      "event_id": "9f2c1d7a4b8e5c3a1f0d2b6e",
      "type": "compliance.defect",
      "severity": "high",
      "location": "src/auth.py:9",
      "evidence": "logger.info(f\"issued session token={token}\")",
      "suggested_fix": "logger.info(\"issued session token for user %s\", user[\"id\"])",
      "obligations": [
        {
          "framework": "DPDP",
          "control": "S.8(5)",
          "control_id": "DPDP-S8(5)-secrets-in-logs",
          "title": "Reasonable security safeguards - personal data must not be exposed in logs",
          "exposure": "Up to INR 250 crore (Schedule, breach of security safeguards)"
        },
        {
          "framework": "GDPR",
          "control": "Art. 32",
          "control_id": "GDPR-Art32-secrets-in-logs",
          "title": "Personal data and credentials must not be written to application logs",
          "exposure": "Up to EUR 20M or 4% of global annual turnover (Art. 83(5))"
        }
      ],
      "audit": {
        "audit_id": "50370cdcc148",
        "score": 25,
        "verdict": "CRITICAL EXPOSURE",
        "report": "out/report-50370cdcc148.md"
      },
      "dedupe_key": "9a1c...:payments-api"
    }
  ]
}
```

## Guarantees the consumer can rely on

| Property | Consequence for the receiver |
|---|---|
| `event_id` is deterministic (`sha256(defect_id:repo)`) | safe to upsert; retries won't open duplicate tickets |
| One event per defect, carrying every obligation | a GDPR+DPDP line opens one ticket, not two |
| Events sorted high → medium → low | a chat preview truncates to the worst five and stays useful |
| `evidence` is verbatim source | can be rendered in a code block without re-fetching the repo |
| `suggested_fix` may be empty | fall back to `exposure` text; never render an empty diff |
| Payload is replay-safe | `maximum_attempts=2` on the activity; duplicates are the consumer's to absorb by `event_id` |

## Not included, on purpose

- **No repository credentials or clone URLs.** The receiver never needs repo access.
- **No full file contents.** A finding carries the offending line, not the file.
- **No raw model output.** Everything here passed the validation gate; whatever the
  model said that failed is not transmitted.

## Slack rendering

`notify/slack.py` renders the top five defects as one section block: severity,
`path:line`, every obligation, the evidence in a code block, and the proposed
replacement. It posts once per audit, not once per defect — an alerting
integration that pages five times for one commit gets muted on day two.

## Verification

```bash
NUDGEBEE_MODE=outbox python -m workflows.start --path fixtures/bad
cat out/notifications.jsonl | python3 -m json.tool | head -30
```

Against a real endpoint, the demo should assert on the response status and the
`delivered` count returned by `send()`.
