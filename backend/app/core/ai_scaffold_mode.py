"""Runtime normalization for stored experiment AI modes."""


def normalize_ai_scaffold_mode(value: object) -> str:
    """Recognize legacy multi-agent aliases; otherwise use generic AI."""
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"multi_agent", "multi", "multiagent", "multi_ai"}:
        return "multi_agent"
    return "single_agent"
