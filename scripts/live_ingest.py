"""Runs the ingestion pipeline against a real model, dry-run only.

Never calls ingest.apply -- this only proposes and inspects, so it is safe to
run against the real sample store with a real API key. Swap try_ingest.py's
stub for ingest.anthropic_completer() and you get this script.

Usage:
    python scripts/live_ingest.py                 # runs the built-in probes
    python scripts/live_ingest.py "some note"      # runs one note
    python scripts/live_ingest.py --model X "note"
    python scripts/live_ingest.py --claims data/claims.jsonl "note"   # real network
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import ClaimStore, ingest, load_ontology
from crm.actions import slugify

TODAY = "2026-09-15"
NAMED_TYPES = {"Person", "Organization"}

# Each probe is (label, note, what it tests).
PROBES = [
    (
        "a-first-name-only-known-person",
        "Had lunch with Rosa today, she's thinking of leaving Cardinal.",
        "must reuse person:rosa, org:cardinal-mx or org:cardinal, not create a new Rosa",
    ),
    (
        "b-two-known-people-by-first-name",
        "Teo says Nayeli is now running hiring for the whole Northwind eng team.",
        "must reuse person:teo, person:nayeli, org:northwind",
    ),
    (
        "c-new-person-existing-orgs",
        "Met Marco Ruiz, a PM at Cardinal, at a HackDay event. He offered to "
        "review my portfolio next week.",
        "exactly one create for Marco; Cardinal and HackDay by existing id",
    ),
    (
        "d-new-person-mentioned-twice",
        "Coffee with Lucia Ferrer from Northwind. Lucia said she'd send me the "
        "job spec by Friday.",
        "one create for Lucia, one commitment, no duplicate",
    ),
    (
        "e-known-person-by-role-not-name",
        "Northwind's recruiter pinged me, the second round is scheduled for "
        "next Tuesday.",
        "should resolve to person:nayeli via roster linked context",
    ),
    (
        "f-ambiguity-trap",
        "Talked to Ivette about the CTO round; she said the other Ivette on "
        "the panel is a contractor.",
        "first Ivette must be person:ivette; second is a legitimate new person",
    ),
]


def _existing_titles_by_type(store):
    """type_name -> {object_id: title}, snapshotted once before any probe."""
    titles = {}
    for type_name in store.registry.type_names():
        objects = store.all_of_type(type_name)
        if not objects:
            continue
        titles[type_name] = {
            object_id: ingest.query.title(store, object_id) for object_id in objects
        }
    return titles


def _looks_like_id(store, value):
    """Prefix must be one the registry mints, so timestamps do not match."""
    if not isinstance(value, str) or ":" not in value or " " in value:
        return False
    prefix = value.split(":", 1)[0]
    return prefix in {store.registry.type(t).prefix for t in store.registry.type_names()}


def _check_duplicates_and_dangling(store, proposal, existing_titles):
    """Returns (suspects, dangling) as lists of printable strings.

    A create is flagged as a suspect duplicate when its minted id's slug
    matches an existing object of the same type, or, for named entities
    (Person, Organization), when a token (len>2) of its title matches a token
    of an existing title. Interactions and commitments share ordinary words
    all the time, so they only get the slug check. A ref-shaped
    param value is flagged as dangling when it names neither a pre-existing
    object nor one created earlier in this same proposal.
    """
    suspects = []
    dangling = []
    created_ids_this_proposal = set()

    for proposed in proposal.actions:
        if not store.registry.has_action(proposed.name):
            continue
        action = store.registry.action(proposed.name)
        type_name = action.creates

        if type_name:
            object_type = store.registry.type(type_name)
            title_attribute = object_type.title_attribute
            new_title = proposed.params.get(title_attribute) if title_attribute else None
            if isinstance(new_title, str) and new_title:
                new_slug = slugify(new_title)
                new_tokens = {t for t in new_slug.split("-") if len(t) > 2}
                for existing_id, existing_title in existing_titles.get(type_name, {}).items():
                    existing_slug = slugify(existing_title)
                    if new_slug == existing_slug:
                        suspects.append(
                            f"SUSPECT DUPLICATE: {new_title!r} ~ {existing_id} "
                            f"({existing_title!r}) [slug match]"
                        )
                        continue
                    if type_name not in NAMED_TYPES:
                        continue
                    existing_tokens = {t for t in existing_slug.split("-") if len(t) > 2}
                    overlap = new_tokens & existing_tokens
                    if overlap:
                        suspects.append(
                            f"SUSPECT DUPLICATE: {new_title!r} ~ {existing_id} "
                            f"({existing_title!r}) [token overlap: {sorted(overlap)}]"
                        )
                minted = f"{object_type.prefix}:{new_slug}"
                created_ids_this_proposal.add(minted)

        for name, value in proposed.params.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if not _looks_like_id(store, item):
                    continue
                if store.exists(item):
                    continue
                if item in created_ids_this_proposal:
                    continue
                dangling.append(f"DANGLING REF: {name}={item!r} in {proposed.name}")

    return suspects, dangling


def run_probe(store, complete, label, note, checks):
    print(f"\n{'=' * 70}\nprobe: {label}\nnote: {note!r}\nchecks: {checks}\n{'-' * 70}")

    existing_titles = _existing_titles_by_type(store)
    proposal = ingest.propose(store, note, complete, today=TODAY)

    if not proposal.actions:
        print("PARSE FAILURE -- raw response:")
        print(proposal.raw)
        return {"label": label, "reused": 0, "created": 0, "suspects": 0,
                "dangling": 0, "valid": False}

    print(ingest.render(store, proposal))

    usage = getattr(complete, "last_usage", None)
    if usage:
        print(f"\nusage: {usage['input_tokens']} in / {usage['output_tokens']} out")

    print(f"valid: {proposal.valid}")

    suspects, dangling = _check_duplicates_and_dangling(store, proposal, existing_titles)
    for line in suspects:
        print(line)
    for line in dangling:
        print(line)

    reused = 0
    created = 0
    for proposed in proposal.actions:
        if not proposed.valid:
            continue
        action = store.registry.action(proposed.name) if store.registry.has_action(proposed.name) else None
        if action and action.creates:
            created += 1
        for value in proposed.params.values():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if _looks_like_id(store, item) and store.exists(item):
                    reused += 1

    return {
        "label": label,
        "reused": reused,
        "created": created,
        "suspects": len(suspects),
        "dangling": len(dangling),
        "valid": proposal.valid,
        "usage": usage,
    }


def main():
    args = list(sys.argv[1:])
    model = "claude-sonnet-5"
    claims = "data/sample_claims.jsonl"
    if "--model" in args:
        i = args.index("--model")
        model = args[i + 1]
        del args[i:i + 2]
    if "--claims" in args:
        i = args.index("--claims")
        claims = args[i + 1]
        del args[i:i + 2]
    note = args[0] if args else None

    if note is None and claims != "data/sample_claims.jsonl":
        print("The built-in probes name people from the sample network; "
              "pass a note of your own when using --claims.")
        return 1

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Export it before running this script: "
              "export ANTHROPIC_API_KEY=sk-...")
        return 1
    try:
        import anthropic
    except ImportError:
        print("The anthropic package is not installed. Run: pip install anthropic")
        return 1

    store = ClaimStore(load_ontology("ontology/ontology.yaml"), claims)
    complete = ingest.anthropic_completer(model=model)

    results = []
    try:
        if note:
            results.append(run_probe(store, complete, "cli-note", note, "ad hoc"))
        else:
            for label, probe_note, checks in PROBES:
                results.append(run_probe(store, complete, label, probe_note, checks))
    except anthropic.RateLimitError as error:
        print(f"\nRate limited by the Anthropic API: {error}")
        return 1
    except anthropic.APIConnectionError as error:
        print(f"\nCould not reach the Anthropic API: {error}")
        return 1
    except anthropic.APIStatusError as error:
        print(f"\nAnthropic API returned an error status: {error}")
        return 1

    print(f"\n{'=' * 70}\nsummary\n{'-' * 70}")
    print(f"{'probe':<32} {'reused':>6} {'created':>7} {'suspect':>7} {'dangling':>8} {'valid':>6}")
    total_in = total_out = 0
    for result in results:
        usage = result.get("usage") or {}
        total_in += usage.get("input_tokens", 0)
        total_out += usage.get("output_tokens", 0)
        print(f"{result['label']:<32} {result['reused']:>6} {result['created']:>7} "
              f"{result['suspects']:>7} {result['dangling']:>8} {str(result['valid']):>6}")
    print(f"\ntotal tokens: {total_in} in / {total_out} out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
