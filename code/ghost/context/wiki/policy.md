# Deletion policy and how it is applied

`deletion_rules.csv` gives one required action per data category. The category of a record
comes from its id prefix (see systems.md), refined by the record type a readback or a
collection name states (an attachment inside a support ticket is SUPPORT_ATTACHMENT, not
SUPPORT_MESSAGE).

| Rule | Category | Condition | Action | How it is read here |
|---|---|---|---|---|
| rule_01 | USER_PROFILE | always | DELETE | the profile row must be gone from every copy (primary, replicas, failover) |
| rule_02 | SECONDARY_IDENTIFIER | always | DELETE | alternate emails / phones attached to the profile |
| rule_03 | SEARCH_INDEX_ENTRY | always | DELETE | the index document must be gone, not only hidden |
| rule_04 | CACHE_ENTRY | always | DELETE | |
| rule_05 | SUPPORT_MESSAGE | conversation holds shared customer / agent / order context | REDACT | keep the thread, remove the subject's identifiers |
| rule_06 | SUPPORT_ATTACHMENT | attachment belongs only to the subject | DELETE | |
| rule_07 | ANALYTICS_PROFILE | still attributable to a user | ANONYMIZE | keep statistics, null every identifier |
| rule_08 | PRODUCT_TELEMETRY | still attributable to a user | ANONYMIZE | |
| rule_09 | USER_EXPORT | always | DELETE | a "download your data" file is personal data too |
| rule_10 | MARKETING_PROFILE | always | DELETE | |
| rule_11 | EMAIL_DELIVERY_LOG | log contains a user email | REDACT | |
| rule_12 | SECURITY_AUDIT_LOG | within the retention period | RETAIN | must remain PRESENT; verifying it is part of a complete deletion |
| rule_13 | LEGAL_HOLD_RECORD | a legal hold is active | RETAIN | |
| rule_14 | BACKUP_ARCHIVE | restore access can be blocked | RETAIN + BLOCK restore | the archive stays; what must be true is that it cannot restore the person's data |
| rule_15 | ACCOUNT_TOMBSTONE | account deleted, only a stable `user_id` is needed | REDACT | keep `user_id`; name, email, phone, hash must be gone |

Recreation paths are not categories but treatments: an active DATA_FLOW is **DISABLE**d, a
restorable BACKUP_ARCHIVE is **BLOCK**ed. Both come before record changes in the remediation
order, because otherwise the sync or the restore recreates what was just deleted.

## Which treatment a record gets

1. The deletion workflow's attempted action for that record (`DELETE_RECORD`,
   `REDACT_RECORD`, `ANONYMIZE_RECORD`, `CHECK_DATA_FLOW`, `CHECK_RESTORE_ACCESS`) is the
   policy engine's own category decision and matched the expected treatment in 40 of 41
   sample steps. It is used first.
2. Otherwise the rule for the category.
3. Treatment is **not** a judgement call per case: the agent reports membership and state; the
   treatment of a known record is the policy's. A rule condition ("shared order context") is
   decided by the policy engine, not inferred from message text.

## Statuses that satisfy a treatment

| Treatment | Compliant state | Violation reported as |
|---|---|---|
| DELETE | ABSENT | `REPORT_REMAINING_RECORD` (PRESENT) |
| REDACT | REDACTED | `REPORT_EXPOSED_PII` when identifiers are still exposed |
| ANONYMIZE | ANONYMIZED | `REPORT_EXPOSED_PII` when still identifiable, `REPORT_REMAINING_RECORD` when the record simply exists with no identifier columns at all |
| RETAIN | PRESENT | nothing to remediate; verified with `VERIFY_RECORD_PRESENT` |
| DISABLE | DISABLED | `REPORT_ACTIVE_DATA_FLOW` |
| BLOCK | BLOCKED | `REPORT_RESTORE_RISK` |

A record that was deleted and then recreated by a sync or a restore yields two findings:
the remaining record **and** the active flow / restore risk.
