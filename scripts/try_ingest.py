"""Runs the ingestion pipeline with a stubbed model, so it works with no API key.

Swap `stub` for ingest.anthropic_completer() to use a real model.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import ClaimStore, ingest, load_ontology

NOTE = ("Coffee with Rosa yesterday. She's leaving Cardinal at the end of March "
        "and joining Northwind as Head of Strategy. She offered to introduce me "
        "to Ivette, and I said I'd send her my CV this week.")

# What a model returns for that note. Canned here so the demo needs no key.
STUBBED = json.dumps({"actions": [
    {"action": "EndAffiliation", "reason": "roster shows one Rosa, at Cardinal Mexico",
     "params": {"affiliation": "aff:rosa-cardinal", "end_date": "2027-03-31",
                "source_kind": "prompt", "source_person": "person:rosa"}},
    {"action": "AssertAffiliation", "reason": "new role stated in the note",
     "params": {"person": "person:rosa", "organization": "org:northwind",
                "kind": "employee", "role_title": "Head of Strategy",
                "source_kind": "prompt", "source_person": "person:rosa"}},
    {"action": "RecordInteraction", "reason": "the coffee itself",
     "params": {"occurred_at": "2026-09-14T11:00:00", "channel": "in_person",
                "direction": "mutual", "participants": ["person:david", "person:rosa"],
                "subject": "Coffee, her move to Northwind", "source_kind": "prompt"}},
    {"action": "MakeCommitment", "reason": "I said I would send my CV",
     "params": {"obligor": "person:david", "obligee": "person:rosa",
                "description": "send her my CV", "due_date": "2026-09-21",
                "source_kind": "prompt"}},
    {"action": "RecordInteraction", "reason": "invented action to show rejection",
     "params": {"occurred_at": "2026-09-14T11:00:00", "channel": "telepathy",
                "direction": "mutual", "participants": ["person:rosa"],
                "source_kind": "prompt"}},
]}, indent=1)


def stub(system_prompt, user_prompt):
    return STUBBED


def main():
    store = ClaimStore(load_ontology("ontology/ontology.yaml"), "data/sample_claims.jsonl")

    prompt = ingest.build_user_prompt(store, NOTE, "2026-09-15")
    print(f"prompt: {len(prompt)} chars, "
          f"{len(ingest.action_schemas(store.registry))} action schemas generated\n")

    proposal = ingest.propose(store, NOTE, stub, today="2026-09-15")
    print(ingest.render(store, proposal))

    print(f"\nproposal valid: {proposal.valid}")
    if not proposal.valid:
        print("nothing written. drop the rejected action and re-run to apply.")


if __name__ == "__main__":
    main()
