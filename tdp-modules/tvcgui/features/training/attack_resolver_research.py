"""Compatibility shim for the retired native resolver hook module.

The previous research build patched game code. This module now re-exports the
read-only snapshot correlator so stale imports cannot arm native hooks.
"""
from tvcgui.features.training.attack_resolver_readonly import (
    CONTACT_CSV_FIELDS,
    SOURCE_CSV_FIELDS,
    ReadOnlyAttackResearch,
    get_attack_resolver_research,
    infer_property_b_routes,
    shutdown_attack_resolver_research,
)

AttackResolverResearch = ReadOnlyAttackResearch

__all__ = [
    "AttackResolverResearch",
    "ReadOnlyAttackResearch",
    "get_attack_resolver_research",
    "shutdown_attack_resolver_research",
    "infer_property_b_routes",
    "CONTACT_CSV_FIELDS",
    "SOURCE_CSV_FIELDS",
]
