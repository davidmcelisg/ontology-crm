# Demo

Mock network:

1. `python3 tests/test_ontology_driven.py`
2. `python3 -m crm.loader ontology/ontology.yaml`
3. `python3 scripts/prove_data_driven.py`
4. `python3 scripts/seed_sample.py`
5. `python3 scripts/demo.py`
6. `python3 scripts/try_ingest.py`

Real network:

7. `python3 scripts/seed_real.py --dry-run`
8. `python3 scripts/demo.py --claims data/claims.jsonl`
9. `python3 scripts/live_ingest.py --claims data/claims.jsonl "Coffee with Adrian, he says Michael will run my Paraform interview next week and I promised to send him my portfolio."`
10. `python3 scripts/note.py --claims data/claims.jsonl "Coffee with Adrian, he says Michael will run my Paraform interview next week and I promised to send him my portfolio."`
11. `grep -c "" data/claims.jsonl && tail -1 data/claims.jsonl`
12. `python3 scripts/demo.py --claims data/claims.jsonl --org org:primero`

Details for each command are in `CALLS.md`. Steps 9 and 10 are the only ones that
call the Anthropic API (~$0.03 each on Sonnet 5), and step 10 is the only one that
writes — it asks `y/N` first. Everything else is free and offline.
