"""Parser tests: one small fixture per evidence format, asserting the rules that
matter downstream (identifiers normalised, absence detected, time taken from
the record, links and locations kept)."""

from __future__ import annotations

import json
from pathlib import Path

from ghost.data.loaders import EvidenceFile
from ghost.data.parsers import json_records, logs, sql, text

SQL_INVENTORY = """
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, full_name TEXT,
  email TEXT, phone TEXT, email_hash TEXT, account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE connector_subscriptions (resource_id TEXT PRIMARY KEY, customer_reference TEXT,
  customer_email TEXT, connector_name TEXT NOT NULL, enabled INTEGER NOT NULL,
  last_delivery_status TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO snapshot_scope VALUES ('account_service', '2026-09-16T08:00:00Z');
INSERT INTO customer_profiles VALUES ('account_1', NULL, 'Ada Lovelace', 'ADA@EXAMPLE.COM', '+44 20 7946 0958',
  NULL, 'DELETION_PENDING', '2026-09-16T07:00:00Z');
INSERT INTO connector_subscriptions VALUES ('data_flow_1', 'user_7', NULL, 'events', 1, 'delivered',
  '2026-09-16T07:00:00Z');
"""

SQL_REPLICA = """
-- Captured at: 2026-09-21T10:00:00Z
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL,
  collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_data_read_replica (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL,
  collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "customer_data_read_replica" VALUES('search_9','search_service','customer_search_documents',
  '{"resource_id":"search_9","index_name":"v3","document":{"display_name":"Ada","email":"ada@example.com","customer_reference":"user_7"}}',
  '2026-09-21T10:00:00Z');
CREATE TABLE search_documents_primary (resource_id TEXT PRIMARY KEY, display_name TEXT, email TEXT,
  source_resource_id TEXT, indexed_at TEXT);
INSERT INTO "search_documents_primary" VALUES('search_1','Ada','ada@example.com','account_1','2026-09-09T22:12:00Z');
"""

LOG_LINES = """\
2026-09-15T08:51:45Z level=DEBUG service=search_service trace_id=tr-1 event=index_query_started match_key="ada@example.com" attempt=1
2026-09-15T08:51:57Z level=INFO service=search_service trace_id=tr-1 resource_id=search_1 event=index_query_completed index=v3 result=hit
2026-09-15T11:58:11Z level=WARN service=integration_service trace_id=tr-2 resource_id=data_flow_1 event=connector_heartbeat enabled=true
2026-09-16T05:58:05Z {"severity":"INFO","logger":"account_service","service":"account_service","trace_id":"tr-3","resource_id":"account_1","event":"profile_lookup_completed","rows":"0","http_status":"404"}
2026-08-22T10:51:11Z <15>1 node-1 account_service tr-4 - - level=DEBUG event="profile_lookup_started" lookup_key="+44 20 7946 0958"
2026-08-22T10:51:17Z <15>1 node-1 account_service tr-4 - - level=DEBUG resource_id="account_4" event="profile_lookup_completed" rows="1" http_status="200"
2026-02-22T10:58:51Z [DEBUG] [account_service] [tr-5] profile_lookup_completed: resource_id="account_2" rows="1" http_status="200" correlation_key="user_8"
2026-02-03T05:51:30Z | DEBUG | security_service | tr-6 | audit_record_read_completed | resource_id="audit_record_1" subject_reference="user_7" rows="1"
2026-06-08T06:52:09Z requestId=tr-7 level=INFO function=search_service message="resource_id=\\"search_2\\" event=\\"index_query_completed\\" result=\\"miss\\" correlation_key=\\"ada@example.com\\""
2026-03-15T07:00:00Z INFO service=downstream_delivery_service event=record_lookup seq=1 system="account_service" resource_id="account_3" record_type="USER_PROFILE" result="NOT_FOUND" subject_reference="ada@example.com"
2026-09-15T09:29:19Z level=WARN service=queue_consumer event=consumer_lag topic=x lag=33
"""

TEXT_LINES = """CUSTOMER PLATFORM - SHIFT HANDOVER
- 2026-10-01T12:00:00Z | account_service | account_1 | USER_PROFILE | NOT_FOUND | ada@example.com
- 2026-10-01T12:00:00Z | analytics_platform | analytics_1 | ANALYTICS_PROFILE | ANONYMIZED | no direct identifier
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_sql_inventory_normalises_identifiers_and_scope_time(
    tmp_path: Path, entry: EvidenceFile
) -> None:
    obs = {o.resource_id: o for o in sql.parse(entry, _write(tmp_path, "inv.sql", SQL_INVENTORY))}
    account = obs["account_1"]
    assert account.system == "account_service"
    assert account.identifiers.emails == {"ada@example.com"}  # lowercased
    assert account.identifiers.phones == {"+442079460958"}  # digits only
    assert account.captured_at.isoformat() == "2026-09-16T08:00:00+00:00"  # from snapshot_scope
    assert account.state["account_status"] == "DELETION_PENDING"
    flow = obs["data_flow_1"]
    assert flow.system == "integration_service"
    assert flow.identifiers.user_ids == {"user_7"}
    assert flow.state["enabled"] == 1


def test_sql_replica_keeps_table_location_and_source_links(
    tmp_path: Path, entry: EvidenceFile
) -> None:
    observations = sql.parse(entry, _write(tmp_path, "rep.sql", SQL_REPLICA))
    by_loc = {(o.resource_id, o.location.split("/")[0]): o for o in observations}
    replica = by_loc[("search_9", "customer_data_read_replica")]
    assert replica.system == "search_service"
    assert replica.identifiers.emails == {"ada@example.com"}
    assert replica.identifiers.user_ids == {"user_7"}
    assert replica.captured_at.isoformat() == "2026-09-21T10:00:00+00:00"  # header comment
    primary = by_loc[("search_1", "search_documents_primary")]
    assert primary.related_resources == ["account_1"]


def test_json_bundle_readback_lookup_and_query(tmp_path: Path, entry: EvidenceFile) -> None:
    bundle = {
        "exported_at": "2026-09-15T12:00:00Z",
        "snapshots": [
            {
                "captured_at": "2026-09-15T11:00:00Z",
                "data_store": "identity_service",
                "collection": "deleted_customer_references",
                "records": [
                    {
                        "resource_id": "tombstone_1",
                        "subject_reference": "user_7",
                        "name": "Ada",
                        "email": "ada@example.com",
                        "phone": None,
                        "email_hash": None,
                        "account_status": "DELETED",
                    }
                ],
            },
            {
                "captured_at": "2026-09-15T11:00:00Z",
                "data_store": "backup_restore_service",
                "collection": "operational_record_history",
                "records": [
                    {
                        "recorded_at": "2026-02-12T09:00:00Z",
                        "system": "account_service",
                        "resource_id": "account_1",
                        "record_type": "USER_PROFILE",
                        "result": "NOT_FOUND",
                        "lookup_value": "ada@example.com",
                    }
                ],
            },
        ],
    }
    observations = json_records.parse(entry, _write(tmp_path, "b.json", json.dumps(bundle)))
    tomb = next(o for o in observations if o.resource_id == "tombstone_1")
    assert tomb.system == "identity_service"
    assert tomb.identifiers.user_ids == {"user_7"} and tomb.identifiers.emails == {
        "ada@example.com"
    }
    readback = next(o for o in observations if o.resource_id == "account_1")
    assert readback.present is False
    assert readback.captured_at.isoformat() == "2026-02-12T09:00:00+00:00"  # recorded_at wins
    assert readback.identifiers.emails == {"ada@example.com"}

    export = {
        "exported_at": "2026-09-15T11:44:55Z",
        "service_exports": {
            "account_service": {
                "profile_lookups": [
                    {
                        "observed_at": "2026-09-15T09:31:40Z",
                        "profile": None,
                        "resource_id": "account_2",
                        "status_code": 404,
                    }
                ]
            },
            "search_service": {
                "index_queries": [
                    {
                        "observed_at": "2026-09-15T09:14:18Z",
                        "query": "ada@example.com",
                        "resource_id": "search_3",
                        "hits": {
                            "documents": [
                                {
                                    "resource_id": "search_3",
                                    "email": "ada@example.com",
                                    "customer_reference": None,
                                }
                            ],
                            "total": 1,
                        },
                    }
                ]
            },
        },
    }
    observations = json_records.parse(entry, _write(tmp_path, "e.json", json.dumps(export)))
    lookup = next(o for o in observations if o.resource_id == "account_2")
    assert lookup.present is False and lookup.identifiers.is_empty()
    hit = next(o for o in observations if o.resource_id == "search_3")
    assert hit.present is True and hit.identifiers.emails == {"ada@example.com"}


def test_logs_all_syntaxes_join_traces_and_detect_absence(
    tmp_path: Path, entry: EvidenceFile
) -> None:
    observations = logs.parse(entry, _write(tmp_path, "a.log", LOG_LINES))
    by_id = {(o.resource_id, o.state.get("event")): o for o in observations}
    hit = by_id[("search_1", "index_query_completed")]
    assert hit.present and hit.identifiers.emails == {
        "ada@example.com"
    }  # key from the started line
    heartbeat = by_id[("data_flow_1", "connector_heartbeat")]
    assert heartbeat.state["enabled"] == "true"
    absent = by_id[("account_1", "profile_lookup_completed")]
    assert absent.present is False  # JSON syntax, http 404
    syslog = by_id[("account_4", "profile_lookup_completed")]
    assert syslog.present and syslog.identifiers.phones == {
        "+442079460958"
    }  # syslog, phone lookup key
    assert by_id[("account_2", "profile_lookup_completed")].identifiers.user_ids == {
        "user_8"
    }  # bracket
    assert by_id[("audit_record_1", "audit_record_read_completed")].identifiers.user_ids == {
        "user_7"
    }  # pipe
    miss = by_id[("search_2", "index_query_completed")]
    assert miss.present is False and miss.identifiers.emails == {
        "ada@example.com"
    }  # requestId syntax
    readback = next(o for o in observations if o.resource_id == "account_3")
    assert readback.present is False and readback.system == "account_service"
    assert not any(o.resource_id == "x" for o in observations)  # noise line dropped


def test_text_handover(tmp_path: Path, entry: EvidenceFile) -> None:
    observations = text.parse(entry, _write(tmp_path, "h.txt", TEXT_LINES))
    assert len(observations) == 2
    account, analytics = observations
    assert account.present is False and account.identifiers.emails == {"ada@example.com"}
    assert account.captured_at.isoformat() == "2026-10-01T12:00:00+00:00"
    assert analytics.present is True and analytics.identifiers.is_empty()
    assert analytics.state["result"] == "ANONYMIZED"
