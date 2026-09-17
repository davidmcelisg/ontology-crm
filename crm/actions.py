"""Executes Actions.

An Action is a named, typed, validated mutation. Everything that changes the
graph goes through here, which is what will let an LLM write to the store
without being able to corrupt it: the model can only emit action names and
parameters that the registry declares, and anything else is rejected before a
claim is written.

Three kinds, and only one of them needed hand-written code:

    creates  -- mints an id from the title attribute and writes the first claim
    updates  -- finds the ref parameter pointing at the type being updated,
                treats that as the target and everything else as the delta
    special  -- entity resolution, which is the one case with its own logic

Note what is absent: any mention of RecordInteraction, AdvancePursuit, or any
other action by name. Declare a new one in ontology.yaml and it executes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .store import ClaimStore
from .validator import ValidationError, validate_action

# Claim metadata travels with every action but is not part of any object body.
ENVELOPE_FIELDS = (
    "asserted_at",
    "valid_from",
    "valid_to",
    "source_kind",
    "source_person",
    "source_note",
)


class ActionError(Exception):
    pass


class ActionValidationError(ActionError):
    def __init__(self, action_name: str, errors: list[ValidationError]) -> None:
        self.errors = errors
        joined = "\n  ".join(str(error) for error in errors)
        super().__init__(f"{action_name} rejected:\n  {joined}")


@dataclass(frozen=True)
class ActionResult:
    action: str
    object_id: str
    claim_id: str
    created: bool
    note: str = ""


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


def execute(store: ClaimStore, action_name: str, params: dict[str, Any]) -> ActionResult:
    """Validate an action and apply it. The only write path into the system."""
    registry = store.registry
    action = registry.action(action_name)

    supplied = with_envelope_defaults(params)

    normalized, errors = validate_action(registry, action_name, supplied, exists=store.exists)
    if errors:
        raise ActionValidationError(action_name, errors)

    envelope = _envelope(normalized)
    body = _body(normalized)

    if action.creates is not None:
        return _do_create(store, action_name, action.creates, body, envelope)

    if action.updates is not None:
        return _do_update(store, action_name, action.updates, body, envelope)

    if action.special == "entity_resolution":
        return _do_merge(store, action_name, body, envelope)

    raise ActionError(f"{action_name} declares no executable effect")


def with_envelope_defaults(params: dict[str, Any]) -> dict[str, Any]:
    """Fill the envelope fields a caller should not have to supply.

    Every claim needs a timestamp and a source, so these are declared required
    rather than optional -- a claim without provenance is the thing the design
    exists to prevent. The default belongs here, in one place, so that dry-run
    validation and execution agree on what is valid.
    """
    supplied = dict(params)
    if supplied.get("asserted_at") is None:
        supplied["asserted_at"] = datetime.now(timezone.utc)
    if supplied.get("source_kind") is None:
        supplied["source_kind"] = "prompt"
    return supplied


def _do_create(
    store: ClaimStore,
    action_name: str,
    type_name: str,
    body: dict[str, Any],
    envelope: dict[str, Any],
) -> ActionResult:
    object_id = mint_id(store, type_name, body)

    if store.exists(object_id):
        raise ActionError(
            f"{object_id} already exists. Look it up with lookup() and update it, "
            f"or merge the two afterwards."
        )

    claim = store.assert_object(type_name, object_id, body, **envelope)
    return ActionResult(action_name, object_id, claim.id, created=True)


def _do_update(
    store: ClaimStore,
    action_name: str,
    type_name: str,
    body: dict[str, Any],
    envelope: dict[str, Any],
) -> ActionResult:
    target_id, changes = _split_target(store, action_name, type_name, body)

    if not store.exists(target_id):
        raise ActionError(f"{action_name} targets {target_id}, which does not exist")

    claim = store.assert_object(type_name, target_id, changes, **envelope)
    return ActionResult(action_name, target_id, claim.id, created=False)


def _do_merge(
    store: ClaimStore,
    action_name: str,
    body: dict[str, Any],
    envelope: dict[str, Any],
) -> ActionResult:
    keep_id = body["keep"]
    merge_id = body["merge"]

    keep_body = store.resolve(keep_id) or {}
    merge_body = store.resolve(merge_id) or {}
    type_name = store.type_of(keep_id)

    claim = store.merge(
        merge_id,
        keep_id,
        source_kind=envelope.get("source_kind") or "self_observed",
        source_note=envelope.get("source_note"),
        asserted_at=envelope.get("asserted_at"),
    )

    # Carry the loser's names across so future lookups still find the person by
    # whatever they were originally called.
    absorbed = _absorb_aliases(store, type_name, keep_body, merge_body)
    if absorbed:
        store.assert_object(
            type_name, keep_id, {"aliases": absorbed},
            source_kind=envelope.get("source_kind") or "self_observed",
            source_note=f"absorbed names from {merge_id}",
        )

    return ActionResult(
        action_name, keep_id, claim.id, created=False,
        note=f"{merge_id} now redirects to {keep_id}",
    )


def _split_target(
    store: ClaimStore, action_name: str, type_name: str, body: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Find which parameter names the object being updated.

    Generic rule: an updating action has exactly one ref parameter whose target
    is the type it updates. That parameter is the target; the rest is the delta.
    This is why EndAffiliation, AdvancePursuit and FulfillCommitment all work
    with no code of their own.
    """
    action = store.registry.action(action_name)

    target_id = None
    target_param = None

    for name, attribute in action.parameters.items():
        if not attribute.is_ref:
            continue
        if type_name not in attribute.ref_targets:
            continue
        if target_param is not None:
            raise ActionError(
                f"{action_name} has more than one parameter pointing at {type_name}; "
                f"the executor cannot tell which is the target"
            )
        target_param = name
        target_id = body.get(name)

    if target_param is None:
        raise ActionError(
            f"{action_name} updates {type_name} but declares no ref parameter "
            f"pointing at it"
        )
    if target_id is None:
        raise ActionError(f"{action_name} is missing {target_param}")

    changes: dict[str, Any] = {}
    for name, value in body.items():
        if name != target_param:
            changes[name] = value

    return target_id, changes


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def mint_id(store: ClaimStore, type_name: str, body: dict[str, Any]) -> str:
    """Build an id from the type's prefix and its title attribute.

    "Ana Ruiz" as a Person becomes person:ana-ruiz. Types with no title
    attribute, or an unusable one, fall back to a counter.

    When the title is not an identity (title_is_identity is false) and the id
    is already taken, a counter is appended: the second "Software Engineer"
    affiliation becomes aff:software-engineer-2. Identity-titled types keep
    the collision, so the caller can reject it as a duplicate.
    """
    object_type = store.registry.type(type_name)
    prefix = object_type.prefix

    title_attribute = object_type.title_attribute
    raw = body.get(title_attribute) if title_attribute else None

    slug = slugify(raw) if isinstance(raw, str) else ""
    if not slug:
        existing = len(store.all_of_type(type_name)) + 1
        slug = f"{existing:04d}"

    object_id = f"{prefix}:{slug}"
    if object_type.title_is_identity:
        return object_id

    suffix = 2
    while store.exists(object_id):
        object_id = f"{prefix}:{slug}-{suffix}"
        suffix += 1
    return object_id


def slugify(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    ascii_only = stripped.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    hyphenated = re.sub(r"[^a-z0-9]+", "-", lowered)
    return hyphenated.strip("-")[:60]


def lookup(store: ClaimStore, type_name: str, text: str) -> list[str]:
    """Find objects of a type whose title or aliases match some text.

    The ingestion layer calls this before creating anything, which is how a
    second mention of the same person avoids becoming a second object.
    Matching is deliberately loose; ambiguity is the caller's to resolve.
    """
    object_type = store.registry.type(type_name)
    title_attribute = object_type.title_attribute
    wanted = slugify(text)

    if not wanted:
        return []

    matches: list[str] = []

    for object_id, body in store.all_of_type(type_name).items():
        candidates = []
        if title_attribute and isinstance(body.get(title_attribute), str):
            candidates.append(body[title_attribute])
        for alias in body.get("aliases", []) or []:
            candidates.append(alias)

        for candidate in candidates:
            candidate_slug = slugify(candidate)
            if not candidate_slug:
                continue
            if candidate_slug == wanted or wanted in candidate_slug or candidate_slug in wanted:
                matches.append(object_id)
                break

    return matches


def _absorb_aliases(
    store: ClaimStore,
    type_name: str,
    keep_body: dict[str, Any],
    merge_body: dict[str, Any],
) -> list[str]:
    object_type = store.registry.type(type_name)
    if "aliases" not in object_type.attributes:
        return []

    absorbed: list[str] = []
    for value in keep_body.get("aliases", []) or []:
        absorbed.append(value)

    title_attribute = object_type.title_attribute
    incoming: list[str] = []
    if title_attribute and isinstance(merge_body.get(title_attribute), str):
        incoming.append(merge_body[title_attribute])
    for value in merge_body.get("aliases", []) or []:
        incoming.append(value)

    changed = False
    for value in incoming:
        if value not in absorbed:
            absorbed.append(value)
            changed = True

    if not changed:
        return []
    return absorbed


def _envelope(normalized: dict[str, Any]) -> dict[str, Any]:
    envelope: dict[str, Any] = {}
    for name in ENVELOPE_FIELDS:
        if name in normalized:
            envelope[name] = normalized[name]
    if "source_kind" not in envelope:
        envelope["source_kind"] = "prompt"
    return envelope


def _body(normalized: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for name, value in normalized.items():
        if name not in ENVELOPE_FIELDS:
            body[name] = value
    return body
