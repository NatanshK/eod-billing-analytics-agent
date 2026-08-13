# Sample Billing Data — Mehta Multi-Specialty Clinic (CLN-KNP-014)

Three clinic-days, one file each. All three should run through the same pipeline —
don't assume anything about a day beyond what its file actually contains.

| File | Date |
|---|---|
| `billing_log_2026-07-27.json` | 27 Jul 2026 |
| `billing_log_2026-07-26.json` | 26 Jul 2026 |
| `billing_log_2026-07-25.json` | 25 Jul 2026 |

Each file is a JSON array of visit records matching the schema in the assignment brief:

```
clinic_id            string
visit_id             string
timestamp            ISO 8601, UTC
doctor_id            string
line_items           array of { drug_name, qty, unit_price_paise }
payment_mode         "cash" | "card" | "upi"
amount_paid_paise    integer
discount_paise       integer
is_refund            boolean
```

Notes:
- Amounts are integer paise throughout.
- A refund (`is_refund: true`) represents money going back out for a previous sale —
  `amount_paid_paise` on a refund row is negative.
- Not every row in every file is guaranteed to be well-formed. Handle that however
  you think a production ingestion endpoint should.
