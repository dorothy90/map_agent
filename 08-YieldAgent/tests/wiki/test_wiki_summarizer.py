import pytest


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
