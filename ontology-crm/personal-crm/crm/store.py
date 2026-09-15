"""Append-only storage for claims.

Nothing is ever updated or deleted. Every fact learned is a new line in a JSONL
file, and current state is computed on read. The store is the only component
that touches disk.

Each claim carries two things: `changes`, what this claim actually asserted,
and `body`, the merged snapshot after applying it. That gives two read paths,
the same split Hudi makes between merge-on-read and compaction:

    fast path  -- no retractions on this object, so take the latest snapshot
    slow path  -- something was retracted, so replay `changes` in order

The log itself is never rewritten. Retraction changes how claims are read, not
what is stored.

Two clocks, both optional on every read:
    as_of        -- world time. What was true on this date?
    known_as_of  -- belief time. What did I think, as of this moment?
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .registry import Registry
from .validator import ValidationError, validate_object


class StoreError(Exception):
    pass


class InvalidClaimError(StoreError):
    def __init__(self, errors: list[ValidationError]) -> None:
        self.errors = errors
        joined = "\n  ".join(str(error) for error in errors)
        super().__init__(f"claim rejected:\n  {joined}")


@dataclass(frozen=True)
class Claim:
    """One assertion about one object, at one moment, from one source."""

    id: str
    object_type: str
    object_id: str
    changes: dict[str, Any]     # what this claim asserted
    body: dict[str, Any]        # state after merging it onto what came before
    asserted_at: datetime
    source_kind: str
    valid_from: date | None = None
    valid_to: date | None = None
    source_person: str | None = None
    source_note: str | None = None
    supersedes: str | None = None
    is_retraction: bool = False


class ClaimStore:
    def __init__(self, registry: Registry, path: str) -> None:
        self.registry = registry
        self.path = path

        self._claims: list[Claim] = []
        self._by_object: dict[str, list[Claim]] = {}
        self._by_type: dict[str, set[str]] = {}
        self._retracted_claim_ids: set[str] = set()
        self._objects_with_retractions: set[str] = set()
        self._next_sequence = 1

        if os.path.exists(path):
            self._load()

    # -- writing ----------------------------------------------------------

    def assert_object(
        self,
        type_name: str,
        object_id: str,
        changes: dict[str, Any],
        source_kind: str,
        source_person: str | None = None,
        source_note: str | None = None,
        asserted_at: datetime | None = None,
        valid_from: date | None = None,
        valid_to: date | None = None,
    ) -> Claim:
        """Record what you now believe about an object.

        `changes` holds only what you learned. The store merges it onto current
        state and stores the merged result, so callers write deltas and disk
        holds snapshots.
        """
        if asserted_at is None:
            asserted_at = datetime.now()

        current = self.resolve(object_id)

        merged: dict[str, Any] = {}
        if current is not None:
            for key, value in current.items():
                merged[key] = value
        for key, value in changes.items():
            merged[key] = value

        normalized, errors = validate_object(
            self.registry, type_name, merged, exists=self.exists
        )
        if errors:
            raise InvalidClaimError(errors)

        # Keep the delta as well as the merge. Without it, retracting an
        # older claim could not undo the values later snapshots inherited.
        change_set: dict[str, Any] = {}
        for key in changes:
            if key in normalized:
                change_set[key] = normalized[key]

        claim = Claim(
            id=self._mint_claim_id(),
            object_type=type_name,
            object_id=object_id,
            changes=change_set,
            body=normalized,
            asserted_at=asserted_at,
            source_kind=source_kind,
            valid_from=valid_from,
            valid_to=valid_to,
            source_person=source_person,
            source_note=source_note,
            supersedes=self._latest_claim_id(object_id),
        )
        self._append(claim)
        return claim

    def retract(
        self,
        claim_id: str,
        source_kind: str = "self_observed",
        source_note: str | None = None,
        asserted_at: datetime | None = None,
    ) -> Claim:
        """Withdraw a claim by appending another one. Nothing is edited."""
        target = self.claim(claim_id)

        claim = Claim(
            id=self._mint_claim_id(),
            object_type=target.object_type,
            object_id=target.object_id,
            changes={},
            body={},
            asserted_at=asserted_at or datetime.now(),
            source_kind=source_kind,
            source_note=source_note,
            supersedes=claim_id,
            is_retraction=True,
        )
        self._append(claim)
        return claim

    # -- reading ----------------------------------------------------------

    def resolve(
        self,
        object_id: str,
        as_of: date | None = None,
        known_as_of: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Current believed state of an object, or None if nothing applies.

        The resolution rule from the design doc: the latest non-retracted claim
        by asserted_at whose validity window contains the evaluation time.
        """
        applicable = self._applicable_claims(object_id, as_of, known_as_of)
        if not applicable:
            return None

        if object_id not in self._objects_with_retractions:
            # Fast path. Nothing was withdrawn, so the latest snapshot is
            # already correct and resolution is a max.
            return applicable[-1].body

        # Slow path. A retracted claim's contents may be sitting inside later
        # snapshots, so rebuild from the deltas instead of trusting them.
        merged: dict[str, Any] = {}
        for claim in applicable:
            for key, value in claim.changes.items():
                merged[key] = value
        return merged

    def resolving_claim(
        self,
        object_id: str,
        as_of: date | None = None,
        known_as_of: datetime | None = None,
    ) -> Claim | None:
        """The most recent claim contributing to current state, so callers can
        show where a belief came from."""
        applicable = self._applicable_claims(object_id, as_of, known_as_of)
        if not applicable:
            return None
        return applicable[-1]

    def _applicable_claims(
        self,
        object_id: str,
        as_of: date | None = None,
        known_as_of: datetime | None = None,
    ) -> list[Claim]:
        """Live claims about an object under both clocks, oldest first.

        Both read paths share this filter, so retraction, the belief clock and
        the world clock are applied in exactly one place.
        """
        if as_of is None:
            as_of = date.today()
        if known_as_of is None:
            known_as_of = datetime.now()

        applicable: list[Claim] = []

        for claim in self._by_object.get(object_id, []):
            if claim.is_retraction:
                continue
            if claim.id in self._retracted_claim_ids:
                continue
            if claim.asserted_at > known_as_of:
                continue
            if claim.valid_from is not None and claim.valid_from > as_of:
                continue
            if claim.valid_to is not None and claim.valid_to < as_of:
                continue
            applicable.append(claim)

        applicable.sort(key=lambda claim: claim.asserted_at)
        return applicable

    def exists(self, object_id: str) -> bool:
        """Injected into the validator to turn on referential checks."""
        return self.resolving_claim(object_id) is not None

    def history(self, object_id: str) -> list[Claim]:
        """Every claim about an object, oldest first, retractions included.

        This is what why_do_i_believe reads.
        """
        claims = list(self._by_object.get(object_id, []))
        claims.sort(key=lambda claim: claim.asserted_at)
        return claims

    def claim(self, claim_id: str) -> Claim:
        for candidate in self._claims:
            if candidate.id == claim_id:
                return candidate
        raise StoreError(f"no claim with id {claim_id!r}")

    def all_of_type(
        self,
        type_name: str,
        as_of: date | None = None,
        known_as_of: datetime | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Resolved bodies of every live object of a type, keyed by id."""
        if type_name not in self._by_type:
            return {}

        resolved: dict[str, dict[str, Any]] = {}
        for object_id in self._by_type[type_name]:
            body = self.resolve(object_id, as_of, known_as_of)
            if body is not None:
                resolved[object_id] = body
        return resolved

    def referrers(self, object_id: str) -> list[tuple[str, str, str]]:
        """Every object that currently points at this one.

        Returns (type_name, object_id, attribute_name) triples. Driven entirely
        by the link registry, so it covers types added after this was written.

        Scans all objects of every referring type. Fine at personal-network
        scale; the thing to replace first if the log ever gets large.
        """
        found: list[tuple[str, str, str]] = []

        type_name = self._type_of(object_id)
        if type_name is None:
            return found

        for link in self.registry.links_to(type_name):
            candidates = self.all_of_type(link.source_type)
            for candidate_id, body in candidates.items():
                value = body.get(link.attribute)
                if value is None:
                    continue
                if isinstance(value, list):
                    if object_id in value:
                        found.append((link.source_type, candidate_id, link.attribute))
                elif value == object_id:
                    found.append((link.source_type, candidate_id, link.attribute))

        return found

    def object_count(self) -> int:
        return len(self._by_object)

    def claim_count(self) -> int:
        return len(self._claims)

    # -- internals --------------------------------------------------------

    def type_of(self, object_id: str) -> str | None:
        """Which object type an id belongs to, or None if it is unknown."""
        for type_name, ids in self._by_type.items():
            if object_id in ids:
                return type_name
        return None

    def _type_of(self, object_id: str) -> str | None:
        return self.type_of(object_id)

    def _mint_claim_id(self) -> str:
        claim_id = f"claim:{self._next_sequence:06d}"
        self._next_sequence += 1
        return claim_id

    def _latest_claim_id(self, object_id: str) -> str | None:
        claims = self._by_object.get(object_id, [])
        if not claims:
            return None
        return claims[-1].id

    def _append(self, claim: Claim) -> None:
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(self._encode(claim), ensure_ascii=False))
            handle.write("\n")
        self._index(claim)

    def _index(self, claim: Claim) -> None:
        self._claims.append(claim)

        if claim.object_id not in self._by_object:
            self._by_object[claim.object_id] = []
        self._by_object[claim.object_id].append(claim)

        if claim.object_type not in self._by_type:
            self._by_type[claim.object_type] = set()
        self._by_type[claim.object_type].add(claim.object_id)

        if claim.is_retraction and claim.supersedes is not None:
            self._retracted_claim_ids.add(claim.supersedes)
            self._objects_with_retractions.add(claim.object_id)

        sequence = int(claim.id.split(":")[1])
        if sequence >= self._next_sequence:
            self._next_sequence = sequence + 1

    def _load(self) -> None:
        with open(self.path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    self._index(self._decode(json.loads(line)))
                except (ValueError, KeyError) as error:
                    raise StoreError(
                        f"{self.path} line {line_number} is corrupt: {error}"
                    )

    # -- serialization ----------------------------------------------------
    # Dates become date objects inside the validator, so they need converting
    # back on the way out. The registry says which attributes those are.

    def _encode(self, claim: Claim) -> dict[str, Any]:
        return {
            "id": claim.id,
            "object_type": claim.object_type,
            "object_id": claim.object_id,
            "asserted_at": claim.asserted_at.isoformat(),
            "valid_from": claim.valid_from.isoformat() if claim.valid_from else None,
            "valid_to": claim.valid_to.isoformat() if claim.valid_to else None,
            "source_kind": claim.source_kind,
            "source_person": claim.source_person,
            "source_note": claim.source_note,
            "supersedes": claim.supersedes,
            "is_retraction": claim.is_retraction,
            "changes": self._encode_body(claim.object_type, claim.changes),
            "body": self._encode_body(claim.object_type, claim.body),
        }

    def _encode_body(self, type_name: str, body: dict[str, Any]) -> dict[str, Any]:
        encoded: dict[str, Any] = {}
        for name, value in body.items():
            encoded[name] = _to_json(value)
        return encoded

    def _decode_body(self, type_name: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Turn stored JSON back into parsed values.

        `changes` is a partial body, so this cannot run the validator -- it
        would report every absent required attribute. The data was validated on
        the way in; loading only needs to restore types.
        """
        object_type = self.registry.type(type_name)

        decoded: dict[str, Any] = {}
        for name, value in raw.items():
            attribute = object_type.attributes.get(name)
            decoded[name] = _from_json(attribute, value)
        return decoded

    def _decode(self, raw: dict[str, Any]) -> Claim:
        object_type = raw["object_type"]

        return Claim(
            id=raw["id"],
            object_type=object_type,
            object_id=raw["object_id"],
            changes=self._decode_body(object_type, raw.get("changes") or {}),
            body=self._decode_body(object_type, raw.get("body") or {}),
            asserted_at=datetime.fromisoformat(raw["asserted_at"]),
            source_kind=raw["source_kind"],
            valid_from=date.fromisoformat(raw["valid_from"]) if raw.get("valid_from") else None,
            valid_to=date.fromisoformat(raw["valid_to"]) if raw.get("valid_to") else None,
            source_person=raw.get("source_person"),
            source_note=raw.get("source_note"),
            supersedes=raw.get("supersedes"),
            is_retraction=bool(raw.get("is_retraction", False)),
        )


def _from_json(attribute: Any, value: Any) -> Any:
    if attribute is None or value is None:
        return value
    if isinstance(value, list):
        converted = []
        for item in value:
            converted.append(_from_json(attribute, item))
        return converted
    if attribute.kind == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if attribute.kind == "datetime" and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _to_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        converted = []
        for item in value:
            converted.append(_to_json(item))
        return converted
    return value
