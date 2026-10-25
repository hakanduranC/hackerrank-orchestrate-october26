# Systems and record types

Northstar Shop keeps a person's data in these systems. Resource ids carry the system in
their prefix; the prefix also decides the policy category.

| Resource prefix | System (`system_id`) | Policy category | Where it shows up in evidence | What "deleted" looks like |
|---|---|---|---|---|
| `account_*` | `account_service` | USER_PROFILE | SQL `customer_profiles*` tables (primary, regional and failover replicas), JSON bundles `account_service/customer_profiles`, readback `record_lookup` lines, admin-console screenshots | missing from a full `customer_profiles_primary` / failover snapshot; readback `NOT_FOUND` / `http_status=404` |
| `search_*` | `search_service` | SEARCH_INDEX_ENTRY | SQL `search_documents_primary`, bundles `customer_search_documents`, index-inspector screenshots | missing from a full search snapshot; readback `NOT_FOUND`; `rows=0` on an index query |
| `support_*` | `support_platform` | SUPPORT_MESSAGE (REDACT when shared context) | `support_records` / `support_conversations` rows, ticket-console screenshots, support PDFs | record gone, or kept with the requester email / phone removed (REDACTED) |
| `object_*` | `object_storage` | SUPPORT_ATTACHMENT | `ticket_attachments`, object-storage browser screenshots, attachment PDFs | object absent from the bucket listing |
| `analytics_*` | `analytics_platform` | ANALYTICS_PROFILE | `customer_analytics_profiles`, analytics screenshots, readbacks `ANONYMIZED` / `IDENTIFIABLE` | record kept but every identifier null (ANONYMIZED); anonymized rows carry **no** identifier, so they link only through the activity window |
| `export_*` | `export_service` | USER_EXPORT | `customer_exports` / `customer_export_jobs`, export-console screenshots | export job and file absent |
| `data_flow_*` | `integration_service` | DATA_FLOW (DISABLE) | `connector_subscriptions` / `connector_configurations`, connector heartbeats in logs, connector-console screenshots | `enabled=false` / `DISABLED`; a *healthy, continuous* connector is ACTIVE and can recreate deleted data |
| `backup_*` | `backup_service` | BACKUP_ARCHIVE (RETAIN, restore must be BLOCKED) | `backup_catalog` / `backup_restore_entries`, `restore_authorization_*` log lines, recovery-manager screenshots | archive stays; `deletion_block=true`, `restore_allowed=false`, readback `BLOCKED`, or `decision=deny` on a restore authorization |
| `audit_record_*` | `security_service` | SECURITY_AUDIT_LOG (RETAIN) | `security_audit_records` / `security_audit_events`, compliance-journal PDFs, security-explorer screenshots; linked to the subject by `subject_reference` = `user_id` | must still be PRESENT; never deleted |
| `tombstone_*` | `identity_service` | ACCOUNT_TOMBSTONE (REDACT to the bare `user_id`) | `deleted_customer_references`, customer-reference-registry screenshots | the stable `user_id` remains; name, email, phone and hash are gone |
| `legal_hold_*` | `legal_service` | LEGAL_HOLD_RECORD (RETAIN) | `retained_order_records` | must still be PRESENT while the hold is active |
| `marketing_*` | `marketing_platform` | MARKETING_PROFILE | audience-operations screenshots, exports | record absent |
| `cache_*` | `cache_service` | CACHE_ENTRY | cache dumps / readbacks | entry absent |
| `email_delivery_*` | `email_delivery_service` | EMAIL_DELIVERY_LOG (REDACT) | delivery logs | log kept, email address removed |

A deletion request touches, on average, four to six of these. The deletion workflow
(`system_activity.csv`) records what it *attempted* per record (`DELETE_RECORD`,
`REDACT_RECORD`, `ANONYMIZE_RECORD`, `CHECK_DATA_FLOW`, `CHECK_RESTORE_ACCESS`) and what it
*claims* happened (`SUCCESS`, `PARTIAL`, `FAILED`). The attempted action is the policy
engine's category decision; the claim is not a state.

Decoys: the activity stream and the evidence files describe many other people. A record
belongs to the subject only through an exact identifier (email, phone, 64-hex email hash,
`user_id`), a reference from such a record (`source_resource_id`), or the deletion workflow's
own time window for records that carry no identifier after deletion.
