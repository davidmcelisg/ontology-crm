"""The named questions from section 6 of the design document.

Unlike everything else in this system, these contain real domain judgement.
"An unanswered outbound message is a debt" is not derivable from the ontology;
it is an opinion about how relationships work, written down.

Nothing here is stored. Every answer is computed from the claim log on demand,
which is why a status field can never drift out of sync with reality.

Each function is declared in ontology.yaml under `functions`, so the registry
knows they exist even though the logic lives in Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import query
from .store import ClaimStore


def _me(store: ClaimStore) -> str:
    return store.registry.self_person_id


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenThread:
    interaction_id: str
    with_person: str
    occurred_at: datetime
    subject: str | None
    days_waiting: int


def open_threads(store: ClaimStore, as_of: date | None = None) -> dict[str, list[OpenThread]]:
    """Conversations still waiting on somebody.

    The reference case for "never store a state you can derive". There is no
    awaiting_reply field anywhere in the ontology. An interaction is open when
    it expected a response and nothing in its chain answered it.
    """
    if as_of is None:
        as_of = date.today()

    me = _me(store)
    interactions = store.all_of_type("Interaction")

    # Anything another interaction replies to has been answered.
    answered: set[str] = set()
    for body in interactions.values():
        parent = body.get("in_reply_to")
        if parent is not None:
            answered.add(parent)

    they_owe_me: list[OpenThread] = []
    i_owe_them: list[OpenThread] = []

    for interaction_id, body in interactions.items():
        if not body.get("expects_response"):
            continue
        if interaction_id in answered:
            continue

        occurred_at = body["occurred_at"]
        waiting = (as_of - occurred_at.date()).days

        others = []
        for participant in body.get("participants", []):
            if participant != me:
                others.append(participant)

        thread = OpenThread(
            interaction_id=interaction_id,
            with_person=others[0] if others else me,
            occurred_at=occurred_at,
            subject=body.get("subject"),
            days_waiting=waiting,
        )

        if body.get("direction") == "outbound":
            they_owe_me.append(thread)
        elif body.get("direction") == "inbound":
            i_owe_them.append(thread)

    they_owe_me.sort(key=lambda thread: thread.days_waiting, reverse=True)
    i_owe_them.sort(key=lambda thread: thread.days_waiting, reverse=True)

    return {"they_owe_me": they_owe_me, "i_owe_them": i_owe_them}


def outstanding_commitments(
    store: ClaimStore, side: str = "owed_by_me"
) -> dict[str, dict[str, Any]]:
    """Promises with no fulfilling interaction attached.

    side is "owed_by_me" or "owed_to_me". Same machinery both ways -- only which
    end self sits on changes.
    """
    me = _me(store)
    field = "obligor" if side == "owed_by_me" else "obligee"

    def unfulfilled(object_id: str, body: dict[str, Any]) -> bool:
        if body.get(field) != me:
            return False
        return body.get("fulfilled_by") is None

    return query.where(store, "Commitment", unfulfilled)


def who_do_i_know_at(store: ClaimStore, organization_id: str) -> list[dict[str, Any]]:
    """Live contacts at an organization or any of its children.

    Rolls up through org_parent, so asking about Bain finds people at the CDMX
    office. Ranked by how strong I consider the relationship.
    """
    me = _me(store)
    org_ids = query.descendants(store, organization_id, "parent")

    strength_by_person: dict[str, int] = {}
    for body in store.all_of_type("Relationship").values():
        if body.get("from_person") != me:
            continue
        strength = body.get("strength")
        if strength is not None:
            strength_by_person[body["to_person"]] = strength

    contacts: list[dict[str, Any]] = []

    for affiliation_id, body in store.all_of_type("Affiliation").items():
        if body.get("organization") not in org_ids:
            continue
        if body.get("end_date") is not None:
            continue

        person_id = body["person"]
        if person_id == me:
            continue

        contacts.append(
            {
                "person": person_id,
                "name": query.title(store, person_id),
                "role": body.get("role_title"),
                "seniority": body.get("seniority"),
                "organization": query.title(store, body["organization"]),
                "strength": strength_by_person.get(person_id, 0),
                "affiliation": affiliation_id,
            }
        )

    contacts.sort(key=lambda contact: contact["strength"], reverse=True)
    return contacts


def path_to(store: ClaimStore, target_id: str, max_hops: int = 6) -> str:
    """Shortest route from me to anyone or anything.

    Delegates entirely to generic traversal. A route may run through an
    Affiliation, an Introduction or a Commitment, and this function does not
    need to know those types exist.
    """
    me = _me(store)
    path = query.shortest_path(store, me, target_id, max_hops)
    if path is None:
        return "no known path"
    return query.describe_path(store, path, me)


def pursuit_board(store: ClaimStore) -> list[dict[str, Any]]:
    """Every live pursuit, with whether a reply is outstanding on it."""
    me = _me(store)

    waiting_on: set[str] = set()
    threads = open_threads(store)
    for thread in threads["they_owe_me"]:
        body = store.resolve(thread.interaction_id)
        if body is None:
            continue
        for subject_id in body.get("about", []):
            waiting_on.add(subject_id)

    board: list[dict[str, Any]] = []

    for pursuit_id, body in store.all_of_type("Pursuit").items():
        board.append(
            {
                "pursuit": pursuit_id,
                "organization": query.title(store, body["target_organization"]),
                "role": body.get("target_role"),
                "stage": body.get("stage"),
                "outcome": body.get("outcome"),
                "awaiting_reply": pursuit_id in waiting_on,
                "referred_by": (
                    query.title(store, body["referred_by"])
                    if body.get("referred_by")
                    else None
                ),
            }
        )

    board.sort(key=lambda entry: entry["outcome"] != "open")
    return board


def going_stale(
    store: ClaimStore, months: int = 6, minimum_strength: int = 3
) -> list[dict[str, Any]]:
    """People I claim to be close to and have not spoken with.

    The gap between how strong I say a relationship is and when I last acted
    like it. Only computable because Relationship carries a strength I asserted
    and Interaction carries dates I did not.
    """
    me = _me(store)
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)

    last_seen: dict[str, datetime] = {}
    for body in store.all_of_type("Interaction").values():
        occurred_at = body["occurred_at"]
        for participant in body.get("participants", []):
            if participant == me:
                continue
            if participant not in last_seen or occurred_at > last_seen[participant]:
                last_seen[participant] = occurred_at

    stale: list[dict[str, Any]] = []

    for body in store.all_of_type("Relationship").values():
        if body.get("from_person") != me:
            continue
        strength = body.get("strength") or 0
        if strength < minimum_strength:
            continue

        person_id = body["to_person"]
        seen = last_seen.get(person_id)
        if seen is not None and seen >= cutoff:
            continue

        stale.append(
            {
                "person": person_id,
                "name": query.title(store, person_id),
                "kind": body.get("kind"),
                "strength": strength,
                "last_interaction": seen.date() if seen else None,
            }
        )

    stale.sort(key=lambda entry: entry["strength"], reverse=True)
    return stale


def why_do_i_believe(store: ClaimStore, object_id: str) -> list[dict[str, Any]]:
    """The full provenance chain for an object.

    The thing a normal CRM cannot answer at all, because it overwrote the
    evidence when it updated the row.
    """
    trail: list[dict[str, Any]] = []

    for claim in store.history(object_id):
        source = claim.source_kind
        if claim.source_person is not None:
            source = f"{query.title(store, claim.source_person)} ({claim.source_kind})"

        trail.append(
            {
                "claim": claim.id,
                "learned": claim.asserted_at.date(),
                "source": source,
                "note": claim.source_note,
                "asserted": _what_it_asserted(claim),
            }
        )

    return trail


def _what_it_asserted(claim: Any) -> Any:
    """What a claim did, for display.

    Three claims carry no body: a retraction, and a merge, which asserts only
    that this object is really another one. Both would otherwise render as an
    empty line.
    """
    if claim.is_retraction:
        return "RETRACTION"
    if claim.redirect_to is not None:
        return f"MERGED into {claim.redirect_to}"
    return claim.changes


def reciprocity(store: ClaimStore, person_id: str) -> dict[str, int]:
    """Introductions exchanged in each direction with one person.

    Only answerable because Introduction is an object rather than an edge --
    three participants cannot be expressed as a link.
    """
    me = _me(store)
    made_for_them = 0
    made_by_them = 0

    for body in store.all_of_type("Introduction").values():
        parties = [body.get("introduced_a"), body.get("introduced_b")]

        if body.get("introducer") == me and person_id in parties:
            made_for_them += 1
        if body.get("introducer") == person_id and me in parties:
            made_by_them += 1

    return {
        "introductions_i_made": made_for_them,
        "introductions_they_made": made_by_them,
    }
