"""Walks through what the system can answer. This is the screen-share script.

    python scripts/demo.py                          # the fake sample network
    python scripts/demo.py --claims data/claims.jsonl   # your real one

Nothing below names a person or an organization. The org to ask about is the
one with the most contacts in the data. Point it at a different claims file and
it asks the same questions of a different life.
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


def pick_org(store):
    """The organization with the most contacts, preferring one with a parent
    so the subsidiary roll-up has something to show."""
    me = store.registry.self_person_id
    by_org = Counter()
    for body in store.all_of_type("Affiliation").values():
        if body.get("person") != me:
            by_org[body["organization"]] += 1
    for candidate, _ in by_org.most_common():
        if store.resolve(candidate).get("parent"):
            return store.resolve(candidate)["parent"]
    return by_org.most_common(1)[0][0] if by_org else None


def pick_believed(store):
    """The object with the richest claim history, so the provenance chain has
    something to show. Most claims wins, ties broken by how many different
    sources contributed. Nothing here names anything either."""
    best = None
    for type_name in store.registry.type_names():
        for object_id in store.all_of_type(type_name):
            history = store.history(object_id)
            if len(history) < 2:
                continue
            score = (len(history), len({claim.source_kind for claim in history}))
            if best is None or score > best[0]:
                best = (score, object_id)
    return best[1] if best else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--claims", default=SAMPLE, help=f"claims file (default: {SAMPLE})")
    parser.add_argument("--org", default=None,
                        help="organization id for the 'who do I know at' question "
                             "(default: the one with the most contacts)")
    args = parser.parse_args()

    registry = load_ontology(ONTOLOGY)
    store = ClaimStore(registry, args.claims)

    if store.claim_count() == 0:
        print(f"No data in {args.claims}. Run scripts/seed_sample.py or scripts/seed_real.py first.")
        return 1

    org = args.org or pick_org(store)

    print(f"{len(registry.object_types)} object types, "
          f"{len(registry.links)} links derived from ref attributes, "
          f"{len(registry.actions)} actions")

    heading("who owes me a reply")
    threads = functions.open_threads(store)
    for thread in threads["they_owe_me"]:
        print(f"   {query.title(store, thread.with_person):<16} "
              f"{thread.days_waiting:>3}d  {thread.subject}")

    heading("who do I owe a reply")
    for thread in threads["i_owe_them"]:
        print(f"   {query.title(store, thread.with_person):<16} "
              f"{thread.days_waiting:>3}d  {thread.subject}")

    heading(f"who do I know at {query.title(store, org)} (rolls up through subsidiaries)")
    for contact in functions.who_do_i_know_at(store, org):
        print(f"   {contact['name']:<16} {contact['role'] or '(role unknown)':<22} "
              f"{contact['organization']}")


    heading("pursuit board")
    for entry in functions.pursuit_board(store):
        flag = "   <- waiting on them" if entry["awaiting_reply"] else ""
        print(f"   {entry['organization']:<20} {entry['role'] or '(role tbd)':<28} "
              f"{entry['stage']}{flag}")

    heading("what I promised and have not delivered")
    for body in functions.outstanding_commitments(store, "owed_by_me").values():
        due = f", due {body['due_date']}" if body.get("due_date") else ""
        print(f"   {body['description']} -> {query.title(store, body['obligee'])}{due}")

    believed = pick_believed(store)
    if believed is not None:
        heading(f"why do I believe what I believe about "
                f"{query.title(store, believed)} ({believed})")
        for entry in functions.why_do_i_believe(store, believed):
            asserted = entry["asserted"]
            if isinstance(asserted, dict):
                asserted = ", ".join(f"{k}={v}" for k, v in asserted.items())
            print(f"   {entry['learned']}  {entry['source']:<34} {asserted}")
            if entry["note"]:
                print(f"   {'':<12}  {'':<34} note: {entry['note']}")

    heading("relationships going stale")
    for entry in functions.going_stale(store):
        print(f"   {entry['name']:<16} strength {entry['strength']}  "
              f"last spoke {entry['last_interaction'] or 'never (no interaction logged)'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
