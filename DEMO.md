# Demo

Mock network:

1. `python3 tests/test_ontology_driven.py`
2. `python3 scripts/seed_sample.py`
3. `python3 scripts/demo.py`
4. `python3 scripts/try_ingest.py`

Real network:

5. `python3 scripts/seed_real.py --dry-run`
6. `python3 scripts/demo.py --claims data/claims.jsonl`
7. `python3 scripts/note.py --claims data/claims.jsonl "Coffee with Adrian, he says Michael will run my Paraform interview next week and I promised to send him my portfolio."`
8. `python3 scripts/demo.py --claims data/claims.jsonl`

Details for each command are in `CALLS.md`.
