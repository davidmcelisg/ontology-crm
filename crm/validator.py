"""Validates and normalizes data against the ontology.

Knows nothing about any specific type. Every rule it applies comes from the
registry, so adding an object type to ontology.yaml extends validation for free.

Two entry points, both backed by the same machinery:
    validate_object(registry, "Affiliation", body)
    validate_action(registry, "RecordInteraction", params)

Both return (normalized, errors). `normalized` holds parsed values: date
strings have become date objects. Validation and parsing are the same act, so
the validator hands back what it produced rather than making a later step
re-parse and possibly skip the check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable

from .registry import AttributeDef, AxiomDef, Registry


# Injected by the claim store when referential checks are wanted.
# Signature: exists(object_id) -> bool
ExistsFn = Callable[[str], bool]


@dataclass(frozen=True)
class ValidationError:
    path: str          # "Affiliation.kind" or "RecordInteraction.participants[1]"
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


def validate_object(
    registry: Registry,
    type_name: str,
    body: dict[str, Any],
    exists: ExistsFn | None = None,
) -> tuple[dict[str, Any], list[ValidationError]]:
    object_type = registry.type(type_name)
    return _validate(
        label=type_name,
        attributes=object_type.attributes,
        axioms=object_type.axioms,
        body=body,
        registry=registry,
        exists=exists,
    )


def validate_action(
    registry: Registry,
    action_name: str,
    params: dict[str, Any],
    exists: ExistsFn | None = None,
) -> tuple[dict[str, Any], list[ValidationError]]:
    action = registry.action(action_name)
    attributes = registry.action_parameters_with_envelope(action_name)
    return _validate(
        label=action_name,
        attributes=attributes,
        axioms=action.axioms,
        body=params,
        registry=registry,
        exists=exists,
    )


def validate_claim_envelope(
    registry: Registry,
    envelope: dict[str, Any],
    exists: ExistsFn | None = None,
) -> tuple[dict[str, Any], list[ValidationError]]:
    return _validate(
        label="Claim",
        attributes=registry.claim_envelope.attributes,
        axioms=registry.claim_envelope.axioms,
        body=envelope,
        registry=registry,
        exists=exists,
    )


# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------


def _validate(
    label: str,
    attributes: dict[str, AttributeDef],
    axioms: tuple[AxiomDef, ...],
    body: dict[str, Any],
    registry: Registry,
    exists: ExistsFn | None,
) -> tuple[dict[str, Any], list[ValidationError]]:
    errors: list[ValidationError] = []
    normalized: dict[str, Any] = {}

    # 1. anything present that shouldn't be
    for name in body:
        if name not in attributes:
            errors.append(
                ValidationError(
                    f"{label}.{name}",
                    f"not a declared attribute. Expected one of {sorted(attributes)}",
                )
            )

    # 2. anything required that's missing
    for name, attribute in attributes.items():
        if not attribute.required:
            continue
        if name not in body or body[name] is None:
            errors.append(ValidationError(f"{label}.{name}", "is required"))

    # 3. each value against its declared type
    for name, raw_value in body.items():
        if name not in attributes:
            continue
        if raw_value is None:
            continue

        attribute = attributes[name]
        path = f"{label}.{name}"

        if attribute.is_list:
            if not isinstance(raw_value, list):
                errors.append(ValidationError(path, "must be a list"))
                continue
            if attribute.required and len(raw_value) == 0:
                errors.append(ValidationError(path, "must not be empty"))
                continue

            cleaned_items = []
            for index, item in enumerate(raw_value):
                value, item_errors = _check_value(
                    f"{path}[{index}]", attribute, item, registry, exists
                )
                errors.extend(item_errors)
                cleaned_items.append(value)
            normalized[name] = cleaned_items
        else:
            if isinstance(raw_value, list):
                errors.append(ValidationError(path, "must not be a list"))
                continue
            value, value_errors = _check_value(
                path, attribute, raw_value, registry, exists
            )
            errors.extend(value_errors)
            normalized[name] = value

    # 4. constraints that span attributes
    for axiom in axioms:
        errors.extend(_check_axiom(label, axiom, normalized))

    return normalized, errors


def _check_value(
    path: str,
    attribute: AttributeDef,
    value: Any,
    registry: Registry,
    exists: ExistsFn | None,
) -> tuple[Any, list[ValidationError]]:
    """Check one scalar. Returns the parsed value and any errors.

    One branch per primitive in the meta-schema. This function is the reason
    the meta-schema lives in code: every entry there is a case here.
    """
    kind = attribute.kind

    if kind in ("string", "text"):
        if not isinstance(value, str):
            return value, [ValidationError(path, f"must be text, got {type(value).__name__}")]
        return value, []

    if kind == "int":
        # bool is a subclass of int in Python, so exclude it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            return value, [ValidationError(path, "must be a whole number")]
        return value, []

    if kind == "bool":
        if not isinstance(value, bool):
            return value, [ValidationError(path, "must be true or false")]
        return value, []

    if kind == "date":
        return _parse_date(path, value)

    if kind == "datetime":
        return _parse_datetime(path, value)

    if kind == "enum":
        if value not in attribute.enum_values:
            return value, [
                ValidationError(
                    path,
                    f"{value!r} is not permitted. Allowed: {list(attribute.enum_values)}",
                )
            ]
        return value, []

    if kind == "ref":
        return _check_ref(path, attribute, value, registry, exists)

    return value, [ValidationError(path, f"unhandled attribute kind {kind!r}")]


def _parse_date(path: str, value: Any) -> tuple[Any, list[ValidationError]]:
    if isinstance(value, datetime):
        return value.date(), []
    if isinstance(value, date):
        return value, []
    if isinstance(value, str):
        try:
            return date.fromisoformat(value), []
        except ValueError:
            return value, [ValidationError(path, f"{value!r} is not a date (expected YYYY-MM-DD)")]
    return value, [ValidationError(path, "must be a date")]


def _parse_datetime(path: str, value: Any) -> tuple[Any, list[ValidationError]]:
    if isinstance(value, datetime):
        return _as_utc(value), []
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc), []
    if isinstance(value, str):
        try:
            return _as_utc(datetime.fromisoformat(value)), []
        except ValueError:
            return value, [ValidationError(path, f"{value!r} is not a timestamp")]
    return value, [ValidationError(path, "must be a timestamp")]


def _as_utc(value: datetime) -> datetime:
    """A timestamp with no zone is ambiguous. Assume UTC and say so."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _check_ref(
    path: str,
    attribute: AttributeDef,
    value: Any,
    registry: Registry,
    exists: ExistsFn | None,
) -> tuple[Any, list[ValidationError]]:
    if not isinstance(value, str):
        return value, [ValidationError(path, "must be an object id")]

    errors: list[ValidationError] = []

    # Ids are conventionally "<lowercased type>:<slug>". When the prefix is
    # present it tells us the referenced type without touching storage, which
    # catches an organization id sitting in a person slot.
    if ":" in value:
        prefix = value.split(":", 1)[0]

        allowed_prefixes = []
        for target in attribute.ref_targets:
            target_prefix = registry.prefix_for(target)
            if target_prefix is not None:
                allowed_prefixes.append(target_prefix)

        if allowed_prefixes and prefix not in allowed_prefixes:
            errors.append(
                ValidationError(
                    path,
                    f"id {value!r} looks like a {prefix!r}, expected {allowed_prefixes}",
                )
            )

    if exists is not None and not exists(value):
        errors.append(ValidationError(path, f"no object with id {value!r}"))

    return value, errors


def _check_axiom(
    label: str, axiom: AxiomDef, normalized: dict[str, Any]
) -> list[ValidationError]:
    """Apply one declared constraint. Absent values are skipped, not failed --
    requiredness is a separate rule and shouldn't be reported twice.
    """
    if axiom.kind == "distinct":
        names = list(axiom.params.get("attributes", ()))

        seen: dict[Any, str] = {}
        for name in names:
            value = normalized.get(name)
            if value is None:
                continue
            if value in seen:
                return [
                    ValidationError(
                        f"{label}.{name}",
                        f"must differ from {seen[value]} (both are {value!r})",
                    )
                ]
            seen[value] = name
        return []

    if axiom.kind == "not_equal":
        first = axiom.params.get("a")
        second = axiom.params.get("b")
        left = normalized.get(first)
        right = normalized.get(second)
        if left is not None and left == right:
            return [
                ValidationError(
                    f"{label}.{second}", f"must differ from {first} (both are {left!r})"
                )
            ]
        return []

    if axiom.kind == "date_order":
        earlier_name = axiom.params.get("earlier")
        later_name = axiom.params.get("later")
        earlier = normalized.get(earlier_name)
        later = normalized.get(later_name)

        if earlier is None or later is None:
            return []
        if not isinstance(earlier, (date, datetime)) or not isinstance(later, (date, datetime)):
            return []
        if later < earlier:
            return [
                ValidationError(
                    f"{label}.{later_name}",
                    f"{later} is before {earlier_name} ({earlier})",
                )
            ]
        return []

    return [ValidationError(label, f"unhandled axiom kind {axiom.kind!r}")]
