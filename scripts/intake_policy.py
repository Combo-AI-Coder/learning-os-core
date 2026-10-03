"""Pure intake preference resolution; no storage, generation or write authority."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


INTAKE_DEPTHS = frozenset({"minimal", "balanced", "thorough"})


def validate_intake_preferences(preferences: Mapping[str, Any] | None) -> str | None:
    """Return an explicit depth, or None when the existing owner inherits."""
    if preferences is None:
        return None
    if not isinstance(preferences, Mapping):
        raise ValueError("preferences must be a mapping")
    if "intake_depth" not in preferences:
        return None
    depth = preferences["intake_depth"]
    if not isinstance(depth, str) or depth not in INTAKE_DEPTHS:
        raise ValueError("intake_depth must be minimal, balanced or thorough; remove the key to inherit")
    return depth


@dataclass(frozen=True)
class IntakePolicy:
    depth: str
    source: str
    start_immediately: bool = False

    @property
    def question_scope(self) -> str:
        if self.start_immediately:
            return "defer_intake"
        return {"minimal": "route_blocking", "balanced": "route_changing",
                "thorough": "relevant_background"}[self.depth]


def resolve_intake_policy(
    *, current_instruction: str | None = None,
    topic_preferences: Mapping[str, Any] | None = None,
    learner_preferences: Mapping[str, Any] | None = None,
    start_immediately: bool = False,
) -> IntakePolicy:
    """Resolve current > Topic > learner-global > Core's balanced default.

    The caller interprets the current learner instruction. This function neither
    infers a durable preference nor changes the supplied mappings. Lower-priority
    state is read lazily; a current correction need not depend on stale settings.
    Persistent documents are independently checked by the Instance validator.
    """
    if type(start_immediately) is not bool:
        raise ValueError("start_immediately must be a boolean")
    if current_instruction is not None:
        depth = validate_intake_preferences({"intake_depth": current_instruction})
        assert depth is not None  # the explicit intake_depth key was validated
        return IntakePolicy(depth, "current_instruction", start_immediately)
    for source, preferences in (("topic", topic_preferences),
                                ("learner_global", learner_preferences)):
        depth = validate_intake_preferences(preferences)
        if depth is not None:
            return IntakePolicy(depth, source, start_immediately)
    return IntakePolicy("balanced", "core_default", start_immediately)
