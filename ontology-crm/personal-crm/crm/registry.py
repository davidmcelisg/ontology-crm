"""In-memory representation of the ontology.

This module knows nothing about YAML, files, or parsing. It is the interface
every other component talks to: the validator, the claim store, the functions
layer, and the action layer all ask questions of a Registry and never touch the
declaration file themselves.

If you ever find `if object_type == "Person"` anywhere outside this package,
some part of the ontology has leaked back into code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Types that may be referenced by a `ref` attribute but are not declared under
# `object_types`. Claim is machinery rather than domain, so it lives in the
# claim envelope instead of the type list, but claims still point at claims.
BUILTIN_TYPES = ("Claim",)

# Cardinality values produced when links are derived from ref attributes.
MANY_TO_ONE = "many_to_one"
MANY_TO_MANY = "many_to_many"


class OntologyError(Exception):
    """Raised when the ontology declarations are malformed or inconsistent."""


class UnknownTypeError(OntologyError):
    pass


class UnknownAttributeError(OntologyError):
    pass


class UnknownActionError(OntologyError):
    pass


@dataclass(frozen=True)
class AttributeDef:
    """One attribute of an object type, normalized.

    A `list of ref` in the yaml collapses into a single AttributeDef with
    kind="ref" and is_list=True, so consumers never have to unwrap a nested
    declaration.
    """

    name: str
    kind: str                                  # string|text|int|bool|date|datetime|enum|ref
    is_list: bool = False
    required: bool = False
    enum_values: tuple[str, ...] = ()
    ref_targets: tuple[str, ...] = ()
    link_name: str | None = None
    inverse_name: str | None = None

    @property
    def is_ref(self) -> bool:
        return self.kind == "ref"

    @property
    def is_enum(self) -> bool:
        return self.kind == "enum"

    @property
    def cardinality(self) -> str | None:
        if not self.is_ref:
            return None
        if self.is_list:
            return MANY_TO_MANY
        return MANY_TO_ONE


@dataclass(frozen=True)
class AxiomDef:
    """A declared constraint that spans more than one attribute.

    kind is one of: distinct, not_equal, date_order.
    params carries the attribute names the axiom operates on.
    """

    kind: str
    params: dict[str, Any]


@dataclass(frozen=True)
class ObjectTypeDef:
    name: str
    attributes: dict[str, AttributeDef]
    title_attribute: str | None = None
    id_prefix: str | None = None
    axioms: tuple[AxiomDef, ...] = ()

    @property
    def prefix(self) -> str:
        """Leading segment of this type's ids: "org" in "org:bain-cdmx".

        Declared rather than derived, because the natural shorthand for a type
        is rarely its full lowercased name.
        """
        if self.id_prefix is not None:
            return self.id_prefix
        return self.name.lower()

    def attribute(self, name: str) -> AttributeDef:
        if name not in self.attributes:
            raise UnknownAttributeError(
                f"{self.name} has no attribute {name!r}. "
                f"Declared: {sorted(self.attributes)}"
            )
        return self.attributes[name]

    def required_attributes(self) -> list[AttributeDef]:
        found = []
        for attribute in self.attributes.values():
            if attribute.required:
                found.append(attribute)
        return found

    def ref_attributes(self) -> list[AttributeDef]:
        found = []
        for attribute in self.attributes.values():
            if attribute.is_ref:
                found.append(attribute)
        return found


@dataclass(frozen=True)
class LinkDef:
    """A permitted connection between two object types.

    Never written by hand. Derived by the loader from every ref attribute in
    every object type, which keeps the link registry impossible to desync from
    the attribute declarations.
    """

    name: str
    source_type: str
    attribute: str
    target_types: tuple[str, ...]
    cardinality: str
    inverse_name: str | None = None


@dataclass(frozen=True)
class ActionDef:
    """A named, typed, validated mutation.

    Exactly one of creates / updates / special is set. Parameters are already
    expanded: `parameters: inherit` in the yaml has been resolved into the real
    attribute list by the time an ActionDef exists.
    """

    name: str
    parameters: dict[str, AttributeDef]
    creates: str | None = None
    updates: str | None = None
    special: str | None = None
    axioms: tuple[AxiomDef, ...] = ()


@dataclass(frozen=True)
class FunctionDef:
    name: str
    returns: str
    parameters: tuple[str, ...] = ()


@dataclass
class Registry:
    """Everything the ontology declares, queryable.

    Built once at startup and then read-only in practice.
    """

    version: int
    config: dict[str, Any]
    object_types: dict[str, ObjectTypeDef]
    links: dict[str, LinkDef]
    actions: dict[str, ActionDef]
    functions: dict[str, FunctionDef]
    claim_envelope: ObjectTypeDef

    def __post_init__(self) -> None:
        # Two indexes the declaration file cannot answer directly.
        self._links_by_source: dict[str, list[LinkDef]] = {}
        self._links_by_target: dict[str, list[LinkDef]] = {}

        for link in self.links.values():
            if link.source_type not in self._links_by_source:
                self._links_by_source[link.source_type] = []
            self._links_by_source[link.source_type].append(link)

            for target in link.target_types:
                if target not in self._links_by_target:
                    self._links_by_target[target] = []
                self._links_by_target[target].append(link)

    # -- types ------------------------------------------------------------

    def type(self, name: str) -> ObjectTypeDef:
        if name not in self.object_types:
            raise UnknownTypeError(
                f"No object type named {name!r}. "
                f"Declared: {sorted(self.object_types)}"
            )
        return self.object_types[name]

    def has_type(self, name: str) -> bool:
        return name in self.object_types

    def is_referenceable(self, name: str) -> bool:
        return name in self.object_types or name in BUILTIN_TYPES

    def type_names(self) -> list[str]:
        return sorted(self.object_types)

    def prefix_for(self, type_name: str) -> str | None:
        """Id prefix for a type, or None for built-ins that have no objects."""
        if type_name not in self.object_types:
            return None
        return self.object_types[type_name].prefix

    # -- links ------------------------------------------------------------

    def link(self, name: str) -> LinkDef:
        if name not in self.links:
            raise OntologyError(f"No link named {name!r}.")
        return self.links[name]

    def links_from(self, type_name: str) -> list[LinkDef]:
        """Links whose source is this type. Used for forward traversal."""
        return list(self._links_by_source.get(type_name, []))

    def links_to(self, type_name: str) -> list[LinkDef]:
        """Links that point at this type. Used for reverse traversal.

        This is what makes "show me everything connected to Ana" answerable
        without anyone enumerating the possibilities.
        """
        return list(self._links_by_target.get(type_name, []))

    # -- actions ----------------------------------------------------------

    def action(self, name: str) -> ActionDef:
        if name not in self.actions:
            raise UnknownActionError(
                f"No action named {name!r}. Declared: {sorted(self.actions)}"
            )
        return self.actions[name]

    def action_names(self) -> list[str]:
        return sorted(self.actions)

    def action_parameters_with_envelope(self, name: str) -> dict[str, AttributeDef]:
        """Full parameter set an action accepts, including claim provenance.

        Every action carries who said it and when, so the action layer builds
        LLM schemas from this rather than from ActionDef.parameters alone.
        """
        action = self.action(name)

        merged: dict[str, AttributeDef] = {}
        for attribute_name, attribute in action.parameters.items():
            merged[attribute_name] = attribute

        for attribute_name, attribute in self.claim_envelope.attributes.items():
            # Identity and lineage are set by the store, not by the caller.
            if attribute_name in ("id", "object_type", "object_id", "is_retraction", "supersedes"):
                continue
            if attribute_name in merged:
                continue
            merged[attribute_name] = attribute

        return merged

    # -- config -----------------------------------------------------------

    @property
    def self_person_id(self) -> str:
        if "self_person_id" not in self.config:
            raise OntologyError("config.self_person_id is not set.")
        return self.config["self_person_id"]

    # -- introspection ----------------------------------------------------

    def describe(self) -> str:
        """Human-readable summary. Useful as a startup sanity check."""
        lines = [f"ontology v{self.version}"]

        lines.append(f"\nobject types ({len(self.object_types)}):")
        for type_name in self.type_names():
            object_type = self.type(type_name)
            required = len(object_type.required_attributes())
            refs = len(object_type.ref_attributes())
            lines.append(
                f"  {type_name:<14} {len(object_type.attributes):>2} attrs"
                f"  {required:>2} required  {refs:>2} refs"
                f"  {len(object_type.axioms)} axioms"
            )

        lines.append(f"\nlinks ({len(self.links)}), derived:")
        for link_name in sorted(self.links):
            link = self.links[link_name]
            targets = " | ".join(link.target_types)
            lines.append(
                f"  {link_name:<28} {link.source_type} -> {targets}"
                f"  ({link.cardinality})"
            )

        lines.append(f"\nactions ({len(self.actions)}):")
        for action_name in self.action_names():
            action = self.action(action_name)
            effect = action.creates or action.updates or action.special
            count = len(self.action_parameters_with_envelope(action_name))
            lines.append(f"  {action_name:<22} {effect:<18} {count} params")

        lines.append(f"\nfunctions ({len(self.functions)}):")
        for function_name in sorted(self.functions):
            lines.append(f"  {function_name}")

        return "\n".join(lines)
