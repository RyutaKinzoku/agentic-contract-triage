# Sample Contracts

Three demo contracts, each engineered to exercise a different path through the
pipeline against the shipped knowledge base. These are the files used in the
screen recording.

| File | Format | Designed outcome | What it exercises |
|------|--------|------------------|-------------------|
| `contract_1_compliant_nda.pdf` | Digital PDF | `auto_approve` | Clean extraction; known client; every term within policy |
| `contract_2_violations_scan.jpg` | Scanned image | `needs_review` | Vision/OCR on a skewed scan; unknown counterparty; multiple policy violations |
| `contract_3_incomplete_letter.pdf` | Digital PDF | `needs_review` | Missing critical fields handled gracefully, not guessed |

## 1. Compliant NDA — `auto_approve`
Counterparty **Acme Corporation Ltd** (in the known-clients register). Governing
law England and Wales, liability capped at 12 months' fees, auto-renewal with 30
days' notice, Net-30, 60-day termination, 3-year confidentiality — all within
policy. Expected: clean approval, written to the Approved sheet.

## 2. Master Services Agreement (scanned) — `needs_review`
A deliberately skewed, grayscale, noisy JPEG to test the multimodal vision path.
Counterparty **Zenith Dynamics Pty Ltd** is **not** a known client, and the
contract violates several policies at once:
- Liability is **unlimited** (must be capped).
- Auto-renewal notice is **90 days** (max 60).
- Payment is **Net-90** (max Net-60).
- Governing law is **New South Wales, Australia** (not an approved jurisdiction).

Expected: flagged for review with each reason and the triggering policy listed.

## 3. Incomplete Letter of Engagement — `needs_review`
A sparse letter with **no named counterparty, no effective date, and no
governing law**. Tests that missing critical fields route to human review rather
than being approved on guesses or causing an error.

Expected: flagged for review citing the missing required fields.
