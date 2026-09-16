"""Ontology-driven personal CRM.

The type system lives in ontology/ontology.yaml, not in this package. Nothing
here names a domain type; every rule is read from that file at startup.
"""

from . import actions
from .loader import load_ontology
from .registry import Registry
from .store import ClaimStore

DEFAULT_ONTOLOGY = "ontology/ontology.yaml"
DEFAULT_CLAIMS = "data/claims.jsonl"

__all__ = ["actions", "load_ontology", "Registry", "ClaimStore", "DEFAULT_ONTOLOGY", "DEFAULT_CLAIMS"]
