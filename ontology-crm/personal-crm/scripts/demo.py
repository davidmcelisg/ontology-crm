"""Walks through what the system can answer. This is the screen-share script."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import ClaimStore, load_ontology
from crm import functions, query

ONTOLOGY = "ontology/ontology.yaml"
SAMPLE = "data/sample_claims.jsonl"


def heading(text):
    print(f"\n{'=' * 4} {text}")


def main():
    registry = load_ontology(ONTOLOGY)
    store = ClaimStore(registry, SAMPLE)

    if store.claim_count() == 0:
        print("No data. Run scripts/seed_sample.py first.")
        return 1

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

    heading("who do I know at Cardinal Group (rolls up through subsidiaries)")
    for contact in functions.who_do_i_know_at(store, "org:cardinal"):
        print(f"   {contact['name']:<16} {contact['role']:<22} "
              f"{contact['organization']:<24} strength {contact['strength']}")

    heading("shortest path to the CTO")
    print("  ", functions.path_to(store, "person:ivette"))

    heading("pursuit board")
    for entry in functions.pursuit_board(store):
        flag = "   <- waiting on them" if entry["awaiting_reply"] else ""
        print(f"   {entry['organization']:<20} {entry['role']:<28} "
              f"{entry['stage']}{flag}")

    heading("what I promised and have not done")
    for body in functions.outstanding_commitments(store, "owed_by_me").values():
        print(f"   {body['description']} -> "
              f"{query.title(store, body['obligee'])}, due {body['due_date']}")

    heading("relationships going stale")
    for entry in functions.going_stale(store):
        print(f"   {entry['name']:<16} strength {entry['strength']}  "
              f"last spoke {entry['last_interaction']}")

    heading("why do I believe Rosa works there")
    for step in functions.why_do_i_believe(store, "aff:rosa-cardinal"):
        print(f"   {step['learned']}  {step['source']:<26} {step['note']}")

    heading("traversal knows no type names")
    for edge in query.neighbors(store, "org:northwind"):
        target = query.title(store, edge.other_end("org:northwind"))
        print(f"   {edge.direction:<4} {edge.link:<24} {target}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
