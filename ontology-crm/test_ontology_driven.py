"""The four arguments, as runnable assertions.

These double as the demo script: each one is a claim about the design that you
can prove on screen in a few seconds.

    python tests/test_ontology_driven.py
"""

import os
import shutil
import sys
import tempfile
from datetime import date, datetime

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
        source_kind="self_observed", asserted_at=datetime(2024, 1, 1),
    )
    store.assert_object(
        "Affiliation", "aff:xy", {"end_date": "2025-06-30"},
        source_kind="told_by_person", asserted_at=datetime(2026, 3, 1),
    )

    believed_in_2025 = store.resolve("aff:xy", known_as_of=datetime(2025, 1, 1))
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
        source_kind="self_observed", asserted_at=datetime(2024, 1, 1),
    )
    promotion = store.assert_object(
        "Affiliation", "aff:xy", {"role_title": "Manager", "seniority": "manager"},
        source_kind="told_by_person", asserted_at=datetime(2025, 1, 1),
    )
    store.assert_object(
        "Affiliation", "aff:xy", {"end_date": "2026-01-31"},
        source_kind="self_observed", asserted_at=datetime(2026, 2, 1),
    )

    assert store.resolve("aff:xy")["role_title"] == "Manager"

    store.retract(promotion.id)
    after = store.resolve("aff:xy")

    assert after["role_title"] == "Analyst"      # fell back
    assert "seniority" not in after              # only ever came from the retracted claim
    assert after["end_date"] == date(2026, 1, 31)  # unrelated claim survived


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
