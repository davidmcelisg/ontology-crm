"""Walks through what the system can answer. This is the screen-share script.

    python scripts/demo.py                          # the fake sample network
    python scripts/demo.py --claims data/claims.jsonl   # your real one

Nothing below names a person or an organization. The targets for each question
are picked from the data: the org with the most contacts, the first executive,
the first thing I only know second-hand. Point it at a different claims file
and it asks the same questions of a different life.
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import ClaimStore, load_ontology
from crm import functions, query

ONTOLOGY = "ontology/ontology.yaml"
SAMPLE = "data/sample_claims.jsonl"


def heading(text):
    print(f"\n{'=' * 4} {text}")


def pick_targets(store):
    """Choose what to ask about, from the data rather than from this file."""
    me = store.registry.self_person_id

    by_org = Counter()
    executive = None
    secondhand = None
    for aff_id, body in store.all_of_type("Affiliation").items():
        if body.get("person") == me:
            continue
        by_org[body["organization"]] += 1
        if executive is None and body.get("seniority") == "executive":
            executive = body["person"]
        if secondhand is None and store.resolving_claim(aff_id).source_kind == "told_by_person":
            secondhand = aff_id

    org = by_org.most_common(1)[0][0] if by_org else None
    # Prefer an org with a parent so the roll-up has something to show.
    for candidate, _ in by_org.most_common():
        parent = store.resolve(candidate).get("parent")
        if parent:
            org = parent
            break
    return {"org": org, "executive": executive, "secondhand": secondhand}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--claims", default=SAMPLE, help=f"claims file (default: {SAMPLE})")
    args = parser.parse_args()

    registry = load_ontology(ONTOLOGY)
    store = ClaimStore(registry, args.claims)

    if store.claim_count() == 0:
        print(f"No data in {args.claims}. Run scripts/seed_sample.py or scripts/seed_real.py first.")
        return 1

    targets = pick_targets(store)
    org, executive, secondhand = targets["org"], targets["executive"], targets["secondhand"]

    heading("the ontology is data")
    print(f"   {len(registry.object_types)} object types, "
          f"{len(registry.links)} links derived from ref attributes, "
          f"{len(registry.actions)} actions")

    heading("who owes me a reply")
    threads = functions.open_threads(store)
    for thread in threads["they_owe_me"]:
        print(f"   {query.title(store, thread.with_person):<16} "
              f"{thread.days_waiting:>3}d  {thread.subject}")

    heading("who am I ignoring")
    for thread in threads["i_owe_them"]:
        print(f"   {query.title(store, thread.with_person):<16} "
              f"{thread.days_waiting:>3}d  {thread.subject}")

    heading(f"who do I know at {query.title(store, org)} (rolls up through subsidiaries)")
    for contact in functions.who_do_i_know_at(store, org):
        print(f"   {contact['name']:<16} {contact['role']:<22} "
              f"{contact['organization']:<24} strength {contact['strength']}")

    heading(f"shortest path to {query.title(store, executive)}")
    print("  ", functions.path_to(store, executive))

    heading("pursuit board")
    for entry in functions.pursuit_board(store):
        flag = "   <- waiting on them" if entry["awaiting_reply"] else ""
        print(f"   {entry['organization']:<20} {entry['role']:<28} "
              f"{entry['stage']}{flag}")

    heading("what I promised and have not done")
    for body in functions.outstanding_commitments(store, "owed_by_me").values():
        due = f", due {body['due_date']}" if body.get("due_date") else ""
        print(f"   {body['description']} -> {query.title(store, body['obligee'])}{due}")

    heading("relationships going stale")
    for entry in functions.going_stale(store):
        print(f"   {entry['name']:<16} strength {entry['strength']}  "
              f"last spoke {entry['last_interaction'] or 'never (no interaction logged)'}")

    if secondhand is not None:
        who = query.title(store, store.resolve(secondhand)["person"])
        where = query.title(store, store.resolve(secondhand)["organization"])
        heading(f"why do I believe {who} works at {where}")
        for step in functions.why_do_i_believe(store, secondhand):
            print(f"   {step['learned']}  {step['source']:<26} {step['note']}")

    heading("traversal knows no type names")
    for edge in query.neighbors(store, org):
        target = query.title(store, edge.other_end(org))
        print(f"   {edge.direction:<4} {edge.link:<24} {target}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
