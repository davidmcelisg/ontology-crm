"""Reads ontology.yaml and builds a Registry.

The only module in the system that knows the declaration file format exists.
Everything downstream talks to the Registry instead.

Three jobs, in order:
  1. Parse the file.
  2. Validate the ontology itself against the meta-schema, accumulating every
     error rather than stopping at the first.
  3. Derive what the file does not state: the link registry, the reverse link
     index, and the expanded action parameter lists.

Run directly for a startup sanity check:
    python loader.py ontology.yaml
"""

from __future__ import annotations

import sys
from typing import Any

import yaml

from .registry import (
    BUILTIN_TYPES,
    ActionDef,
    AttributeDef,
    AxiomDef,
    FunctionDef,
    LinkDef,
    ObjectTypeDef,
    OntologyError,
    Registry,
)


# The meta-schema. Hardcoded here rather than read from the file, because each
# entry corresponds to a branch in the validator: these are code, not data.
SCALAR_KINDS = ("string", "text", "int", "bool", "date", "datetime")
ENUM_KIND = "enum"
REF_KIND = "ref"
LIST_KIND = "list"
ATTRIBUTE_KINDS = SCALAR_KINDS + (ENUM_KIND, REF_KIND)

# Axiom kinds and the param names each one requires.
AXIOM_SIGNATURES = {
    "distinct": ("attributes",),
    "not_equal": ("a", "b"),
    "date_order": ("earlier", "later"),
}

TOP_LEVEL_KEYS = (
    "_meta",
    "config",
    "object_types",
    "claim_envelope",
    "actions",
    "functions",
)


class _Errors:
    """Accumulates problems so the user sees all of them in one run."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def add(self, where: str, message: str) -> None:
        self.messages.append(f"{where}: {message}")

    def raise_if_any(self) -> None:
        if not self.messages:
            return
        joined = "\n  ".join(self.messages)
        raise OntologyError(
            f"{len(self.messages)} problem(s) in the ontology declarations:\n  {joined}"
        )


def load_ontology(path: str) -> Registry:
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if not isinstance(raw, dict):
        raise OntologyError(f"{path} did not parse into a mapping.")

    errors = _Errors()

    for key in raw:
        if key not in TOP_LEVEL_KEYS:
            errors.add("top level", f"unexpected key {key!r}")

    meta = raw.get("_meta", {})
    version = meta.get("version", 0)
    _check_meta_matches_code(meta, errors)

    config = raw.get("config", {})
    if "self_person_id" not in config:
        errors.add("config", "self_person_id is required")

    # -- object types -----------------------------------------------------

    object_types: dict[str, ObjectTypeDef] = {}
    raw_types = raw.get("object_types", {})
    if not isinstance(raw_types, dict) or not raw_types:
        errors.add("object_types", "must be a non-empty mapping")
        raw_types = {}

    for type_name, spec in raw_types.items():
        object_types[type_name] = _parse_object_type(type_name, spec, errors)

    # -- claim envelope ---------------------------------------------------

    raw_envelope = raw.get("claim_envelope", {})
    claim_envelope = _parse_object_type("Claim", raw_envelope, errors)

    # -- reference resolution ---------------------------------------------
    # Deferred until every type is known, so forward references are legal.

    known = set(object_types) | set(BUILTIN_TYPES)
    _check_ref_targets(object_types, known, errors)
    _check_ref_targets({"Claim": claim_envelope}, known, errors)
    _check_title_attributes(object_types, errors)
    _check_id_prefixes(object_types, errors)
    _check_axioms(object_types, errors)
    _check_axioms({"Claim": claim_envelope}, errors)

    # -- derived: the link registry ---------------------------------------

    links = _derive_links(object_types, errors)

    # -- actions ----------------------------------------------------------

    actions: dict[str, ActionDef] = {}
    for action_name, spec in raw.get("actions", {}).items():
        actions[action_name] = _parse_action(action_name, spec, object_types, errors)

    # -- functions --------------------------------------------------------

    functions: dict[str, FunctionDef] = {}
    for function_name, spec in raw.get("functions", {}).items():
        spec = spec or {}
        functions[function_name] = FunctionDef(
            name=function_name,
            returns=str(spec.get("returns", "unspecified")),
            parameters=tuple(spec.get("parameters", ())),
        )

    errors.raise_if_any()

    return Registry(
        version=version,
        config=config,
        object_types=object_types,
        links=links,
        actions=actions,
        functions=functions,
        claim_envelope=claim_envelope,
    )


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def _parse_object_type(type_name: str, spec: Any, errors: _Errors) -> ObjectTypeDef:
    if not isinstance(spec, dict):
        errors.add(type_name, "must be a mapping")
        spec = {}

    raw_attributes = spec.get("attributes", {})
    if not isinstance(raw_attributes, dict) or not raw_attributes:
        errors.add(type_name, "must declare at least one attribute")
        raw_attributes = {}

    attributes: dict[str, AttributeDef] = {}
    for attribute_name, attribute_spec in raw_attributes.items():
        attributes[attribute_name] = _parse_attribute(
            f"{type_name}.{attribute_name}", type_name, attribute_name,
            attribute_spec, errors,
        )

    axioms = []
    for axiom_spec in spec.get("axioms", ()):
        parsed = _parse_axiom(type_name, axiom_spec, errors)
        if parsed is not None:
            axioms.append(parsed)

    return ObjectTypeDef(
        name=type_name,
        attributes=attributes,
        title_attribute=spec.get("title_attribute"),
        id_prefix=spec.get("id_prefix"),
        title_is_identity=bool(spec.get("title_is_identity", False)),
        axioms=tuple(axioms),
    )


def _parse_attribute(
    where: str, owner: str, name: str, spec: Any, errors: _Errors
) -> AttributeDef:
    if not isinstance(spec, dict):
        errors.add(where, "must be a mapping")
        spec = {}

    declared_kind = spec.get("type")
    is_list = False

    # Flatten `list of X` into one definition carrying an is_list flag, so no
    # consumer downstream has to unwrap a nested declaration.
    if declared_kind == LIST_KIND:
        is_list = True
        declared_kind = spec.get("of")
        if declared_kind is None:
            errors.add(where, "list attributes must declare `of`")
            declared_kind = "string"

    if declared_kind not in ATTRIBUTE_KINDS:
        errors.add(
            where,
            f"unknown type {declared_kind!r}, expected one of {list(ATTRIBUTE_KINDS)}",
        )
        declared_kind = "string"

    enum_values: tuple[str, ...] = ()
    if declared_kind == ENUM_KIND:
        values = spec.get("values")
        if not values:
            errors.add(where, "enum attributes must declare `values`")
        else:
            enum_values = tuple(values)

    ref_targets: tuple[str, ...] = ()
    link_name = spec.get("link_name")
    inverse_name = spec.get("inverse_name")

    if declared_kind == REF_KIND:
        target = spec.get("target")
        if target is None:
            errors.add(where, "ref attributes must declare `target`")
        elif isinstance(target, str):
            ref_targets = (target,)
        else:
            ref_targets = tuple(target)

        if link_name is None:
            link_name = f"{owner.lower()}_{name}"
    else:
        if link_name is not None or inverse_name is not None:
            errors.add(where, "link_name / inverse_name only apply to ref attributes")

    return AttributeDef(
        name=name,
        kind=declared_kind,
        is_list=is_list,
        required=bool(spec.get("required", False)),
        enum_values=enum_values,
        ref_targets=ref_targets,
        link_name=link_name,
        inverse_name=inverse_name,
    )


def _parse_axiom(where: str, spec: Any, errors: _Errors) -> AxiomDef | None:
    if not isinstance(spec, dict) or "kind" not in spec:
        errors.add(where, "each axiom needs a `kind`")
        return None

    kind = spec["kind"]
    if kind not in AXIOM_SIGNATURES:
        errors.add(where, f"unknown axiom kind {kind!r}")
        return None

    params = {}
    for key, value in spec.items():
        if key != "kind":
            params[key] = value

    for required_param in AXIOM_SIGNATURES[kind]:
        if required_param not in params:
            errors.add(where, f"axiom {kind} requires `{required_param}`")

    return AxiomDef(kind=kind, params=params)


def _parse_action(
    action_name: str,
    spec: Any,
    object_types: dict[str, ObjectTypeDef],
    errors: _Errors,
) -> ActionDef:
    if not isinstance(spec, dict):
        errors.add(f"action {action_name}", "must be a mapping")
        spec = {}

    creates = spec.get("creates")
    updates = spec.get("updates")
    special = spec.get("special")

    effects = []
    for effect in (creates, updates, special):
        if effect is not None:
            effects.append(effect)
    if len(effects) != 1:
        errors.add(
            f"action {action_name}",
            "must declare exactly one of creates / updates / special",
        )

    for target in (creates, updates):
        if target is not None and target not in object_types:
            errors.add(f"action {action_name}", f"unknown object type {target!r}")

    raw_parameters = spec.get("parameters")
    parameters: dict[str, AttributeDef] = {}

    if raw_parameters == "inherit":
        # Take the parameter list from the object type this action creates,
        # so attributes are never restated in two places.
        if creates is None:
            errors.add(
                f"action {action_name}",
                "`parameters: inherit` requires `creates`",
            )
        elif creates in object_types:
            for attribute_name, attribute in object_types[creates].attributes.items():
                parameters[attribute_name] = attribute
    elif isinstance(raw_parameters, dict):
        for parameter_name, parameter_spec in raw_parameters.items():
            parameters[parameter_name] = _parse_attribute(
                f"action {action_name}.{parameter_name}",
                action_name,
                parameter_name,
                parameter_spec,
                errors,
            )
    else:
        errors.add(
            f"action {action_name}",
            "`parameters` must be a mapping or the string `inherit`",
        )

    axioms = []
    for axiom_spec in spec.get("axioms", ()):
        parsed = _parse_axiom(f"action {action_name}", axiom_spec, errors)
        if parsed is not None:
            axioms.append(parsed)

    return ActionDef(
        name=action_name,
        parameters=parameters,
        creates=creates,
        updates=updates,
        special=special,
        axioms=tuple(axioms),
    )


# ---------------------------------------------------------------------------
# cross-checks
# ---------------------------------------------------------------------------


def _check_meta_matches_code(meta: Any, errors: _Errors) -> None:
    """The file documents the meta-schema; this module owns it. Keep them honest."""
    if not isinstance(meta, dict):
        return

    documented = set(meta.get("attribute_types", ()))
    if documented:
        implemented = set(ATTRIBUTE_KINDS) | {LIST_KIND}
        if documented != implemented:
            errors.add(
                "_meta.attribute_types",
                f"documents {sorted(documented)} but the loader implements "
                f"{sorted(implemented)}",
            )

    documented_axioms = set(meta.get("axiom_kinds", ()))
    if documented_axioms and documented_axioms != set(AXIOM_SIGNATURES):
        errors.add(
            "_meta.axiom_kinds",
            f"documents {sorted(documented_axioms)} but the loader implements "
            f"{sorted(AXIOM_SIGNATURES)}",
        )


def _check_ref_targets(
    object_types: dict[str, ObjectTypeDef], known: set[str], errors: _Errors
) -> None:
    for type_name, object_type in object_types.items():
        for attribute in object_type.attributes.values():
            for target in attribute.ref_targets:
                if target not in known:
                    errors.add(
                        f"{type_name}.{attribute.name}",
                        f"points at unknown type {target!r}",
                    )


def _check_title_attributes(
    object_types: dict[str, ObjectTypeDef], errors: _Errors
) -> None:
    for type_name, object_type in object_types.items():
        title = object_type.title_attribute
        if title is not None and title not in object_type.attributes:
            errors.add(type_name, f"title_attribute {title!r} is not an attribute")


def _check_id_prefixes(
    object_types: dict[str, ObjectTypeDef], errors: _Errors
) -> None:
    """Prefixes identify a type inside an id, so two types cannot share one."""
    claimed: dict[str, str] = {}
    for type_name, object_type in object_types.items():
        prefix = object_type.prefix
        if prefix in claimed:
            errors.add(type_name, f"id_prefix {prefix!r} already used by {claimed[prefix]}")
            continue
        claimed[prefix] = type_name


def _check_axioms(object_types: dict[str, ObjectTypeDef], errors: _Errors) -> None:
    """Every attribute an axiom names must actually exist on the type."""
    for type_name, object_type in object_types.items():
        for axiom in object_type.axioms:
            named = []
            if axiom.kind == "distinct":
                named = list(axiom.params.get("attributes", ()))
            elif axiom.kind == "not_equal":
                named = [axiom.params.get("a"), axiom.params.get("b")]
            elif axiom.kind == "date_order":
                named = [axiom.params.get("earlier"), axiom.params.get("later")]

            for attribute_name in named:
                if attribute_name is None:
                    continue
                if attribute_name not in object_type.attributes:
                    errors.add(
                        f"{type_name} axiom {axiom.kind}",
                        f"names unknown attribute {attribute_name!r}",
                    )


def _derive_links(
    object_types: dict[str, ObjectTypeDef], errors: _Errors
) -> dict[str, LinkDef]:
    """Build the link registry by walking every ref attribute.

    Nothing in the yaml declares links directly. That is deliberate: a separate
    link section would be a second source of truth and would drift.
    """
    links: dict[str, LinkDef] = {}

    for type_name, object_type in object_types.items():
        for attribute in object_type.attributes.values():
            if not attribute.is_ref:
                continue

            link_name = attribute.link_name
            if link_name in links:
                existing = links[link_name]
                errors.add(
                    f"{type_name}.{attribute.name}",
                    f"link_name {link_name!r} already used by "
                    f"{existing.source_type}.{existing.attribute}",
                )
                continue

            links[link_name] = LinkDef(
                name=link_name,
                source_type=type_name,
                attribute=attribute.name,
                target_types=attribute.ref_targets,
                cardinality=attribute.cardinality,
                inverse_name=attribute.inverse_name,
            )

    return links


# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python loader.py <path-to-ontology.yaml>")
        return 2

    try:
        registry = load_ontology(argv[1])
    except OntologyError as error:
        print(error)
        return 1

    print(registry.describe())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
