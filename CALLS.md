# Command reference

Two networks, one system. The **mock** network is invented and committed
(`data/sample_claims.jsonl`). The **real** is original and gitignored
(`data/claims.jsonl`, built from `data/network.local.yaml`). Every script
below runs on either; only the `--claims` flag changes.

Cost note: only the two commands marked **$** call the Anthropic API
(~$0.03 per note on Sonnet 5). Everything else is free and offline.

## 0. Setup (once)

```bash
pip install -r requirements.txt
```

```bash
python3 tests/test_ontology_driven.py
```

Expect `10/10 passing`. Each test is one design argument you can point at.

## 1. Mock demo

Build the fake network, then ask it questions.

```bash
python3 scripts/seed_sample.py
```

```bash
python3 scripts/demo.py
```

Show that ingestion turns text into validated actions, with a canned model
response so no key is needed:

```bash
python3 scripts/try_ingest.py
```

**$** Same thing against a real model. Dry-run: proposes, never writes. Runs
six name-resolution probes and reports whether the model reused roster ids
or invented duplicates.

```bash
python3 scripts/live_ingest.py
```

## 2. Real demo

`data/network.local.yaml` is your contact list. Validate it, then build the
claims file. `--rebuild` replaces an existing `claims.jsonl` (including
anything ingested since), so re-seed only when you edited the YAML.

```bash
python3 scripts/seed_real.py --dry-run
```

```bash
python3 scripts/seed_real.py --rebuild
```

Same questions, your answers:

```bash
python3 scripts/demo.py --claims data/claims.jsonl
```

`--org` picks which organization the "who do I know at" question asks about.
Without it the demo picks the org with the most contacts, preferring one with a
parent so the subsidiary roll-up has something to show:

```bash
python3 scripts/demo.py --claims data/claims.jsonl --org org:primero
```

**$** Plain text in, proposed actions out, against your real roster. Still
dry-run. Use a note about people who are actually in the file:

```bash
python3 scripts/live_ingest.py --claims data/claims.jsonl "Coffee with Adrian, he says Michael will run my Paraform interview next week and I promised to send him my portfolio."
```

**$** The full loop: propose, show, ask `y/N`, write. This is the only
script that lets a model's output into `claims.jsonl`, and only after you
say yes. Run `demo.py` again afterwards to see the answers change.

```bash
python3 scripts/note.py --claims data/claims.jsonl "Coffee with Adrian, he says Michael will run my Paraform interview next week and I promised to send him my portfolio."
```

```bash
python3 scripts/demo.py --claims data/claims.jsonl
```

Undo: the claim log is append-only, so there is no delete. To start over
from the YAML, `python3 scripts/seed_real.py --rebuild` (drops every note
ingested since). Add `--model claude-opus-5` to either `$` script to compare
models.

## 3. Things worth showing on screen

```bash
python3 -m crm.loader ontology/ontology.yaml
```

The registry as the loader sees it: types, derived links, actions. Pairs with
the line in `demo.py` output: *8 object types, 20 links derived from ref
attributes, 13 actions*, none of them named in Python.

```bash
grep -c "" data/claims.jsonl && tail -1 data/claims.jsonl
```

One line per claim, append-only. Every line carries `source_kind`,
`asserted_at` and `supersedes`, plus both read paths side by side: `changes` is
the delta that claim asserted, `body` is the snapshot after merging it.

The headline claim, checked rather than asserted. It reads the type names from
the ontology and parses every module in `crm/`, skipping strings and comments:

```bash
python3 scripts/prove_data_driven.py
```

## Order for a screen share

1. `tests/`: four design arguments, ten assertions, all green.
1b. `prove_data_driven.py`: 0 uses of any domain type as executable code.
2. `demo.py` on the mock: the questions the system answers.
3. `demo.py --claims data/claims.jsonl`: the same questions, real answers
   (*Alan owes me a reply*, *who do I know at Adyen* rolling up through Orb,
   and the provenance chain on whichever object has the richest claim history).
4. `try_ingest.py`: text becomes actions; nothing written until confirmed.
5. **$** `note.py --claims data/claims.jsonl "..."`: the real model resolving
   real names, you say `y`, then `demo.py` again shows the new answer.

## What each file is

| File | Role |
|---|---|
| `ontology/ontology.yaml` | the schema: types, links, actions. Data, not code |
| `data/network.example.yaml` | format for writing a network by hand (fake names) |
| `data/network.local.yaml` | your real network, gitignored |
| `data/sample_claims.jsonl` | mock claim log, committed |
| `data/claims.jsonl` | real claim log, gitignored |
| `scripts/prove_data_driven.py` | proves no domain type is named in `crm/` code |
| `scripts/seed_sample.py` | writes the mock claim log |
| `scripts/seed_real.py` | YAML → real claim log, through the validated action path |
| `scripts/demo.py` | the questions; `--claims` picks the network, `--org` the org |
| `scripts/try_ingest.py` | ingestion with a stubbed model, free |
| `scripts/live_ingest.py` | ingestion with Sonnet 5, dry-run, costs cents |
| `scripts/note.py` | ingestion with Sonnet 5, confirm, **writes**; costs cents |

Check if auth key is set:
```bash
echo ${ANTHROPIC_API_KEY:+set}
```