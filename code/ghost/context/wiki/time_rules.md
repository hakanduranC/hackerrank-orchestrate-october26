# Time rules

Every case has four timestamps: `requested_at` (the person asked), `reported_at` (the workflow
made its claim), `audit_at` (the moment the answer must describe), and each evidence file's
`captured_at` (manifest) plus the record-level timestamps inside it.

1. **Cut at `audit_at`, on the file.** A file captured after `audit_at` does not exist for the
   case: it cannot prove a state and cannot be cited, whatever timestamps its rows carry.
   Tools refuse such files. The answer key cites only files captured at or before `audit_at`
   (75 of 75 sample citations).
2. **Newest evidence wins.** For one record, the latest observation at or before `audit_at`
   decides the state; an absence proof (full table without the record) outranks an older
   presence.
3. **Evidence older than the request still counts.** A record seen in June and again in
   September existed for a July request too. A request-time cutoff was measured and rejected:
   it flipped one case wrongly and discarded four valid absence proofs.
4. **Claims are dated, states are observed.** `reported_status`, `SUCCESS` / `FAILED` rows and
   workflow messages are claims at `reported_at`; they never set a state. In the samples the
   reported status was right in 13 of 32 cases.
5. **Recreation is a sequence.** A record absent at one time and present again later, with a
   sync or restore event in between, is present **and** the flow / restore path is active.
6. **Membership has no time cut.** Which records belong to the subject may be learned from a
   later file (for example a retained audit record that first appears in a later journal); only
   state and citations are cut.
7. **Retention periods** on audit records ("retain until …") are read against `audit_at`: within
   the period the record is RETAIN; the samples contain no expired record.

The deletion workflow's own window — `requested_at` to `reported_at` (+15 min) — is how
identifier-less records (deleted accounts, anonymized analytics rows) are tied to a request:
one deletion batch per request, status-consistent rows, tightest window first. Two requests
handled in the same minutes share a batch; their records become candidates for both and are
assigned only when an identifier decides.
