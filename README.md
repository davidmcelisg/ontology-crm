# Ontology-driven personal CRM

## The problem

Managing my personal network during a job search. I wanted one place that:

- maps every interaction, referral and commitment
- answers questions over them: who owes me a reply, what did I promise, who do I know at a company
- takes new entries as plain text, so logging a coffee is one sentence and not a form

## What it is

A personal CRM where the type system lives in a YAML file instead of in the
code. `ontology/ontology.yaml` declares what kinds of things exist, how they may
connect, and what changes are legal. Every component reads that file at startup
and builds a registry. No module in `crm/` mentions `Person`, `Pursuit` or any
other domain type as executable code.

Underneath it is an append-only JSONL claim log. Objects are never updated in
place. A change is a new claim that supersedes an old one, carrying who told me
and when. Nothing derived is stored: every function is a fold over that log,
computed when you ask.

The payoff of the design is a single test: add an object type to the yaml,
restart, and validation, storage, graph traversal and the JSON schemas handed to
the model all handle it with no code change.

The model cannot corrupt the graph, because Actions are the only write path and
their schemas are generated from the ontology. It can only emit action names and
parameters that the registry declares. Anything else is rejected by the
validator before a claim is written, and a batch containing one invalid action
writes nothing at all.

**8 object types · 20 links derived automatically · 13 actions · 8 functions**

## Architecture

```
            ontology/ontology.yaml
            object types · ref attributes · actions · functions
                      │
                      │  loader parses it, validates it,
                      │  derives the link registry
                      ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │  REGISTRY   built once, at startup                                    │
   │  type registry  ·  link registry (derived)  ·  action definitions     │
   └───────────────────────────────────────────────────────────────────────┘
                      ╎
                      ╎  everything below reads the registry.
                      ╎  nothing below ever reads the yaml.
                      ╎
   ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┴╌╌  WRITE PATH  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌

     note ──▶ propose ──▶ model ──▶ validator ──▶ confirm ──▶ execute
              schemas +   returns   dry run       y/N         the only
              roster      JSON                                write path
                                                                  │
   ┌──────────────────────────────────────────────────────────────▼────────┐
   │  CLAIM LOG   append-only JSONL, one claim per line                    │
   │  nothing is ever updated or deleted                                   │
   └───────────────────────────────────────────────────────────────────────┘
                      │
   ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┴╌╌  READ PATH  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌

     resolve ──▶ traverse ──▶ functions ──▶ answers
     two clocks  walks the    folded on read,
                 link registry never stored
```

The dotted line is the claim worth checking. Everything below it works in terms
of "whatever types the registry declares," never in terms of `Person` or
`Organization`. `scripts/prove_data_driven.py` parses every module in `crm/`
looking for a domain type used as executable code, and finds none.

## Four design decisions

Everything else follows from these.

- **Thin objects, fat relationships:** anything you would qualify with a date, a
  source or an end gets its own type. `Person` holds almost nothing, because
  almost nothing about a person is permanently true.
- **Never store a state that can be derived:** "owes me a reply" is not a field,
  it is a function over interactions. A stored status drifts; a derived one
  cannot.
- **Every assertion is a claim, not a fact:** network data is hearsay with an
  expiry date. Storage is append-only and current state is a fold over the log.
- **Scope grows by adding data, not types:** family is not a type, it is a
  `Relationship` with `kind: family`. Needing a new object type to cover a new
  part of life means the model is wrong.

## Object types

- **Person:** identity only. No employer, no title, no history.
- **Organization:** has an optional parent, so an office or a subsidiary needs no
  new type, and "who do I know at Adyen" rolls up through Orb.
- **Affiliation:** reified Person ↔ Organization, because employment has a
  history and not just a present.
- **Relationship:** reified and *directed* Person → Person. Ties are asymmetric
  in reality; a symmetric model halves the storage and lies.
- **Interaction:** the atom. `in_reply_to` makes threads a chain rather than a
  guess, which is what makes "who owes me a reply" computable.
- **Introduction:** three participants, so it cannot be an edge under any
  scheme. The cleanest argument for reification in the model.
- **Pursuit:** any directed effort toward an outcome, so job applications live
  here. No stage-history table: claims are timestamped, so history is free.
- **Commitment:** who owes what to whom. Symmetric machinery answers both "what
  did I promise and drop" and "what am I owed."

Attribute-level detail is in [`ontology/ontology.md`](ontology/ontology.md).

## Links

Nothing below is written by hand. The loader derives one row per `ref` attribute,
which is what makes the link registry impossible to desync from the types.

| Link | From | To | Cardinality |
|---|---|---|---|
| `affiliation_at` | Affiliation | Organization | many-to-one |
| `affiliation_of` | Affiliation | Person | many-to-one |
| `commitment_created_in` | Commitment | Interaction | many-to-one |
| `commitment_fulfilled_by` | Commitment | Interaction | many-to-one |
| `commitment_obligee` | Commitment | Person | many-to-one |
| `commitment_obligor` | Commitment | Person | many-to-one |
| `interaction_about` | Interaction | Pursuit \| Organization | many-to-many |
| `interaction_participant` | Interaction | Person | many-to-many |
| `interaction_reply_to` | Interaction | Interaction | many-to-one |
| `intro_introducer` | Introduction | Person | many-to-one |
| `intro_party_a` | Introduction | Person | many-to-one |
| `intro_party_b` | Introduction | Person | many-to-one |
| `intro_result` | Introduction | Interaction | many-to-one |
| `org_parent` | Organization | Organization | many-to-one |
| `pursuit_origin` | Pursuit | Introduction | many-to-one |
| `pursuit_referrer` | Pursuit | Person | many-to-one |
| `pursuit_target` | Pursuit | Organization | many-to-one |
| `relationship_from` | Relationship | Person | many-to-one |
| `relationship_origin` | Relationship | Interaction | many-to-one |
| `relationship_to` | Relationship | Person | many-to-one |

## Actions

The only permitted mutations, and the only vocabulary the model is given. Every
action also accepts the claim metadata: `asserted_at`, `valid_from`, `valid_to`,
`source_kind`, `source_person`, `source_note`.

| Action | Effect | Required | Optional |
|---|---|---|---|
| `CreatePerson` | creates Person | display_name | given_name, family_name, aliases, based_in, notes |
| `CreateOrganization` | creates Organization | name, kind | location, parent, notes |
| `UpdateOrganization` | updates Organization | organization | name, kind, location, parent, notes |
| `AssertAffiliation` | creates Affiliation | person, organization, kind | role_title, seniority, start_date, end_date |
| `EndAffiliation` | updates Affiliation | affiliation, end_date | (none) |
| `AssertRelationship` | creates Relationship | from_person, to_person, kind | strength, origin_context, origin_interaction |
| `RecordInteraction` | creates Interaction | occurred_at, channel, direction, participants | subject, summary, expects_response, in_reply_to, about |
| `RecordIntroduction` | creates Introduction | introducer, introduced_a, introduced_b, occurred_at | context, resulting_interaction |
| `OpenPursuit` | creates Pursuit | kind, target_organization, stage, outcome, opened_at | target_role, closed_at, referred_by, originated_in |
| `AdvancePursuit` | updates Pursuit | pursuit, stage | outcome, target_role |
| `MakeCommitment` | creates Commitment | obligor, obligee, description | created_in, due_date, fulfilled_by |
| `FulfillCommitment` | updates Commitment | commitment, fulfilled_by | (none) |
| `MergePersons` | entity resolution | keep, merge | (none) |

Only one action needed hand-written code. Creates mint an id from the title
attribute; updates find the ref parameter pointing at the type being updated and
treat the rest as the delta; `MergePersons` is the one special case.

## Functions

Derived answers. Nothing here is stored, and nothing here is cached.

| Function | Question it answers |
|---|---|
| `open_threads()` | Who owes me a reply, and who am I ignoring |
| `outstanding_commitments(side)` | What I promised and dropped / what I'm owed |
| `who_do_i_know_at(org)` | Contacts at an org or its subsidiaries, ranked by strength |
| `path_to(target)` | Shortest route to a person or org, through any object type |
| `pursuit_board()` | Every live pursuit, its stage, and whether I'm waiting on someone |
| `going_stale(months)` | People I claim to be close to and have not spoken with |
| `why_do_i_believe(object)` | The full claim chain, with sources and dates |
| `reciprocity(person)` | Introductions and favours exchanged in each direction |

## Run it

```bash
pip install -r requirements.txt
```

The questions, answered from the claim log:

```bash
python3 scripts/demo.py
```

The headline claim, checked rather than asserted. Every module in `crm/` gets
parsed, looking for a domain type used as executable code:

```bash
python3 scripts/prove_data_driven.py
```

A note becoming validated actions, with a canned model response so no API key is
needed:

```bash
python3 scripts/try_ingest.py
```

The design arguments as runnable assertions:

```bash
python3 tests/test_ontology_driven.py
```

`DEMO.md` has the full command list and `CALLS.md` explains what each one does,
including running against your own network and the two scripts that call a real
model.

## Layout

    ontology/ontology.yaml   the source of truth. Data, not code
    ontology/ontology.md     attribute-level detail and design rationale
    crm/loader.py            reads the yaml, validates it, derives the link registry
    crm/registry.py          in-memory type system. Knows nothing about YAML
    crm/validator.py         checks data against whatever the registry says
    crm/store.py             append-only claim log, bitemporal resolution
    crm/query.py             generic graph traversal over the link registry
    crm/functions.py         the named questions. The only file with domain judgement
    crm/actions.py           executes the declared actions. The only write path
    crm/ingest.py            plain text -> proposed actions, via an injected model
    scripts/                 seeders, the demo, and the ingestion runners
