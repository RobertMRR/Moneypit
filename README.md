# Moneypit

Local-first personal finance dashboard. Your bank data stays on your machine.

## What it does (v0.1)

- Imports bank CSVs (Pekao supported; ING and mBank stubbed)
- Categorizes transactions with a rules engine
- Detects recurring subscriptions
- Dashboard: spending by category, top vendors, recurring charges

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -e .
python -m moneypit
```

Then open http://127.0.0.1:8000

## Import a CSV

Drop your bank export into `./data/` and click "Import" in the UI,
or POST the file to `/import`.

## Roadmap

- [x] Bank CSV import (Pekao)
- [ ] ING + mBank CSV parsers
- [ ] Gmail subscription scanner
- [ ] Receipt parser (vision model)
- [ ] Docker image for self-hosting
