# Moneypit

Local-first personal finance dashboard. Your bank data stays on your machine.

## Features

- **Bank CSV import** — auto-detects Pekao (ING and mBank stubbed)
- **Receipt scanning** — snap a photo, Claude Vision extracts the transaction (requires `ANTHROPIC_API_KEY`)
- **Rules engine** — auto-categorizes transactions by pattern matching; create and edit rules from the UI
- **Recurring detection** — flags subscriptions and repeating charges
- **Dashboard** — spending by category, top vendors, recurring charges, income vs. spend totals
- **Transactions browser** — search, filter by category/profile/date/direction, sort, paginate
- **Profiles** — separate budgets (e.g. personal vs. business) with per-profile filtering
- **Settings** — manage profiles, change password, set default currency, export CSV, delete data
- **Auth** — email/password registration, session-based login with cookie auth
- **Light/dark mode**

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -e .
python -m moneypit
```

Then open http://127.0.0.1:8000

For receipt scanning, set `ANTHROPIC_API_KEY` in your environment before starting.

## Import data

- **CSV** — drop your bank export and click "Import" in the UI, or use the `/import` page
- **Receipt** — upload a photo/PDF of a receipt; review the extracted data before saving
- **Manual** — add transactions by hand from the import page

## Roadmap

- [x] Bank CSV import (Pekao)
- [x] Receipt parser (Claude Vision)
- [x] Rules engine with UI management
- [x] Multi-profile support
- [x] User auth (register / login / logout)
- [x] Settings page (password, currency, export, account deletion)
- [ ] ING + mBank CSV parsers
- [ ] Gmail subscription scanner
- [ ] Docker image for self-hosting
