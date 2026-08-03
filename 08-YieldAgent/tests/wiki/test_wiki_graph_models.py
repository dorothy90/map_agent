import pytest


pytestmark = pytest.mark.no_server


def test_relation_contract_normalizes_names_and_rejects_unknown_predicate():
    from pydantic import ValidationError
    from wiki_graph_models import RelationCandidate

    relation = RelationCandidate(
        subject="  Queue\u3000time 초과  ",
        predicate="causes",
        object="자연 산화",
        confidence=0.82,
        source_doc_ids=["FH-9003-EXTRA", "FH-9003-EXTRA"],
    )

    assert relation.subject == "Queue time 초과"
    assert relation.source_doc_ids == ["FH-9003-EXTRA"]

    with pytest.raises(ValidationError):
        RelationCandidate(
            subject="A",
            predicate="maybe_causes",
            object="B",
            confidence=0.5,
            source_doc_ids=["FH-1"],
        )


def test_entity_and_relation_contracts_reject_empty_values_and_invalid_confidence():
    from pydantic import ValidationError
    from wiki_graph_models import EntityCandidate, RelationCandidate

    with pytest.raises(ValidationError):
        EntityCandidate(canonical_name=" \u3000 ", entity_type="process_condition")
    with pytest.raises(ValidationError):
        RelationCandidate(
            subject="A",
            predicate="causes",
            object="B",
            confidence=1.01,
            source_doc_ids=["FH-1"],
        )
    with pytest.raises(ValidationError):
        RelationCandidate(
            subject="A",
            predicate="causes",
            object="B",
            confidence=0.5,
            source_doc_ids=["  "],
        )
