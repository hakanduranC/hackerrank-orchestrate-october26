# Reading tool results

Every tool answers with a header that names the format and the count; the header is not
evidence.

## get_portfolio → resources[]

| Field | Values | What to do with it |
|---|---|---|
| `status_at_audit` | PRESENT, ABSENT, IDENTIFIABLE, ANONYMIZED, REDACTED, ACTIVE, DISABLED, RESTORABLE, BLOCKED, UNKNOWN | the newest observation at or before `audit_at`; UNKNOWN = no file shows it — search before asserting anything |
| `required_treatment` / `treatment_basis` | DELETE, REDACT, ANONYMIZE, RETAIN, DISABLE, BLOCK | the policy's decision; keep it |
| `evidence_id` | file id or empty | what proves the status; empty = nothing proves it yet |
| `link` | `identifier` (a file holds the subject's email / phone / hash / user_id in this record), `related` (referenced from such a record), `window` (the workflow touched it in this request's window; deleted records carry no identifier, so this is the only link), `window-alternative` (touched in the same window by a batch that served several requests — a candidate, not a link) | candidates need an identifier to become the subject's; without one they stay out |
| `ambiguous` / `alternatives` | bool / ids | the window pick could not be separated from these ids |
| `direct_pii` | field=value list | identifiers still exposed; the reason for IDENTIFIABLE |
| `recreated_after_deletion` | bool | absent, then present again: look for the sync / restore |
| `absence_proofs` | `evidence@date` | full snapshots lacking the record; cite one for ABSENT |
| `activity` | `activity_id time event result` | the workflow's claims; never a state |

`notes[]` lists what the plan could not link; each note is a gap to chase.

## get_resource_history → observations[]

Newest first. `present=false` is a proof of absence from that file. `state` holds the raw
fields (`result`, `enabled`, `restore_allowed`, `deletion_block`, `decision`, …);
`identifiers` lists what the record still holds. The newest observation decides the status.

## search_evidence / list_evidence / read_evidence / describe_evidence / sql_probe

Raw lines or rows with line numbers and the file's capture time. `no match` means the term is
absent from every file captured by `audit_at` — proof of absence only for a full snapshot, not
for a log. `describe_evidence` lists a dump's tables, columns and row counts so a `sql_probe`
query names real tables. A `refused:` answer means the file is after `audit_at` or the query
is not read-only; do not retry the same call.

## final_answer

One entry per record considered. `is_subject` yes / no / uncertain; `status` from the
vocabulary above; `treatment` the policy's; `evidence_id` a file id. The validator replies
with every claim it cannot anchor; fix exactly those and resubmit.
