"""Turn a plain-text note into claims: propose, show, confirm, apply.

    python3 scripts/note.py "Coffee with Adrian, he says ..."          # sample store
    python3 scripts/note.py --claims data/claims.jsonl "note"          # real store
    python3 scripts/note.py --claims data/claims.jsonl --yes "note"    # skip the prompt
    python3 scripts/note.py --model claude-opus-5 "note"

This is the one script that writes what a model proposed. It writes only
after you answer y, and only if every proposed action validated. One API
call per note; usage is printed so you can see the cost.
"""

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import ClaimStore, ingest, load_ontology
from crm.actions import ActionError

ONTOLOGY = "ontology/ontology.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("note")
    parser.add_argument("--claims", default="data/sample_claims.jsonl")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--yes", action="store_true", help="apply without asking")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.")
        return 1
    try:
        import anthropic
    except ImportError:
        print("The anthropic package is not installed. Run: pip install anthropic")
        return 1

    store = ClaimStore(load_ontology(ONTOLOGY), args.claims)
    if store.claim_count() == 0:
        print(f"{args.claims} is empty; seed it first.")
        return 1

    complete = ingest.anthropic_completer(model=args.model)
    try:
        proposal = ingest.propose(store, args.note, complete, today=date.today().isoformat())
    except anthropic.APIError as error:
        print(f"Anthropic API error: {error}")
        return 1

    print(ingest.render(store, proposal))
    usage = complete.last_usage or {}
    print(f"\nusage: {usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out")

    if not proposal.actions:
        print("Nothing proposed; nothing written.")
        return 0
    if not proposal.valid:
        print("A proposed action was rejected; nothing written.")
        return 1

    if not args.yes:
        answer = input(f"\nWrite {len(proposal.actions)} action(s) to {args.claims}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Not written.")
            return 0

    try:
        results = ingest.apply(store, proposal)
    except ActionError as error:
        print(f"Stopped: {error}")
        return 1

    for result in results:
        verb = "created" if result.created else "updated"
        print(f"  {verb:<8} {result.object_id}  ({result.action})")
    print(f"{len(results)} claim(s) written to {args.claims}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
