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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--claims", default=SAMPLE, help=f"claims file (default: {SAMPLE})")
    args = parser.parse_args()

    registry = load_ontology(ONTOLOGY)
    store = ClaimStore(registry, args.claims)

    if store.claim_count() == 0:
        print(f"No data in {args.claims}. Run scripts/seed_sample.py or scripts/seed_real.py first.")
        return 1

    org = pick_org(store)

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
        print(f"   {contact['name']:<16} {contact['role']:<22} "
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

    heading("relationships going stale")
    for entry in functions.going_stale(store):
        print(f"   {entry['name']:<16} strength {entry['strength']}  "
              f"last spoke {entry['last_interaction'] or 'never (no interaction logged)'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
