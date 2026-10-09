"""Source-faithful file and knowledge modeling, independent of domain rules."""

from .core import (
    SCHEMA_VERSION,
    KnowledgeFoundationError,
    coverage,
    impact,
    load_json,
    query,
    render_markdown,
    validate,
)

__all__ = ["SCHEMA_VERSION", "KnowledgeFoundationError", "coverage", "impact", "load_json", "query", "render_markdown", "validate"]
