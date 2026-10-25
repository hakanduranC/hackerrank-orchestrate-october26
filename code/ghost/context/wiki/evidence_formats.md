# Evidence formats and how each one states a fact

94 files, all listed in `evidence_files.csv` with a `file_id`, path, origin and
`captured_at`. The manifest never says which person a file concerns; the contents do.

## SQL snapshot exports (`.sql`, SQLite-compatible)

Three layouts occur:

1. **Typed tables** — `customer_profiles`, `search_documents`, `support_records`,
   `backup_catalog` …, one column per field. `customer_profiles_primary` and
   `search_documents_primary` are **full** tables: a subject missing from them is proof of
   absence at the snapshot time.
2. **Replica dumps** — `customer_data_primary`, `customer_data_reporting_replica`,
   `customer_data_regional_replica`, `customer_data_failover_replica` with columns
   `resource_id, origin_system, collection_name, record_json, …`; the record itself is the
   JSON blob. Compare the same `resource_id` across replicas for drift.
3. **Inventory captures** — `customer_data_inventory_*` with mixed typed tables and a
   `snapshot_scope` table saying which systems the capture covers.

A header comment carries `Captured at:` and `Database topology:`; the manifest capture time is
the time cut, the header time is the record time.

## JSON

- **Bundles** — `snapshots[]` of `{data_store, collection, captured_at, records[]}`. An empty
  `records` list is still a snapshot: it proves the collection held nothing for the subjects
  the bundle is about.
- **Service exports** — `service_exports{system{collection[records]}}` with per-record
  `observed_at`.
- **Flat collections** — `{collection, records[]}`.
- **Readback records** — `{system, resource_id, record_type, result, subject_reference}` with
  `result` in FOUND, NOT_FOUND, IDENTIFIABLE, ANONYMIZED, REDACTED, ACTIVE, DISABLED, BLOCKED.
  A readback is a direct statement of state by the owning system.

## Logs (`.log`, six line syntaxes)

key=value, JSON lines, bracketed, pipe-separated, syslog-like and CEF-like. Each line
normalises to timestamp, service, trace id, event and fields. Operational traces are joined by
`trace_id`: the `*_started` line names the lookup key, the `*_completed` line names the
`resource_id` and the result. State fields: `http_status=404`, `rows=0`, `result=miss` mean
absent; `enabled`, `restore_allowed`, `archive_present`, `marker_found`,
`download_available`, `matched_value`; `restore_authorization_completed decision=deny`
means restore is BLOCKED. Heartbeats (`connector_heartbeat`) show a flow is alive. Noise
services (routers, collectors) carry no state.

## PDFs

Business documents: statements, support attachments, retention registers, compliance-journal
extracts. They state identifiers, resource references and retention dates
("retain until 25 Aug 2027"); they contain no verdict. A support-platform PDF **is** the
customer data (an attachment), not a report about it.

## Images (screenshots)

Read once by the vision model into typed JSON (title, resource ids, identifiers, fields, table
rows, line transcription), cached by content hash, then parsed like any other file. A screen
attests the record it displays in detail (title / side panel); table rows are context. Status
words seen: Healthy / Continuous sync (flow enabled), Retained reference (tombstone), Deleted,
Not found. Truncated hashes on screen are not identifiers.

## Text handovers (`.txt`)

Pipe-separated handover notes with readback-style result columns.
