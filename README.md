# Ontology-driven personal CRM

A personal CRM whose type system is data rather than code.

`ontology/ontology.yaml` declares what kinds of things exist, how they may
relate, and what mutations are legal. Every component reads that file at
startup. No Python module in `crm/` mentions `Person`, `Affiliation`, or any
other domain type by name.

The test of the claim: add an object type to the yaml, restart, and the
validator, the store, the graph traversal and the generated action schemas all
handle it with no code change.

## Run it

    pip install -r requirements.txt
    python -m crm.loader ontology/ontology.yaml    # inspect the ontology
    python scripts/seed_sample.py                  # build a fake network
    python scripts/demo.py                         # answer real questions
    python tests/test_ontology_driven.py           # the design arguments

To run it on your own network, and for the ingestion scripts, see `DEMO.md` (the commands) and `CALLS.md` (what each one does).

## Layout

    ontology/ontology.md     design rationale -- why the model is shaped this way
    ontology/ontology.yaml   the source of truth. Data, not code.
    crm/loader.py            reads the yaml, validates it, derives the link registry
    crm/registry.py          in-memory type system. Knows nothing about YAML.
    crm/validator.py         checks and parses data against whatever the registry says
    crm/store.py             append-only claim log with bitemporal resolution
    crm/query.py             generic graph traversal over the link registry
    crm/functions.py         the named questions. The only file with domain judgement.
    crm/actions.py           executes the declared actions. The only write path.
    crm/ingest.py            plain text -> proposed actions, via an injected model
    scripts/                 seeders, the demo, and the ingestion runners (see CALLS.md)
    data/                    claim logs. Real ones are gitignored.

## Four design decisions

**Relationships are objects.** "Ana works at Bain" is an `Affiliation` with
dates and a source, not an edge. Anything you want to qualify has to be a
thing. An `Introduction` has three participants and cannot be an edge at all.

**Everything is a claim.** You do not know where Ana works; you know Isa told
you in March. Every record carries provenance and two clocks -- when it was
true in the world, and when you learned it. Nothing is ever updated in place.

**Derived state is never stored.** There is no `awaiting_reply` field. "Who
owes me a reply" is computed from the interaction chain every time, so it
cannot drift out of sync with reality.

**Actions are the only mutation.** Thirteen declared, typed, validated. Their
schemas are generated from the ontology, which is what will let an LLM write to
the graph without being able to corrupt it.

## Known limitations

See section 8 of `ontology/ontology.md`. Each cut has a reason attached.
