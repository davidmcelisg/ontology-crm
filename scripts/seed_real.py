"""Seeds data/claims.jsonl from a hand-written YAML description of a real network.

    python scripts/seed_real.py                      # reads data/network.local.yaml
    python scripts/seed_real.py --rebuild            # wipe claims.jsonl first
    python scripts/seed_real.py --dry-run            # validate, write nothing

The YAML is people-first so it reads like a contact list, not like claims. The
script translates each entry into actions and runs them through
actions.execute(), the same validated write path the LLM uses. Names are
resolved to ids by exact match on a name or alias declared in the file; "me"
always means config.self_person_id.

data/network.local.yaml and data/claims.jsonl are gitignored. The committed
data/network.example.yaml shows the format with invented names.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import ClaimStore, load_ontology
from crm.actions import ActionError, execute

ONTOLOGY = "ontology/ontology.yaml"
CLAIMS = "data/claims.jsonl"
NETWORK = "data/network.local.yaml"


class SeedError(Exception):
    pass


class Seeder:
    def __init__(self, store: ClaimStore, dry_run: bool) -> None:
        self.store = store
        self.dry_run = dry_run
        self.self_id = store.registry.config["self_person_id"]
        self.people: dict[str, str] = {"me": self.self_id}
        self.orgs: dict[str, str] = {}
        self.interactions: dict[str, str] = {}
        self.count = 0

    # -- name resolution ----------------------------------------------------

    @staticmethod
    def _key(text: str) -> str:
        return " ".join(text.lower().split())

    def person(self, name: str, where: str) -> str:
        found = self.people.get(self._key(name))
        if found is None:
            raise SeedError(f"{where}: unknown person {name!r}; add them under people:")
        return found

    def org(self, name: str, where: str) -> str:
        found = self.orgs.get(self._key(name))
        if found is None:
            raise SeedError(f"{where}: unknown organization {name!r}; add it under orgs:")
        return found

    def interaction(self, subject: str, where: str) -> str:
        found = self.interactions.get(self._key(subject))
        if found is None:
            raise SeedError(f"{where}: no interaction with subject {subject!r}")
        return found

    # -- write path ---------------------------------------------------------

    def run(self, action: str, params: dict[str, Any], entry: dict[str, Any], where: str) -> str:
        params = {k: v for k, v in params.items() if v is not None}
        params.setdefault("source_kind", "self_observed")
        if entry.get("heard_from"):
            params["source_kind"] = "told_by_person"
            params["source_person"] = self.person(entry["heard_from"], where)
        if entry.get("source_note"):
            params["source_note"] = entry["source_note"]
        try:
            result = execute(self.store, action, params)
        except ActionError as error:
            raise SeedError(f"{where}: {error}") from error
        self.count += 1
        return result.object_id

    # -- sections -----------------------------------------------------------

    def seed(self, doc: dict[str, Any]) -> None:
        me = doc.get("me")
        if not me:
            raise SeedError("top-level `me:` (your own name) is required")
        # The self person is minted from the configured id, not from the name,
        # so functions that key on config.self_person_id keep working.
        self.store.assert_object("Person", self.self_id, {"display_name": me},
                                 source_kind="self_observed")
        self.count += 1
        self.people[self._key(me)] = self.self_id

        for entry in doc.get("orgs") or []:
            self._org(entry)
        for entry in doc.get("people") or []:
            self._person(entry)
        for entry in doc.get("people") or []:
            self._affiliations_and_relationship(entry)
        for entry in doc.get("interactions") or []:
            self._interaction(entry)
        for entry in doc.get("commitments") or []:
            self._commitment(entry)
        for entry in doc.get("pursuits") or []:
            self._pursuit(entry)
        for entry in doc.get("intros") or []:
            self._intro(entry)

    def _org(self, entry: dict[str, Any]) -> None:
        name = entry["name"]
        where = f"orgs/{name}"
        parent = self.org(entry["parent"], where) if entry.get("parent") else None
        object_id = self.run("CreateOrganization", {
            "name": name,
            "kind": entry.get("kind", "company"),
            "location": entry.get("location"),
            "parent": parent,
            "notes": entry.get("notes"),
        }, entry, where)
        self.orgs[self._key(name)] = object_id
        for alias in entry.get("aliases") or []:
            self.orgs[self._key(alias)] = object_id

    def _person(self, entry: dict[str, Any]) -> None:
        name = entry["name"]
        where = f"people/{name}"
        object_id = self.run("CreatePerson", {
            "display_name": name,
            "aliases": entry.get("aliases"),
            "based_in": entry.get("based_in"),
            "notes": entry.get("notes"),
        }, entry, where)
        self.people[self._key(name)] = object_id
        for alias in entry.get("aliases") or []:
            self.people[self._key(alias)] = object_id

    def _affiliations_and_relationship(self, entry: dict[str, Any]) -> None:
        name = entry["name"]
        where = f"people/{name}"
        person_id = self.person(name, where)

        affiliations = list(entry.get("affiliations") or [])
        if entry.get("at"):
            affiliations.insert(0, {
                "org": entry["at"], "role": entry.get("role"),
                "kind": entry.get("affiliation_kind"), "since": entry.get("since"),
                "until": entry.get("until"), "seniority": entry.get("seniority"),
            })
        for aff in affiliations:
            # An affiliation inherits the person's provenance unless it has its own.
            for key in ("heard_from", "source_note"):
                if key not in aff and key in entry:
                    aff[key] = entry[key]
            self.run("AssertAffiliation", {
                "person": person_id,
                "organization": self.org(aff["org"], where),
                "kind": aff.get("kind") or "employee",
                "role_title": aff.get("role"),
                "seniority": aff.get("seniority"),
                "start_date": aff.get("since"),
                "end_date": aff.get("until"),
            }, aff, where)

        if entry.get("relationship"):
            self.run("AssertRelationship", {
                "from_person": self.self_id,
                "to_person": person_id,
                "kind": entry["relationship"],
                "strength": entry.get("strength"),
                "origin_context": entry.get("how_we_met"),
            }, entry, where)

    def _interaction(self, entry: dict[str, Any]) -> None:
        subject = entry.get("subject")
        where = f"interactions/{subject or entry.get('when')}"
        others = entry.get("with") or []
        if isinstance(others, str):
            others = [others]
        participants = [self.self_id] + [self.person(n, where) for n in others]
        about = []
        for target in entry.get("about") or []:
            key = self._key(target)
            if key in self.orgs:
                about.append(self.orgs[key])
            else:
                raise SeedError(f"{where}: `about` must name an org declared under orgs: ({target!r})")
        object_id = self.run("RecordInteraction", {
            "occurred_at": entry["when"],
            "channel": entry.get("channel", "in_person"),
            "direction": entry.get("direction", "mutual"),
            "participants": participants,
            "subject": subject,
            "summary": entry.get("summary"),
            "expects_response": entry.get("expects_response"),
            "about": about or None,
        }, entry, where)
        if subject:
            self.interactions[self._key(subject)] = object_id

    def _commitment(self, entry: dict[str, Any]) -> None:
        where = f"commitments/{entry.get('what')}"
        created_in = (self.interaction(entry["from_interaction"], where)
                      if entry.get("from_interaction") else None)
        self.run("MakeCommitment", {
            "obligor": self.person(entry.get("who_owes", "me"), where),
            "obligee": self.person(entry.get("to", "me"), where),
            "description": entry["what"],
            "created_in": created_in,
            "due_date": entry.get("due"),
        }, entry, where)

    def _pursuit(self, entry: dict[str, Any]) -> None:
        where = f"pursuits/{entry.get('org')}/{entry.get('role')}"
        self.run("OpenPursuit", {
            "kind": entry.get("kind", "job_application"),
            "target_organization": self.org(entry["org"], where),
            "target_role": entry.get("role"),
            "stage": entry.get("stage", "applied"),
            "outcome": entry.get("outcome", "open"),
            "opened_at": entry["opened"],
            "closed_at": entry.get("closed"),
            "referred_by": self.person(entry["referred_by"], where) if entry.get("referred_by") else None,
        }, entry, where)

    def _intro(self, entry: dict[str, Any]) -> None:
        where = f"intros/{entry.get('by')}"
        self.run("RecordIntroduction", {
            "introducer": self.person(entry["by"], where),
            "introduced_a": self.person(entry.get("a", "me"), where),
            "introduced_b": self.person(entry["b"], where),
            "occurred_at": entry["date"],
            "context": entry.get("context"),
        }, entry, where)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("network", nargs="?", default=NETWORK)
    parser.add_argument("--claims", default=CLAIMS)
    parser.add_argument("--rebuild", action="store_true", help="delete the claims file before seeding")
    parser.add_argument("--dry-run", action="store_true", help="validate everything, write nothing")
    args = parser.parse_args()

    if not os.path.exists(args.network):
        print(f"{args.network} not found. Copy data/network.example.yaml to it and fill in your network.")
        return 1

    with open(args.network) as handle:
        doc = yaml.safe_load(handle) or {}

    if args.dry_run:
        claims_path = os.path.join(os.path.dirname(args.claims), ".seed_dry_run.jsonl")
        if os.path.exists(claims_path):
            os.remove(claims_path)
    else:
        claims_path = args.claims
        if os.path.exists(claims_path):
            if not args.rebuild:
                print(f"{claims_path} already exists. Pass --rebuild to replace it "
                      f"(this also drops anything ingested since).")
                return 1
            os.remove(claims_path)

    store = ClaimStore(load_ontology(ONTOLOGY), claims_path)
    seeder = Seeder(store, dry_run=args.dry_run)
    try:
        seeder.seed(doc)
    except (SeedError, KeyError) as error:
        print(f"error: {error}")
        if args.dry_run and os.path.exists(claims_path):
            os.remove(claims_path)
        return 1

    if args.dry_run:
        os.remove(claims_path)
        print(f"ok: {seeder.count} claims across {store.object_count()} objects would be written to {args.claims}")
    else:
        print(f"{store.claim_count()} claims across {store.object_count()} objects -> {claims_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
