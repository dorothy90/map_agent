import pytest

from wiki_graph_models import EntityCandidate, RelationCandidate
from wiki_summarizer import EpisodeRef


pytestmark = pytest.mark.no_server


def test_concept_synthesis_defaults_graph_fields_for_old_provider_output():
    from wiki_summarizer import ConceptSynthesis

    result = ConceptSynthesis(body_markdown="body", confidence=0.8, citations=[])

    assert result.entities == []
    assert result.relations == []


def test_synthesize_concept_from_docs_invokes_structured_model_once(monkeypatch):
    import wiki_summarizer

    calls = []
    expected = wiki_summarizer.ConceptSynthesis(
        body_markdown="body", confidence=0.8, citations=[]
    )

    class FakeChain:
        def invoke(self, messages, config):
            calls.append((messages, config))
            return expected

    class FakeModel:
        def with_structured_output(self, schema, method):
            assert schema is wiki_summarizer.ConceptSynthesis
            assert method == "function_calling"
            return FakeChain()

    monkeypatch.setattr(wiki_summarizer, "_model", lambda: FakeModel())
    monkeypatch.setattr(wiki_summarizer, "_lf_callbacks", lambda: [])

    result = wiki_summarizer.synthesize_concept_from_docs(
        "concept:4SS|PRE METAL CLN|EASY",
        [{"doc_id": "FH-1", "cause": "cause", "action": "action"}],
    )

    assert result is expected
    assert len(calls) == 1


def test_synthesize_from_docs_filters_forged_citations_and_rejects_entire_relation(
    monkeypatch, caplog,
):
    import wiki_summarizer

    expected = wiki_summarizer.ConceptSynthesis(
        body_markdown="body [FH-REAL]",
        confidence=0.8,
        citations=[
            EpisodeRef(
                episode_id="episode:forged",
                doc_id=" FH-REAL ",
                source_file="FORGED.pptx",
                date="2099-12-31",
                natural_label="FORGED LABEL",
                download_url="https://evil.example/FORGED.pptx",
            ),
            EpisodeRef(episode_id="", doc_id="FH-FORGED"),
            EpisodeRef(episode_id="", doc_id="FH-REAL"),
        ],
        entities=[
            EntityCandidate(canonical_name="Queue", entity_type="condition"),
            EntityCandidate(canonical_name="Oxide", entity_type="mechanism"),
        ],
        relations=[
            RelationCandidate(
                subject="Queue",
                predicate="causes",
                object="Oxide",
                confidence=0.8,
                source_doc_ids=["FH-REAL"],
            ),
            RelationCandidate(
                subject="Queue",
                predicate="contributes_to",
                object="Oxide",
                confidence=0.7,
                source_doc_ids=["FH-REAL", "FH-FORGED"],
            ),
            RelationCandidate(
                subject="Queue",
                predicate="associated_with",
                object="Oxide",
                confidence=0.6,
                source_doc_ids=["FH-FORGED"],
            ),
        ],
    )

    class FakeChain:
        def invoke(self, messages, config):
            return expected

    class FakeModel:
        def with_structured_output(self, schema, method):
            return FakeChain()

    monkeypatch.setattr(wiki_summarizer, "_model", lambda: FakeModel())
    monkeypatch.setattr(wiki_summarizer, "_lf_callbacks", lambda: [])
    caplog.set_level("WARNING", logger="yield_agent.wiki_summarizer")

    result = wiki_summarizer.synthesize_concept_from_docs(
        "concept:4SS|PRE METAL CLN|EASY",
        [
            {
                "doc_id": "FH-REAL",
                "cause": "cause",
                "action": "action",
                "source_file": "FH-REAL.pptx",
                "date": "2026-08-01",
            }
        ],
    )

    assert [citation.doc_id for citation in result.citations] == ["FH-REAL"]
    citation = result.citations[0]
    assert citation.episode_id == ""
    assert citation.source_file == "FH-REAL.pptx"
    assert citation.date == "2026-08-01"
    assert citation.natural_label == ""
    assert citation.download_url == ""
    assert [relation.predicate.value for relation in result.relations] == ["causes"]
    assert result.relations[0].source_doc_ids == ["FH-REAL"]
    assert "dropped citations=2 relations=2" in caplog.text


def test_source_restriction_drops_all_forged_provider_sources():
    import wiki_summarizer

    synthesis = wiki_summarizer.ConceptSynthesis(
        body_markdown="body",
        confidence=0.5,
        citations=[EpisodeRef(episode_id="", doc_id="FH-FORGED")],
        relations=[
            RelationCandidate(
                subject="Queue",
                predicate="causes",
                object="Oxide",
                confidence=0.5,
                source_doc_ids=["FH-FORGED"],
            )
        ],
    )

    restricted = wiki_summarizer.restrict_concept_synthesis_sources(
        synthesis, ["FH-REAL"]
    )

    assert restricted.citations == []
    assert restricted.relations == []


def test_episode_authority_maps_source_files_only_for_equal_length_lists():
    import wiki_summarizer

    ambiguous = wiki_summarizer.authoritative_citations_from_episodes(
        [
            {
                "id": "episode:ambiguous",
                "frontmatter": {
                    "doc_ids": ["FH-A", "FH-B"],
                    "source_files": ["B.pptx"],
                },
            }
        ]
    )
    aligned = wiki_summarizer.authoritative_citations_from_episodes(
        [
            {
                "id": "episode:aligned",
                "frontmatter": {
                    "doc_ids": ["FH-A", "FH-B"],
                    "source_files": ["A.pptx", "B.pptx"],
                },
            }
        ]
    )

    assert ambiguous["FH-A"]["source_file"] == ""
    assert ambiguous["FH-B"]["source_file"] == ""
    assert aligned["FH-A"]["source_file"] == "A.pptx"
    assert aligned["FH-B"]["source_file"] == "B.pptx"
