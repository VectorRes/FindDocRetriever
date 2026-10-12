"""Bilingual, document-diverse retrieval for QA (app/qa/query_expansion.py)."""
import pytest

import app.qa.query_expansion as expansion
from app.config import get_settings
from app.retrieval.service import RetrievedChunk


def _chunk(document: str, n: int) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=f"{document}-{n}", document_id=document, document_filename=f"{document}.pdf",
                          sheet_name=None, cell_range=None, page_number=1, text=f"{document} chunk {n}")


class _Reply:
    def __init__(self, content):
        self.content = content


class _Translator:
    def __init__(self, reply="What was cash?", fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    def invoke(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise ConnectionError("down")
        return _Reply(self.reply)


def test_spanish_question_is_translated_to_english(monkeypatch):
    model = _Translator("What was cash as of December 31, 2025?")
    monkeypatch.setattr(expansion, "get_chat_model", lambda: model)

    assert expansion.translate("¿Cuál era el efectivo al 31 de diciembre de 2025?") == "What was cash as of December 31, 2025?"
    assert "into English" in model.calls[0][0]["content"]


def test_english_question_is_translated_to_spanish(monkeypatch):
    model = _Translator("¿Cuál fue la utilidad neta?")
    monkeypatch.setattr(expansion, "get_chat_model", lambda: model)

    expansion.translate("What was net income?")
    assert "into Spanish" in model.calls[0][0]["content"]


def test_failed_or_identical_translation_is_ignored(monkeypatch):
    monkeypatch.setattr(expansion, "get_chat_model", lambda: _Translator(fail=True))
    assert expansion.translate("What was revenue?") is None
    monkeypatch.setattr(expansion, "get_chat_model", lambda: _Translator("What was revenue?"))
    assert expansion.translate("What was revenue?") is None


def test_interleave_alternates_and_drops_repeats():
    a = [_chunk("A", 1), _chunk("A", 2)]
    b = [_chunk("B", 1), _chunk("A", 1)]
    assert [c.chunk_id for c in expansion.interleave(a, b)] == ["A-1", "B-1", "A-2"]


def test_one_document_cannot_take_every_slot_while_others_have_sources():
    ranked = [_chunk("10Q", n) for n in range(10)] + [_chunk("Andina", 1), _chunk("Pacifico", 1)]

    chosen = expansion.diversify(ranked, top_k=8)

    documents = [c.document_id for c in chosen]
    assert len(chosen) == 8
    # Capped at ceil(8 * 0.6) = 5 while Andina and Pacífico take a slot each;
    # the one slot left over goes back to the 10-Q's next best chunk.
    assert documents.count("10Q") == 6
    assert "Andina" in documents and "Pacifico" in documents
    assert documents.index("Andina") < 7 and documents.index("Pacifico") < 7


def test_slots_no_other_document_can_use_go_back_to_the_best_chunks():
    ranked = [_chunk("10Q", n) for n in range(10)] + [_chunk("Andina", 1)]

    chosen = expansion.diversify(ranked, top_k=8)

    assert [c.document_id for c in chosen].count("10Q") == 7
    assert len(chosen) == 8


def test_question_and_translation_are_both_searched(monkeypatch):
    monkeypatch.setenv("QUERY_TRANSLATION", "true")
    get_settings.cache_clear()
    searched = []

    def fake_retrieve(db, question, top_k, user_roles, user_id, document_id):
        searched.append((question, top_k))
        return [_chunk("EN" if question.startswith("What") else "ES", 1)]

    monkeypatch.setattr(expansion, "retrieve", fake_retrieve)
    monkeypatch.setattr(expansion, "translate", lambda q: "What was cash?")
    try:
        chunks = expansion.retrieve_for_question(None, "¿Cuál era el efectivo?", top_k=4)
    finally:
        monkeypatch.setenv("QUERY_TRANSLATION", "false")
        get_settings.cache_clear()

    assert [q for q, _ in searched] == ["¿Cuál era el efectivo?", "What was cash?"]
    assert all(k == 8 for _, k in searched)  # a wider pool to diversify from
    assert {c.document_id for c in chunks} == {"ES", "EN"}


@pytest.mark.parametrize("scoped", [True, False])
def test_translation_can_be_turned_off(monkeypatch, scoped):
    searched = []
    monkeypatch.setattr(expansion, "retrieve", lambda db, question, **kw: searched.append(question) or [])
    monkeypatch.setattr(expansion, "translate", lambda q: pytest.fail("translation is off in tests"))

    expansion.retrieve_for_question(None, "What was revenue?", document_id="d" if scoped else None)

    assert searched == ["What was revenue?"]
