"""Builds a small fake network so the demo has something to answer about.

Every name here is invented. Real data goes in data/claims.jsonl, which is
gitignored.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import ClaimStore, load_ontology

ONTOLOGY = "ontology/ontology.yaml"
SAMPLE = "data/sample_claims.jsonl"


def seed(store):
    def put(type_name, object_id, body, kind="self_observed", who=None, note=None):
        store.assert_object(type_name, object_id, body, source_kind=kind,
                            source_person=who, source_note=note)

    people = [
        ("person:david", "David"),
        ("person:rosa", "Rosa Delgado"),
        ("person:teo", "Teo Marin"),
        ("person:nayeli", "Nayeli Ochoa"),
        ("person:bruno", "Bruno Salas"),
        ("person:ivette", "Ivette Cano"),
    ]
    for person_id, name in people:
        put("Person", person_id, {"display_name": name})

    put("Organization", "org:northwind", {"name": "Northwind Analytics", "kind": "company", "location": "CDMX"})
    put("Organization", "org:cardinal", {"name": "Cardinal Group", "kind": "company"})
    put("Organization", "org:cardinal-mx", {"name": "Cardinal Group Mexico", "kind": "company", "parent": "org:cardinal"})
    put("Organization", "org:hackday", {"name": "HackDay", "kind": "student_org"})

    put("Affiliation", "aff:teo-northwind", {"person": "person:teo", "organization": "org:northwind", "kind": "employee", "role_title": "Software Engineer", "start_date": "2025-01-15"})
    put("Affiliation", "aff:nayeli-northwind", {"person": "person:nayeli", "organization": "org:northwind", "kind": "employee", "role_title": "Recruiter", "start_date": "2025-06-01"})
    put("Affiliation", "aff:bruno-northwind", {"person": "person:bruno", "organization": "org:northwind", "kind": "founder", "role_title": "Co-founder", "seniority": "executive", "start_date": "2023-02-01"})
    put("Affiliation", "aff:ivette-northwind", {"person": "person:ivette", "organization": "org:northwind", "kind": "employee", "role_title": "CTO", "seniority": "executive", "start_date": "2023-02-01"})
    put("Affiliation", "aff:rosa-cardinal", {"person": "person:rosa", "organization": "org:cardinal-mx", "kind": "employee", "role_title": "Consultant", "start_date": "2024-03-01"},
        kind="told_by_person", who="person:teo", note="mentioned at a dinner")

    put("Relationship", "rel:david-teo", {"from_person": "person:david", "to_person": "person:teo", "kind": "friend", "strength": 5, "origin_context": "university"})
    put("Relationship", "rel:david-nayeli", {"from_person": "person:david", "to_person": "person:nayeli", "kind": "friend", "strength": 4, "origin_context": "through Teo"})
    put("Relationship", "rel:david-bruno", {"from_person": "person:david", "to_person": "person:bruno", "kind": "acquaintance", "strength": 3, "origin_context": "spoke when the company was tiny"})
    put("Relationship", "rel:david-rosa", {"from_person": "person:david", "to_person": "person:rosa", "kind": "acquaintance", "strength": 3, "origin_context": "HackDay sponsor dinner"})

    put("Pursuit", "pursuit:northwind-fde", {"kind": "recruiting_process", "target_organization": "org:northwind", "target_role": "Forward Deployed Engineer", "stage": "first round passed", "outcome": "open", "opened_at": "2026-09-05", "referred_by": "person:nayeli"})

    put("Interaction", "interaction:0001", {"occurred_at": "2026-09-03T18:00:00", "channel": "call", "direction": "inbound", "participants": ["person:david", "person:nayeli"], "subject": "A role might open up", "expects_response": False, "about": ["org:northwind"]})
    put("Interaction", "interaction:0002", {"occurred_at": "2026-09-08T09:00:00", "channel": "email", "direction": "outbound", "participants": ["person:david", "person:bruno"], "subject": "Following up on the role", "expects_response": True, "about": ["pursuit:northwind-fde"]})
    put("Interaction", "interaction:0003", {"occurred_at": "2026-09-09T11:00:00", "channel": "whatsapp", "direction": "inbound", "participants": ["person:david", "person:teo"], "subject": "Can you review my resume?", "expects_response": True})
    put("Interaction", "interaction:0004", {"occurred_at": "2024-11-02T20:00:00", "channel": "in_person", "direction": "mutual", "participants": ["person:david", "person:rosa"], "subject": "HackDay sponsor dinner"})

    put("Commitment", "commitment:0001", {"obligor": "person:david", "obligee": "person:teo", "description": "send feedback on his resume", "created_in": "interaction:0003", "due_date": "2026-09-16"})
    put("Introduction", "intro:0001", {"introducer": "person:teo", "introduced_a": "person:david", "introduced_b": "person:ivette", "occurred_at": "2026-09-06", "context": "put me in touch before the CTO round"})


def main():
    if os.path.exists(SAMPLE):
        os.remove(SAMPLE)
    store = ClaimStore(load_ontology(ONTOLOGY), SAMPLE)
    seed(store)
    print(f"{store.claim_count()} claims across {store.object_count()} objects -> {SAMPLE}")


if __name__ == "__main__":
    main()
