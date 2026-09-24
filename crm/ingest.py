"""Plain text in, validated Actions out.

The thinnest file in the project, because everything hard already exists below
it. The registry generates the action schemas, the validator rejects anything
malformed, and the executor is the only write path. The model's entire job is
choosing an action name and filling parameters.

Two phases, always:

    propose(store, text, complete)  -> a list of actions, already validated
    apply(store, proposal)          -> executes them

Nothing is written between those calls. Ambiguous names are resolved by the
model using a roster of what you already know, and its choices are rendered by
name in the proposal so you can see who it picked before confirming.

The LLM is injected rather than imported, so the pipeline runs with a stub and
no API key.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from . import query
from .actions import ActionError, ActionResult, execute, slugify, with_envelope_defaults
from .registry import AttributeDef, Registry
from .store import ClaimStore
from .validator import ValidationError, validate_action

# complete(system_prompt, user_prompt) -> raw model text
CompleteFn = Callable[[str, str], str]

ROSTER_LIMIT_PER_TYPE = 60
NEIGHBOURS_PER_ENTRY = 3


@dataclass
class ProposedAction:
    name: str
    params: dict[str, Any]
    reason: str = ""
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass
class Proposal:
    text: str
    actions: list[ProposedAction]
    raw: str = ""

    @property
    def valid(self) -> bool:
        if not self.actions:
            return False
        for action in self.actions:
            if not action.valid:
                return False
        return True


# ---------------------------------------------------------------------------
# schema generation
# ---------------------------------------------------------------------------


def action_schemas(registry: Registry) -> dict[str, Any]:
    """JSON schemas for every declared action, built from the ontology.

    Not written by hand and not in the prompt text. Add an action to the yaml
    and the model can use it on the next run with no prompt change.
    """
    schemas: dict[str, Any] = {}

    for action_name in registry.action_names():
        action = registry.action(action_name)
        parameters = registry.action_parameters_with_envelope(action_name)

        properties: dict[str, Any] = {}
        required: list[str] = []

        for name, attribute in parameters.items():
            # asserted_at is when *I* learned something, which for an ingested
            # note is always now. The store stamps it at execution time, so it
            # is not offered here and is discarded if the model sends it anyway.
            if name == "asserted_at":
                continue
            properties[name] = _attribute_schema(attribute)
            if attribute.required and name not in ("asserted_at", "source_kind"):
                required.append(name)

        effect = action.creates or action.updates or action.special
        schemas[action_name] = {
            "effect": effect,
            "properties": properties,
            "required": required,
        }

    return schemas


def _attribute_schema(attribute: AttributeDef) -> dict[str, Any]:
    inner: dict[str, Any] = {}

    if attribute.kind in ("string", "text"):
        inner["type"] = "string"
    elif attribute.kind == "int":
        inner["type"] = "integer"
    elif attribute.kind == "bool":
        inner["type"] = "boolean"
    elif attribute.kind == "date":
        inner["type"] = "string"
        inner["format"] = "YYYY-MM-DD"
    elif attribute.kind == "datetime":
        inner["type"] = "string"
        inner["format"] = "YYYY-MM-DDTHH:MM:SS"
    elif attribute.kind == "enum":
        inner["type"] = "string"
        inner["enum"] = list(attribute.enum_values)
    elif attribute.kind == "ref":
        inner["type"] = "string"
        inner["id_of"] = list(attribute.ref_targets)

    if attribute.is_list:
        return {"type": "array", "items": inner}
    return inner


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


def roster(store: ClaimStore, limit: int = ROSTER_LIMIT_PER_TYPE) -> dict[str, list[str]]:
    """What the model already knows about, so it reuses ids instead of duplicating.

    Each entry carries a few neighbours for disambiguation, found by walking the
    link registry. That is what lets the model tell two people with the same
    first name apart: one is linked to a recruiter role, the other is not.
    """
    listing: dict[str, list[str]] = {}

    for type_name in store.registry.type_names():
        objects = store.all_of_type(type_name)
        if not objects:
            continue

        lines: list[str] = []
        for object_id in sorted(objects)[:limit]:
            body = objects[object_id]
            label = query.title(store, object_id)

            parts = [f"{object_id} | {label}"]

            aliases = body.get("aliases") or []
            if aliases:
                parts.append("aka " + ", ".join(str(alias) for alias in aliases))

            context = _neighbour_labels(store, object_id)
            if context:
                parts.append("linked to " + "; ".join(context))

            lines.append("  " + " | ".join(parts))

        listing[type_name] = lines

    return listing


def _neighbour_labels(store: ClaimStore, object_id: str) -> list[str]:
    labels: list[str] = []

    for edge in query.neighbors(store, object_id):
        if len(labels) >= NEIGHBOURS_PER_ENTRY:
            break
        other = edge.other_end(object_id)
        label = query.title(store, other)
        if label and label != other:
            labels.append(label)

    return labels


def id_rules(registry: Registry) -> list[str]:
    rules: list[str] = []
    for type_name in registry.type_names():
        prefix = registry.type(type_name).prefix
        title_attribute = registry.type(type_name).title_attribute
        if title_attribute is None:
            continue
        rules.append(f"  {type_name}: {prefix}:<slug of {title_attribute}>")
    return rules


# ---------------------------------------------------------------------------
# prompting
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """You turn notes about someone's professional network into a list of Actions.

Respond with JSON only. No prose, no markdown fences. The shape is:

{"actions": [{"action": "<name>", "reason": "<short>", "params": {...}}]}

Rules:
- Use only the action names and parameters given below. Anything else is rejected.
- Prefer an existing id from the roster over creating a new object. Two mentions
  of the same person must resolve to the same id.
- When a name is ambiguous, pick the most likely candidate using the linked
  context in the roster, and say why in "reason". A wrong guess is correctable,
  so choose rather than refusing.
- To refer to something not in the roster, emit its Create action first, then use
  the id it will receive. Ids are deterministic, formed from the title attribute.
- Set source_kind to "prompt". Set source_person only when the note says who told
  you something.
- Record only what the note states. Do not infer employers, dates or titles that
  are not there.
- Order actions so that anything referenced is created before it is used.
"""


def build_user_prompt(store: ClaimStore, text: str, today: str) -> str:
    registry = store.registry
    schemas = action_schemas(registry)

    sections = [f"Today is {today}.", "", "AVAILABLE ACTIONS:", json.dumps(schemas, indent=1)]

    sections.extend(["", "ID FORMAT:"])
    sections.extend(id_rules(registry))

    sections.extend(["", "ALREADY KNOWN:"])
    listing = roster(store)
    for type_name in sorted(listing):
        sections.append(f" {type_name}:")
        sections.extend(listing[type_name])

    sections.extend(["", f"SELF: {registry.self_person_id}", "", "NOTE:", text])
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# proposal
# ---------------------------------------------------------------------------


def propose(
    store: ClaimStore,
    text: str,
    complete: CompleteFn,
    today: str | None = None,
) -> Proposal:
    """Ask the model for actions, then validate them before anyone sees them."""
    from datetime import date as _date

    if today is None:
        today = _date.today().isoformat()

    raw = complete(SYSTEM_PROMPT, build_user_prompt(store, text, today))
    parsed = _parse_json(raw)

    actions: list[ProposedAction] = []
    for entry in parsed.get("actions", []):
        name = entry.get("action", "")
        params = entry.get("params", {}) or {}
        reason = entry.get("reason", "")

        # A model told "today is 2026-09-23" tends to answer with the bare date,
        # which parses to midnight. That sorts *before* claims written earlier
        # the same day, so an update would silently lose to the value it meant
        # to supersede. Belief time is the store's to assign, not the model's.
        params.pop("asserted_at", None)

        if not store.registry.has_action(name):
            actions.append(
                ProposedAction(name, params, reason,
                               [ValidationError(name or "?", "not a declared action")])
            )
            continue

        # Same defaults the executor applies, so a dry run and a real run
        # agree. No existence check: an earlier action in this batch may create
        # the object a later one references.
        _, errors = validate_action(store.registry, name, with_envelope_defaults(params))
        actions.append(ProposedAction(name, params, reason, errors))

    return Proposal(text=text, actions=actions, raw=raw)


def apply(store: ClaimStore, proposal: Proposal) -> list[ActionResult]:
    """Execute a confirmed proposal, in order.

    The whole batch is checked before any of it runs. Checking each action as
    it came would leave the valid ones ahead of a bad one already on disk, and
    the log is append-only, so there would be nothing to roll back.

    This covers everything the validator can see on its own. It does not make
    the batch transactional: an action can still fail at execution time, when
    referential checks run against the store, and by then its predecessors are
    written. Undoing those means retracting them.

    The model refers to objects it is about to create by the id it expects them
    to get. If minting appends a suffix (a second "Coffee" interaction), later
    references are rewritten to the id actually minted.
    """
    for proposed in proposal.actions:
        if not proposed.valid:
            raise ActionError(f"{proposed.name} did not validate; nothing applied")

    results: list[ActionResult] = []
    remap: dict[str, str] = {}

    for proposed in proposal.actions:
        params = _remap_ids(proposed.params, remap)
        result = execute(store, proposed.name, params)
        results.append(result)

        if result.created:
            expected = _expected_id(store, proposed.name, params)
            if expected is not None and expected != result.object_id:
                remap[expected] = result.object_id

    return results


def _expected_id(store: ClaimStore, action_name: str, params: dict[str, Any]) -> str | None:
    """The id the model would have predicted for a create, before any suffix."""
    type_name = store.registry.action(action_name).creates
    if type_name is None:
        return None
    object_type = store.registry.type(type_name)
    title = params.get(object_type.title_attribute) if object_type.title_attribute else None
    slug = slugify(title) if isinstance(title, str) else ""
    if not slug:
        return None
    return f"{object_type.prefix}:{slug}"


def _remap_ids(value: Any, remap: dict[str, str]) -> Any:
    if not remap:
        return value
    if isinstance(value, dict):
        return {key: _remap_ids(item, remap) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap_ids(item, remap) for item in value]
    if isinstance(value, str):
        return remap.get(value, value)
    return value


def render(store: ClaimStore, proposal: Proposal) -> str:
    """Show a proposal for confirmation, with ids resolved to names.

    Rendering the names is the whole point: it is where you see which Ana the
    model picked, before anything is written.
    """
    lines = [f'note: "{proposal.text}"', ""]

    for index, proposed in enumerate(proposal.actions, start=1):
        marker = " " if proposed.valid else "!"
        lines.append(f"{marker}{index}. {proposed.name}")

        for name in sorted(proposed.params):
            value = proposed.params[name]
            lines.append(f"      {name}: {_label(store, value)}")

        if proposed.reason:
            lines.append(f"      why: {proposed.reason}")

        for error in proposed.errors:
            lines.append(f"      REJECTED: {error}")

    return "\n".join(lines)


def _label(store: ClaimStore, value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_label(store, item) for item in value)
    if isinstance(value, str) and ":" in value and store.type_of(value) is not None:
        return f"{value} ({query.title(store, value)})"
    return str(value)


def _parse_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {"actions": []}

    if isinstance(parsed, list):
        return {"actions": parsed}
    return parsed


# ---------------------------------------------------------------------------
# a real model, when you want one
# ---------------------------------------------------------------------------


def anthropic_completer(model: str = "claude-sonnet-5", max_tokens: int = 8000) -> CompleteFn:
    """Wire in the Anthropic API. Requires ANTHROPIC_API_KEY and the sdk.

    Imported lazily so the rest of the project has no dependency on it. The
    client is built once, not per call, so repeated proposals reuse the
    connection.
    """
    import anthropic

    client = anthropic.Anthropic()

    def complete(system_prompt: str, user_prompt: str) -> str:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Exposed for callers that want to report cost, without changing the
        # return type everyone else relies on.
        complete.last_usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        pieces = []
        for block in response.content:
            if block.type == "text":
                pieces.append(block.text)
        return "\n".join(pieces)

    complete.last_usage = None
    return complete
