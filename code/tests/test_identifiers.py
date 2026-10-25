"""Identifier extraction from free text (PDF lines, message bodies)."""

from ghost.data.parsers.common import identifiers_from


def test_free_text_user_id_hash_phone_and_email() -> None:
    text = (
        "Detail subject reference user_03. Contact kang.junseo@gmail.com. "
        "Hash 1fa7764c96e40e8570cbb7569f707f6d84c593ce1fa7764c96e40e8570cbb756 phone +12025550103."
    )
    found = identifiers_from({"text": text})
    assert found.user_ids == {"user_03"}
    assert found.emails == {"kang.junseo@gmail.com"}
    assert found.email_hashes == {
        "1fa7764c96e40e8570cbb7569f707f6d84c593ce1fa7764c96e40e8570cbb756"
    }
    assert found.phones == {"+12025550103"}


def test_similar_but_different_identifiers_stay_distinct() -> None:
    found = identifiers_from({"a": "g.silva76@gmail.com", "b": "g.silva76.jr@gmail.com"})
    assert found.emails == {"g.silva76@gmail.com", "g.silva76.jr@gmail.com"}
