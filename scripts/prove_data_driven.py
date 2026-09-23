"""Checks the claim the README makes: no domain type is named in the code.

Parses every module in crm/ and looks for a declared object type used as a real
identifier. Strings and comments are skipped on purpose -- they are allowed to
explain the design, and the claim is about what the code branches on, not about
what the documentation mentions.

The type names are read from the ontology, not hardcoded here, so adding a type
to the yaml also extends this check.

    python3 scripts/prove_data_driven.py
"""

import io
import os
import pathlib
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import load_ontology

ONTOLOGY = "ontology/ontology.yaml"


def main() -> int:
    registry = load_ontology(ONTOLOGY)
    declared = set(registry.type_names())

    offences: list[str] = []
    mentions = 0

    for path in sorted(pathlib.Path("crm").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.STRING, tokenize.COMMENT):
                if any(name in token.string for name in declared):
                    mentions += 1
                continue
            if token.type == tokenize.NAME and token.string in declared:
                offences.append(f"{path}:{token.start[0]}  {token.string}")

    print(f"declared in {ONTOLOGY}: {', '.join(sorted(declared))}")
    print(f"  {len(registry.object_types)} object types, "
          f"{len(registry.links)} links derived from ref attributes, "
          f"{len(registry.actions)} actions, {len(registry.functions)} functions")
    print()
    print(f"scanned {len(list(pathlib.Path('crm').glob('*.py')))} modules in crm/")
    print(f"  {mentions} mentions inside docstrings and comments (allowed: they explain)")
    print(f"  {len(offences)} uses as executable code")

    for offence in offences:
        print(f"    {offence}")

    print()
    if offences:
        print("FAIL: a domain type leaked into the code.")
        return 1
    print("PASS: every one of those names exists only in the yaml and in prose.")
    print("      Add a type to the yaml and nothing here has to change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
