"""The four arguments, as runnable assertions.

These double as the demo script: each one is a claim about the design that you
can prove on screen in a few seconds.

    python tests/test_ontology_driven.py
"""

import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import ClaimStore, load_ontology
from crm import query
from crm.validator import validate_object

ONTOLOGY = "ontology/ontology.yaml"


def fresh_store(registry):
    handle, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(handle)
    os.remove(path)
    return ClaimStore(registry, path)


def test_a_new_object_type_needs_no_code_change():
    """The headline claim. Add a type to the yaml; everything picks it up."""
    source = open(ONTOLOGY).read()

    addition = """
  Referral:
    title_attribute: note
    id_prefix: ref
    attributes:
      referrer:
        type: ref
        target: Person
        required: true
        link_name: referral_by
        inverse_name: referrals_made
      about_person:
        type: ref
        target: Person
        required: true
        link_name: referral_about
        inverse_name: referrals_about
      note: { type: string }
    axioms:
      - { kind: not_equal, a: referrer, b: about_person }

  Person:"""
    patched = source.replace("\n  Person:", addition, 1)

    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "ontology.yaml")
    open(path, "w").write(patched)

    registry = load_ontology(path)
    store = fresh_store(registry)

    assert "Referral" in registry.type_names()

    # The link registry grew without anyone editing it.
    link_names = [link.name for link in registry.links_to("Person")]
    assert "referral_by" in link_names

    # Validation works on a type written thirty seconds ago.
    _, errors = validate_object(registry, "Referral", {"referrer": "person:a"})
    assert any("about_person" in str(error) for error in errors)

    # And so does the declared axiom.
    _, errors = validate_object(
        registry, "Referral",
        {"referrer": "person:a", "about_person": "person:a"},
    )
    assert any("must differ" in str(error) for error in errors)

    # Storage and traversal too.
    store.assert_object("Person", "person:a", {"display_name": "A"}, source_kind="self_observed")
    store.assert_object("Person", "person:b", {"display_name": "B"}, source_kind="self_observed")
    store.assert_object("Referral", "ref:1",
                        {"referrer": "person:a", "about_person": "person:b", "note": "worth meeting"},
                        source_kind="self_observed")

    edges = query.incoming(store, "person:b")
    assert any(edge.link == "referral_about" for edge in edges)

    shutil.rmtree(directory)


def test_the_two_clocks_disagree():
    """World time and belief time answer different questions."""
    store = fresh_store(load_ontology(ONTOLOGY))

    store.assert_object("Person", "person:x", {"display_name": "X"}, source_kind="self_observed")
    store.assert_object("Organization", "org:y", {"name": "Y", "kind": "company"}, source_kind="self_observed")

    store.assert_object(
        "Affiliation", "aff:xy",
        {"person": "person:x", "organization": "org:y", "kind": "employee"},
        source_kind="self_observed", asserted_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    store.assert_object(
        "Affiliation", "aff:xy", {"end_date": "2025-06-30"},
        source_kind="told_by_person", asserted_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    believed_in_2025 = store.resolve("aff:xy", known_as_of=datetime(2025, 1, 1, tzinfo=timezone.utc))
    believed_now = store.resolve("aff:xy")

    assert believed_in_2025.get("end_date") is None
    assert believed_now.get("end_date") == date(2025, 6, 30)


def test_retraction_undoes_inherited_values():
    """Retracting an old claim removes what later snapshots absorbed from it."""
    store = fresh_store(load_ontology(ONTOLOGY))

    store.assert_object("Person", "person:x", {"display_name": "X"}, source_kind="self_observed")
    store.assert_object("Organization", "org:y", {"name": "Y", "kind": "company"}, source_kind="self_observed")
    store.assert_object(
        "Affiliation", "aff:xy",
        {"person": "person:x", "organization": "org:y", "kind": "employee", "role_title": "Analyst"},
        source_kind="self_observed", asserted_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    promotion = store.assert_object(
        "Affiliation", "aff:xy", {"role_title": "Manager", "seniority": "manager"},
        source_kind="told_by_person", asserted_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    store.assert_object(
        "Affiliation", "aff:xy", {"end_date": "2026-01-31"},
        source_kind="self_observed", asserted_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    assert store.resolve("aff:xy")["role_title"] == "Manager"

    store.retract(promotion.id)
    after = store.resolve("aff:xy")

    assert after["role_title"] == "Analyst"      # fell back
    assert "seniority" not in after              # only ever came from the retracted claim
    assert after["end_date"] == date(2026, 1, 31)  # unrelated claim survived


def test_a_merge_is_one_claim_and_reversible():
    """Entity resolution never rewrites history. It appends a redirect."""
    from crm.actions import execute

    store = fresh_store(load_ontology(ONTOLOGY))

    execute(store, "CreatePerson", {"display_name": "Ana Ruiz", "aliases": ["Ana"]})
    execute(store, "CreatePerson", {"display_name": "Ana R"})
    execute(store, "RecordInteraction", {
        "occurred_at": "2026-05-01T10:00:00", "channel": "call", "direction": "outbound",
        "participants": ["person:ana-r"],
    })

    assert len(store.all_of_type("Person")) == 2
    claims_before = store.claim_count()

    result = execute(store, "MergePersons",
                     {"keep": "person:ana-ruiz", "merge": "person:ana-r"})

    # One redirect claim plus one alias-absorption claim. Nothing rewritten.
    assert store.claim_count() == claims_before + 2
    assert len(store.all_of_type("Person")) == 1

    # The old id still works, and references to it now find the survivor.
    assert store.resolve("person:ana-r")["display_name"] == "Ana Ruiz"
    referring = store.referrers("person:ana-ruiz")
    assert any(entry[0] == "Interaction" for entry in referring)

    # And it comes apart again.
    store.retract(result.claim_id)
    assert len(store.all_of_type("Person")) == 2
    assert store.resolve("person:ana-r")["display_name"] == "Ana R"


def test_updates_need_no_per_action_code():
    """Three unrelated updating actions run through one generic code path."""
    from crm.actions import execute

    store = fresh_store(load_ontology(ONTOLOGY))

    execute(store, "CreatePerson", {"display_name": "Q"})
    execute(store, "CreateOrganization", {"name": "R", "kind": "company"})
    execute(store, "AssertAffiliation", {
        "person": "person:q", "organization": "org:r", "kind": "employee",
        "role_title": "Analyst",
    })
    execute(store, "OpenPursuit", {
        "kind": "job_application", "target_organization": "org:r",
        "stage": "applied", "outcome": "open", "opened_at": "2026-01-01",
        "target_role": "Strategy Lead",
    })

    execute(store, "EndAffiliation", {"affiliation": "aff:analyst", "end_date": "2026-06-30"})
    execute(store, "AdvancePursuit", {"pursuit": "pursuit:strategy-lead", "stage": "onsite"})

    assert store.resolve("aff:analyst")["end_date"] == date(2026, 6, 30)
    assert store.resolve("pursuit:strategy-lead")["stage"] == "onsite"


def test_derived_state_cannot_drift():
    """There is no awaiting_reply field. Answering the message changes the answer."""
    store = fresh_store(load_ontology(ONTOLOGY))
    from crm import functions

    store.assert_object("Person", "person:david", {"display_name": "David"}, source_kind="self_observed")
    store.assert_object("Person", "person:z", {"display_name": "Z"}, source_kind="self_observed")
    store.assert_object(
        "Interaction", "interaction:1",
        {"occurred_at": "2026-09-01T10:00:00", "channel": "email", "direction": "outbound",
         "participants": ["person:david", "person:z"], "expects_response": True},
        source_kind="self_observed",
    )

    assert len(functions.open_threads(store)["they_owe_me"]) == 1

    store.assert_object(
        "Interaction", "interaction:2",
        {"occurred_at": "2026-09-04T10:00:00", "channel": "email", "direction": "inbound",
         "participants": ["person:david", "person:z"], "in_reply_to": "interaction:1"},
        source_kind="self_observed",
    )

    assert len(functions.open_threads(store)["they_owe_me"]) == 0


def main():
    tests = [
        test_a_new_object_type_needs_no_code_change,
        test_the_two_clocks_disagree,
        test_retraction_undoes_inherited_values,
        test_a_merge_is_one_claim_and_reversible,
        test_updates_need_no_per_action_code,
        test_derived_state_cannot_drift,
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  pass  {test.__name__}")
        except AssertionError as error:
            failures += 1
            print(f"  FAIL  {test.__name__}: {error}")
    print(f"\n{len(tests) - failures}/{len(tests)} passing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
