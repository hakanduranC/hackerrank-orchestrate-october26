"""Screenshot attestation: the detail record carries identifiers, list rows are context."""

from __future__ import annotations

from datetime import UTC, datetime

from ghost.data.observations import Identifiers
from ghost.data.parsers.image import _attest, _present, _state_of
from ghost.data.vision import ExtractedIdentifiers, ImageExtraction

EX = ImageExtraction(
    document_kind="backup console",
    title="backup_9287",
    resource_ids=["backup_9287", "backup_9291"],
    identifiers=ExtractedIdentifiers(emails=["areed1433@gmail.com", "bluepeakco@outlook.com"]),
    fields={"Account": "areed1433@gmail.com", "Status": "Healthy", "Tier": "Archive"},
    records=[
        {"Archive ID": "backup_9287", "Account": "areed1433@gmail.com", "Tier": "Archive"},
        {"Archive ID": "backup_9291", "Account": "bluepeakco@outlook.com", "Tier": "Archive"},
    ],
    lines=["backup_9287", "Account areed1433@gmail.com"],
)
HEADLINE = " ".join([EX.title, *EX.lines, *EX.fields.values()])
SCREEN_IDS = Identifiers(emails={"areed1433@gmail.com", "bluepeakco@outlook.com"})


def test_detail_record_inherits_row_and_panel_identifiers() -> None:
    ids, values = _attest("backup_9287", EX, HEADLINE, SCREEN_IDS, has_rows=True)
    assert ids.emails == {"areed1433@gmail.com"}
    assert values["Account"] == "areed1433@gmail.com" and values["Status"] == "Healthy"


def test_list_rows_are_context_without_identifiers() -> None:
    ids, values = _attest("backup_9291", EX, HEADLINE, SCREEN_IDS, has_rows=True)
    assert ids.is_empty() and values["Account"] == "bluepeakco@outlook.com"


def test_screen_without_table_is_about_the_records_it_names() -> None:
    ids, _ = _attest("backup_9287", EX, HEADLINE, SCREEN_IDS, has_rows=False)
    assert ids.emails == {"areed1433@gmail.com"}  # detail wins; screen ids only when nothing else
    ids2, _ = _attest("backup_0000", EX, "", SCREEN_IDS, has_rows=False)
    assert ids2.emails == SCREEN_IDS.emails


def test_status_words_map_to_flags_and_presence() -> None:
    state = _state_of({"Status": "Healthy", "Sync mode": "Continuous sync"})
    assert state["enabled"] is True
    assert _state_of({"Status": "Disabled"})["enabled"] is False
    assert _state_of({"Restore": "Restorable"}).get("restore_allowed") is True
    assert _present({"Status": "Active"}) and not _present({"Status": "Deleted"})
    assert datetime(2026, 1, 1, tzinfo=UTC)  # keeps the import meaningful for mypy
