from app.retrieval.access import can_access, normalize_tag, required_permission


def test_untagged_content_normalizes_to_most_restrictive_tier():
    assert normalize_tag(None) == "restricted"
    assert normalize_tag("") == "restricted"


def test_restricted_category_requires_matching_permission():
    tag = "restricted:compensation"
    assert required_permission(tag) == "compensation-access"
    assert can_access(tag, {"analyst"}) is False
    assert can_access(tag, {"compensation-access"}) is True
    assert can_access(tag, {"admin"}) is True


def test_public_and_internal_access():
    assert can_access("public", {"analyst"}) is True
    assert can_access("internal", {"analyst"}) is True
    assert can_access("internal", {"guest"}) is False


def test_generic_restricted_tier_requires_restricted_access():
    assert can_access("restricted", {"analyst"}) is False
    assert can_access("restricted", {"restricted-access"}) is True



def test_role_permission_mapping_supports_controller_and_partial_clearance():
    from app.retrieval.access import permissions_for_roles

    permissions = permissions_for_roles({"financial-controller"})
    assert "compensation-access" in permissions
    assert "legal-access" in permissions
    assert can_access("restricted:compensation", {"financial-controller"}) is True
    assert can_access("restricted:legal-contingencies", {"financial-controller"}) is True

    assert can_access("restricted:compensation", {"compensation-access"}) is True
    assert can_access("restricted:legal-contingencies", {"compensation-access"}) is False


def test_partial_clearance_keeps_tiers_separate():
    roles = {"compensation-access"}
    assert can_access("restricted:compensation", roles) is True
    assert can_access("restricted:legal-contingencies", roles) is False
    assert can_access("restricted:related-parties", roles) is False


def test_authorized_retrieval_is_audited(db_session):
    from app.db.models import Chunk, Document, RetrievalAuditLog
    from app.embeddings.factory import get_embedding_provider
    from app.retrieval.service import retrieve

    document = Document(
        filename="restricted-compensation.pdf", doc_type="pdf", status="ready",
        warnings=[], confidentiality_tag="restricted:compensation",
    )
    db_session.add(document)
    db_session.flush()
    provider = get_embedding_provider()
    embedding = provider.embed(["executive compensation was 50000"])[0]
    chunk = Chunk(
        document_id=document.id, text="executive compensation was 50000",
        embedding=embedding, confidentiality_tag="restricted:compensation",
    )
    db_session.add(chunk)
    db_session.commit()

    try:
        results = retrieve(
            db_session, "What was executive compensation?", top_k=5,
            user_roles="compensation-access", user_id="controller-42",
        )
        assert results
        db_session.commit()
        event = db_session.query(RetrievalAuditLog).filter_by(
            user_id="controller-42", chunk_id=chunk.id
        ).one()
        assert event.authorized is True
        assert event.confidentiality_tag == "restricted:compensation"
        assert event.action == "retrieve"
    finally:
        db_session.delete(document)
        db_session.commit()
