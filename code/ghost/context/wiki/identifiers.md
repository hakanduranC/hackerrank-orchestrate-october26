# Identifiers and identity resolution

A subject is identified by four kinds of values, all compared **exactly** after
normalisation:

| Kind | Normalisation | Notes |
|---|---|---|
| email | lower-case, trailing sentence punctuation stripped (`a@x.com.` → `a@x.com`) | `G.Silva76@Gmail.com` equals `g.silva76@gmail.com`; `g.silva76.jr@gmail.com` is **another person** |
| phone | digits and leading `+` only (`+81 90 7610 2507` → `+819076102507`) | spacing and dashes differ between systems |
| email hash | 64 hex characters, lower-case | a truncated hash shown on a screen (`8f0c…31ad`) is not an identifier |
| user_id | `user_<n>` verbatim | the stable internal reference; it is the one value an account tombstone may keep |

## Current and historical identifiers

`user_identities.csv` is the current snapshot. `user_audit_log.csv` records every change of
email, phone or name with the old and new value. A request may arrive under a **historical**
email; the subject is then the person whose audit history contains that value, and all of
their past and present identifiers are in scope. 100 of the 518 requests use a historical
email. Older evidence (a backup from spring, a reporting replica from February) naturally
holds the older identifiers.

## Where identifiers hide

- Field values (`email`, `phone`, `email_hash`, `customer_reference`, `subject_reference`,
  `lookup_key`, `requester_email`, `matched_value`) in SQL rows, JSON records and log lines.
- Free text: PDF lines ("Detail subject reference user_03"), message bodies ("contact
  kang.junseo@gmail.com."), screenshot labels. Free text is scanned for emails, `user_<n>`,
  64-hex hashes and `+digits` phones.
- Names are **not** identifiers. Two different people can share a name; a name alone never
  links a record.

## Membership vs. state

Whether a record is the subject's (membership) can be learned from any file, even one captured
after `audit_at`. What state the record is in (present, absent, anonymized…) is read only from
files captured at or before `audit_at`. Keep the two questions apart.
