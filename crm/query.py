"""Generic traversal over the object graph.

Contains no domain knowledge. There is no mention of Person, Affiliation, or
any other type in this file -- every edge it walks comes from the link registry,
which the loader derived from the ref attributes in ontology.yaml.

The consequence: add an object type tomorrow and everything here works on it
immediately, with no changes.

Domain judgements ("an unanswered outbound message is a debt") do not belong
here. They live in functions.py.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from .store import ClaimStore


@dataclass(frozen=True)
class Edge:
    """One hop in the graph, with the direction it was traversed."""

    source: str
    target: str
    link: str
    attribute: str
    direction: str      # "out" when source declares the ref, "in" otherwise

    def other_end(self, from_id: str) -> str:
        if from_id == self.source:
            return self.target
        return self.source


# ---------------------------------------------------------------------------
# hops
# ---------------------------------------------------------------------------


def outgoing(store: ClaimStore, object_id: str) -> list[Edge]:
    """Objects this one points at, via its own ref attributes."""
    type_name = store.type_of(object_id)
    if type_name is None:
        return []

    body = store.resolve(object_id)
    if body is None:
        return []

    object_type = store.registry.type(type_name)
    edges: list[Edge] = []

    for attribute in object_type.ref_attributes():
        value = body.get(attribute.name)
        if value is None:
            continue

        targets = value if isinstance(value, list) else [value]
        for target in targets:
            edges.append(
                Edge(
                    source=object_id,
                    target=target,
                    link=attribute.link_name,
                    attribute=attribute.name,
                    direction="out",
                )
            )

    return edges


def incoming(store: ClaimStore, object_id: str) -> list[Edge]:
    """Objects that point at this one. The reverse question the yaml never states."""
    edges: list[Edge] = []

    for source_type, source_id, attribute_name in store.referrers(object_id):
        object_type = store.registry.type(source_type)
        attribute = object_type.attribute(attribute_name)
        edges.append(
            Edge(
                source=source_id,
                target=object_id,
                link=attribute.link_name,
                attribute=attribute_name,
                direction="in",
            )
        )

    return edges


def neighbors(store: ClaimStore, object_id: str) -> list[Edge]:
    return outgoing(store, object_id) + incoming(store, object_id)


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


def find(store: ClaimStore, type_name: str, **equals: Any) -> dict[str, dict[str, Any]]:
    """Live objects of a type whose attributes match every keyword given.

    A value of None matches objects where the attribute is absent, which is how
    "current affiliations" is expressed: end_date=None.
    """
    matches: dict[str, dict[str, Any]] = {}

    for object_id, body in store.all_of_type(type_name).items():
        keep = True
        for attribute_name, wanted in equals.items():
            if body.get(attribute_name) != wanted:
                keep = False
                break
        if keep:
            matches[object_id] = body

    return matches


def where(
    store: ClaimStore,
    type_name: str,
    predicate: Callable[[str, dict[str, Any]], bool],
) -> dict[str, dict[str, Any]]:
    """Same as find, for conditions that aren't equality."""
    matches: dict[str, dict[str, Any]] = {}

    for object_id, body in store.all_of_type(type_name).items():
        if predicate(object_id, body):
            matches[object_id] = body

    return matches


def title(store: ClaimStore, object_id: str) -> str:
    """Readable label for an object, using whatever the type nominated.

    Types without a title attribute (or with an empty one) are named by who they
    connect: "Introduction by Teo Marin". That keeps path rendering from using
    a kind enum or a context sentence as if it were a person's name.
    """
    type_name = store.type_of(object_id)
    if type_name is None:
        return object_id

    body = store.resolve(object_id)
    if body is None:
        return object_id

    attribute_name = store.registry.type(type_name).title_attribute
    if attribute_name is not None:
        value = body.get(attribute_name)
        if value is not None and str(value).strip():
            return str(value)

    return _composed_title(store, type_name, object_id, body)


def _composed_title(
    store: ClaimStore, type_name: str, object_id: str, body: dict[str, Any]
) -> str:
    me = store.registry.self_person_id
    object_type = store.registry.type(type_name)

    for attribute in object_type.ref_attributes():
        value = body.get(attribute.name)
        if value is None:
            continue
        refs = value if isinstance(value, list) else [value]
        for ref in refs:
            if not isinstance(ref, str):
                continue
            if ref == object_id or ref == me:
                continue
            label = title(store, ref)
            if label:
                return f"{type_name} by {label}"

    return type_name


def descendants(store: ClaimStore, object_id: str, attribute: str) -> set[str]:
    """Transitive closure over one self-referencing attribute.

    Used for organization hierarchies, but it knows nothing about organizations
    -- pass any attribute whose type points at its own type.
    """
    found = {object_id}
    pending = [object_id]

    while pending:
        current = pending.pop()
        for edge in incoming(store, current):
            if edge.attribute != attribute:
                continue
            if edge.source in found:
                continue
            found.add(edge.source)
            pending.append(edge.source)

    return found


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def shortest_path(
    store: ClaimStore,
    source_id: str,
    target_id: str,
    max_hops: int = 6,
) -> list[Edge] | None:
    """Breadth-first search over every link in the registry.

    Returns the edges of the shortest route, or None. Because it walks the
    generic neighbor function, a route can run through any object type -- a
    path to a stranger may pass through an Introduction, an Affiliation, or a
    Commitment without this function knowing those words.
    """
    if source_id == target_id:
        return []

    visited = {source_id}
    queue: deque[tuple[str, list[Edge]]] = deque()
    queue.append((source_id, []))

    while queue:
        current_id, path_so_far = queue.popleft()
        if len(path_so_far) >= max_hops:
            continue

        for edge in neighbors(store, current_id):
            next_id = edge.other_end(current_id)
            if next_id in visited:
                continue

            extended = path_so_far + [edge]
            if next_id == target_id:
                return extended

            visited.add(next_id)
            queue.append((next_id, extended))

    return None


def describe_path(store: ClaimStore, path: list[Edge], start_id: str) -> str:
    """Render a route as readable text.

    The start must be passed in. An edge does not know which way it was walked:
    when a path runs backwards along a ref, the node you came from is the edge's
    target rather than its source.
    """
    if not path:
        return title(store, start_id)

    current = start_id
    parts = [title(store, current)]

    for edge in path:
        next_id = edge.other_end(current)
        parts.append(f"--[{edge.link}]--> {title(store, next_id)}")
        current = next_id

    return " ".join(parts)
