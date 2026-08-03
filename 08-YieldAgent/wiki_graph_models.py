"""Typed entity and relation candidates emitted by Wiki concept synthesis."""
from __future__ import annotations

from enum import Enum
import unicodedata

from pydantic import BaseModel, Field, field_validator


class RelationPredicate(str, Enum):
    causes = "causes"
    contributes_to = "contributes_to"
    resolved_by = "resolved_by"
    prevents = "prevents"
    associated_with = "associated_with"


def normalize_entity_name(value: str) -> str:
    """Return an NFKC-normalized, single-space entity identity."""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _require_non_empty(value: str) -> str:
    normalized = normalize_entity_name(value)
    if not normalized:
        raise ValueError("must not be empty")
    return normalized


class EntityCandidate(BaseModel):
    canonical_name: str
    entity_type: str

    @field_validator("canonical_name", "entity_type")
    @classmethod
    def normalize_required_fields(cls, value: str) -> str:
        return _require_non_empty(value)


class RelationCandidate(BaseModel):
    subject: str
    predicate: RelationPredicate
    object: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_doc_ids: list[str] = Field(default_factory=list)

    @field_validator("subject", "object")
    @classmethod
    def normalize_required_fields(cls, value: str) -> str:
        return _require_non_empty(value)

    @field_validator("source_doc_ids")
    @classmethod
    def normalize_and_deduplicate_source_doc_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(_require_non_empty(value) for value in values))


class GraphRelation(BaseModel):
    relation_id: str
    origin_concept_id: str
    subject: str
    predicate: RelationPredicate
    object: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_doc_ids: list[str] = Field(default_factory=list)


class GraphContext(BaseModel):
    primary_concept_id: str | None = None
    concept_ids: list[str] = Field(default_factory=list)
    relations: list[GraphRelation] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)
