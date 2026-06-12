# n8n Workflow Setup

This guide wires the orchestration layer: a Gmail inbox triggers on new
attachments, the agent's `/triage` endpoint does the work, and the result is
routed to Google Sheets with a notification on review.

`n8n/workflow.json` is an **importable scaffold**. Credentials are never part of
an n8n export, so after importing you connect your own Gmail and Google Sheets
credentials in the UI. Once it runs end to end, re-export the workflow
(`⋯ menu → Download`) and commit that file — that becomes the authoritative
export.

## Flow

```
Gmail Trigger ──▶ Call Agent /triage ──▶ Switch on Outcome
                                          ├─ auto_approve ─▶ Approved Row ─▶ Append: Approved
                                          ├─ needs_review ─▶ Review Row    ─▶ Append: Needs Review ─▶ Notify Reviewer
                                          └─ quarantine   ─▶ Quarantine Row ─▶ Append: Needs Review ─▶ Notify Reviewer
```

## Prerequisites

1. The stack is running (`docker compose up -d`) and the knowledge base is
   ingested (`docker compose exec agent python -m scripts.ingest_kb`). The agent
   must be reachable for n8n to call it.
2. A dedicated **Gmail** account for intake.
3. A **Google Sheet** with two tabs, each with a header row:
   - `Approved`: `Timestamp | Counterparty | Contract Type | Effective Date | Governing Law | Liability Cap | Outcome | Email Subject | From`
   - `Needs Review`: `Timestamp | Counterparty | Contract Type | Outcome | Reasons | Email Subject | From`
4. Google OAuth credentials configured in n8n for both **Gmail** and **Google
   Sheets** (n8n → Credentials → New).

## Import

1. Open n8n at http://localhost:5678.
2. `⋯ menu → Import from File` and choose `n8n/workflow.json`.
3. The canvas shows the full flow. Several nodes will show a credential or
   parameter warning until you complete the steps below.

## Node-by-node configuration

### Gmail Trigger
- Set your **Gmail credential**.
- `Download Attachments` is on, so each email's first attachment is exposed as
  the binary property `attachment_0`.
- Confirm the field names by running the trigger once (`Execute Node`) and
  inspecting the output. If your build names the subject/sender differently from
  `subject` / `from`, update the expressions in the three Row nodes to match.

### Call Agent /triage
- URL is `http://agent:8000/triage` — the agent's service name on the Docker
  network. (If you run n8n outside Docker, use `http://localhost:8000/triage`.)
- Body is `multipart/form-data` with one **Form Binary Data** field named `file`,
  reading from `attachment_0`. This matches the agent's expected upload field.
- No credential needed; the call is internal.

### Switch on Outcome
- Three outputs keyed on `{{ $json.outcome }}`: `auto_approve`, `needs_review`,
  `quarantine`. These are the exact strings the agent returns.

### Row nodes (Approved / Review / Quarantine)
- These shape the triage JSON into flat columns whose names match the sheet
  headers, so the Sheets nodes can auto-map. Adjust if you change headers.

### Append: Approved / Append: Needs Review
- Set your **Google Sheets credential**.
- Replace `YOUR_GOOGLE_SHEET_ID` with your spreadsheet ID (from its URL).
- Mapping is `Auto-map input data`, so the Row node's field names must equal the
  sheet headers.

### Notify Reviewer
- Set your **Gmail credential** and change `reviewer@example.com` to the real
  recipient. Optional — disable the node if you don't want notifications.

## Test

1. Send an email to the intake inbox with a contract PDF attached.
2. Within a minute the workflow runs (or click `Execute Workflow` to run now).
3. A compliant contract from a known client lands in **Approved**; a contract
   with a policy violation or unknown counterparty lands in **Needs Review** with
   the reasons filled in; an unreadable file is recorded as **quarantine**.

## Notes

- **Multiple attachments:** the scaffold processes `attachment_0` (the first
  attachment). To handle several, loop over the `attachment_*` properties.
- **Re-export after it works:** the committed `workflow.json` should be the one
  exported from your running instance, so node versions match your n8n.
- **Security:** credentials live only in n8n, never in the exported JSON. The
  agent URL is internal to the Docker network and is not exposed publicly.