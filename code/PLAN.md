# Ghost Data Still Exists — Plan and Implementation Tracker

Design: **plan–execute–verify** with an escalation ladder. Tools compute, the agent decides,
guards check, a deterministic procedure is the fallback and the parity baseline.

## 1. Inputs and their roles

| Input | Role | Produces findings? |
|---|---|---|
| `deletion_requests.csv` | intent (from `request_text` only) + `audit_at` cutoff | no |
| `user_identities.csv` + `user_audit_log.csv` | subject identifier set: `user_id` + all current and historical emails, phones, hashes | no (`audit_id` citable) |
| `deletion_rules.csv` | category → required treatment | no |
| `system_activity.csv` | per-resource history, ordering (delete → sync/restore recreation), 12 catalog links, citable `activity_id` | rarely |
| `evidence/` | the truth: record state at `audit_at` | **yes** |

The CSVs are the auditor's reference inputs, never systems to delete from. Only systems
named in evidence (`account_service`, `search_service`, `support_platform`,
`analytics_platform`, `export_service`, `integration_service`, `backup_service`,
`security_service`, `identity_service`) can appear in output steps.

## 2. Stack

Python 3.12 via uv + lockfile · langchain-core 1.7.0 + langgraph 1.2.17 + langchain-anthropic 1.7.6 ·
pydantic 2 · stdlib `sqlite3/json/csv/re` · pdfplumber · pytest, ruff, mypy (strict) · python-dotenv.
Config: `ANTHROPIC_API_KEY`, `GHOST_MODEL` (default `claude-opus-5-5`), `GHOST_EFFORT`, limits, paths.
No OCR: screenshots are read once each by the vision model and cached.

## 3. Layout

```
code/
  main.py              CLI → output.csv  (default full run; --check --build-index --run --validate --extract-images --case)
  evaluation/main.py   metrics entry point
  ghost/
    config.py
    data/              loaders, Observation model, parsers/{sql,json,logs,text,pdf,images}, index (SQLite), vision cache
    identity.py        email → user_id + full identifier set (current + historical)
    policy.py          rules → category → treatment; condition evaluators
    portfolio.py       PLAN
    agent/             graph.py (LangGraph), tools.py, schemas.py, prompts.py, guards.py, baseline.py
    compose.py         verdicts → ordered steps
    evaluation/        metrics, custom cases, report generation
    tracing.py
tests/
```

Package is named `ghost` (from the challenge title) because a package literally named `code`
would shadow the standard-library `code` module; `code/main.py` stays as the thin entry point.

## 4. Data layer

Every format → one `Observation {evidence_id, captured_at, system, resource_id, category,
identifiers{user_ids, emails, phones, hashes}, state fields, excerpt}`. SQLite index:
`observations`, `identifiers` (normalized), `activity`, plus the SQL dumps loaded raw for the
escalation probe. Lookup is **exact** on any identifier including `user_id`; fuzzy never.
Images: one vision extraction per file into the same shape, cross-checked against known
emails, cached by file hash.

## 5. PLAN (deterministic, unit-tested)

`portfolio(case)` = identifier set → observations ≤ `audit_at`, latest per resource,
category + applicable rule, activity timeline per resource, recreation flag when a present
record follows a deletion. Also every record linked by `user_id` only (audit records,
tombstones, backups, connectors).

## 6. EXECUTE (agent seat)

One bounded tool-calling loop per case, cap `GHOST_JUDGE_TURN_LIMIT`, must end with
`final_answer`. The parsed portfolio is the starting point, not the ceiling: the agent can
search the raw evidence itself, so a parser gap does not bound it. Tools:

| Tool | Purpose |
|---|---|
| `get_portfolio` | the subject, every linked resource with status / treatment / evidence / timeline, candidates flagged |
| `get_resource_history` | every observation and activity row of one resource up to `audit_at` |
| `get_policy_rules` | `deletion_rules.csv` |
| `list_evidence(origin, before)` | which files exist for a system by the cutoff (manifest) |
| `search_evidence(term)` | exact keyword search (SQLite FTS5) over every raw line / record of every file |
| `read_evidence(file_id, around)` | raw excerpt with line numbers and timestamps |
| `final_answer` | the typed `CaseVerdict` |

The prompt tells the agent to compare the portfolio with the checklist of systems a subject
can have records in, and to chase gaps with the leads the index does not use: request-text
hints, resource ids from the activity timeline (window candidates), manifest origins and
capture times, identifier variants. Retrieval stays exact; embeddings are not used (decoys,
hash/phone keys, time cut). Frozen, cached system prompt: policy table, output contract, decoy and claim
rules. Typed outcome:

```
CaseVerdict{intent, reason,
  resources:[{system, resource_id, is_subject: yes|no|uncertain,
              state_at_audit, required_treatment, evidence_id, reason}]}
```

Agent decides: intent (multilingual), decoys, state when evidence and claims disagree,
policy conditions, missing checks. Rule: a record deleted then recreated by a flow →
remaining record **and** active flow.

## 7. VERIFY (guards)

evidence exists and `captured_at ≤ audit_at` · resource came from the portfolio or was shown
by a tool · the cited file mentions the record or proves its absence · a record the agent
found itself is tied to the subject by an identifier in that file · state evidence is a file
(activity / audit rows are claims) · treatment allowed for category · no DELETE on retained
categories · no duplicates · NOT_DELETION ⇒ `[]`. Covered by `tests/test_guards.py`; the
post-acceptance hardening (`agent/merge.py`) by `tests/test_merge.py`.
Refusal → observation → retry within `GHOST_RETRY_ALLOWANCE` → **baseline** completes the
case, trace records the fallback.
**Escalation** (trigger-gated: no accepted verdict, no grounded record, or an unknown-state record mentioned in-window): the same loop with `GHOST_ESCALATION_TURN_LIMIT`, `describe_evidence` + read-only `sql_probe`, same typed outcome.

## 8. COMPOSE (code)

Findings → remediation (flows and restores first) → verification (every remediated item +
account ABSENT + retained records PRESENT). Sort: phase → statement's operation order →
`system_id` → `resource_id`. COMPLETE = verification only, each citing the newest proving
observation.

## 9. Evaluation

Metrics vs `sample_output.csv` and custom cases: status accuracy, step-level P/R on full
tuples, field-level accuracy, sequence accuracy. Baseline vs agent with per-case diff.
≥3 custom cases derived from the real dataset with hand-written expectations: a
non-deletion text, a historical-email subject, a time-cut case with later evidence, a
recreated-by-sync case.

## 10. Decisions log

- Intent: the text rule first (English scoping / negation patterns). Revised: the 10 `CLASSIFY_REQUEST` rows ("privacy intake classified request for account_x as non-deletion") do link — through the subject's own identifier-linked account — and map 1:1 to the 10 non-deletion requests, never to a deletion request. They are a language-independent second signal: NOT_DELETION when either fires; the agent sees the tag as a dated claim and decides disagreements from the text's meaning. Output unchanged (10/10 already caught by text); the gain is robustness for non-English non-deletion texts.
- `reported_status` is a claim (right in 13/32 samples); never a decision input.
- Recreated-by-sync record → both `REPORT_REMAINING_RECORD` and `REPORT_ACTIVE_DATA_FLOW`.
- `user_id` and `resource_id` are first-class join keys.
- Prompt injection: no injection text exists in this dataset; handled structurally (tool output as data, guards on IDs), nothing more.
- Tests only where they protect a rule or freeze a measured result; the agent is measured, not unit-tested.
- One model setting for all roles; tiering only if measurement shows a need.
- Evidence links records in three ways and the index serves all three: identifier match, resource link (`source_resource_id`), absence by full primary table (`customer_profiles_primary`, `search_documents_primary`).
- The deletion worker's attempted action (DELETE/REDACT/ANONYMIZE record, CHECK_DATA_FLOW, CHECK_RESTORE_ACCESS) matched the expected remediation operation in 40/41 sample steps; it is the policy engine's recorded category decision and is used as the treatment signal, with `deletion_rules.csv` as the fallback. Record *state* still comes only from evidence.
- Linking is resource-centric: once a resource is tied to the subject, its full history up to `audit_at` is used, including identifier-less observations (404, anonymized).
- A window-linked non-account record is kept only if it has evidence by `audit_at`; when several accounts match a subject, the one with worker activity in the request window is kept.
- The linker's pick is not final: every status-consistent row in the request window travels with the portfolio as a `window-alternative` candidate (with its own evidence and status). The agent decides per candidate; the baseline uses only the pick. On the samples this makes 8 of the 11 unreachable resources reachable (the remaining 3 exist in no participant file); ~2.6 candidates are offered per case.
- Time cut is by the manifest's `captured_at` of the *file* as well as the record's own timestamp: a snapshot inside a file captured after `audit_at` is not citable (sample key cites evidence_92 over the later bundle for the same state).
- Membership may be learned from any file (the key lists future checks for retained records seen only in later files); state and citations are cut at `audit_at`.
- Absence proofs: typed SQL primaries are whole tables; a bundle collection snapshot (even an empty one) proves absence only for subjects with another record in the same file (bundles are scoped).
- Intent rule (baseline): a request is a deletion unless it scopes itself (marketing-only, export, correction, access) or negates deletion ("not asking for deletion", "don't delete"); 10/518 and 0/32 classified NOT_DELETION, matching the data.
- Deleted accounts (404) and anonymized analytics carry no identifiers by design; 262/518 requests have no identifier-linked account. The portfolio links them through the activity log's request window (`requested_at` → `reported_at`, status-consistent, one account row per request, closest-to-`reported_at` tie-break). Measured 25/32 on samples; 164 requests stay ambiguous and are flagged in the trace. Agent tie-breaking is a step-6 experiment.
- Step-6 result: the agent must not re-decide identifier-less candidates. Sonnet promoted batch candidates to the subject by guesswork (same analytics id claimed in two cases; right 2 of 8 times); the deterministic tie-break is better. `merge.harden` allows a candidate as "yes" only with a record-level subject identifier and fills records the agent omitted from the baseline. Hardened agent = baseline on the samples; its value lies where the parsers see nothing (raw search), which the samples cannot show.
- 100-case run (Sonnet, $0.026/case, 97% cached input): after dropping `uncertain`, 11/100 differed from the baseline; review showed activity ids cited as state proof (the key cites only files), linked audit records demoted for lack of a file, one unsupported treatment override, and one real find (a PDF proving a retained audit record). Adopted: guard rejects activity/audit ids as state evidence; hardening restores membership of linked records unless the record carries someone else's identifiers; treatment of portfolio records is the policy's. The real find was a parser gap (identifiers inside free text) and was fixed deterministically. Result: hardened agent == baseline on 100/100 and on the samples. The agent's measured role is "parser-gap detector" plus the contract floor; its remaining upside is on files the parsers cannot read (images, step 7).
- Vision: the model reads each screenshot once into typed JSON (ids, identifiers, fields, table rows, lines); the parser attests only the record a screen displays in detail (title / side panel) with identifiers, list rows are context (searchable, not linked) — this keeps decoy rows (g.silva76.jr, other owners' backups) and filler rows (audit_record_001-006 next to the case's 003) out. Id prefix decides the owning system before the screen's origin. Status words: Healthy / Continuous sync = flow enabled; an existing archive with no block = RESTORABLE; a data flow with no on/off evidence = UNKNOWN (not DISABLED). Backup logs with `decision=deny` = BLOCKED.
- Request-time cutoff measured and rejected: the key never cites a file captured before `requested_at` (0/75), but that is placement, not a rule — export_1260 is seen in June and September, so it existed for the July request too; the cutoff would have flipped case_33 wrongly and dropped four evidence_54 absence proofs. State = newest evidence at or before `audit_at`.
- Escalation is trigger-gated, not blanket: measured before building, 265 cases have a workflow-touched record with no in-window state evidence, but none of those records is mentioned in any in-window file, so a larger budget cannot find anything for them; the escalated loop runs only on T1/T2/T3 (1 case today). The user chose the thin build over forcing all 265 through (~$8, nothing to find).
- Tool contract: every tool docstring is input / returns / use when / next step; every output starts with a header stating format and count; the system prompt carries a "Reading tool results" dictionary. Added after the user's note that correct tool output is useless if the agent cannot read it.

## 10a. Context layer (ktx-inspired)

`code/ghost/context/wiki/*.md` is the agent's semantic context, hand-authored in the coding session and reviewed like code (no model generates it): `systems`, `identifiers`, `policy`, `evidence_formats`, `time_rules`, `intent` (labelled examples in en/pt/es/fr/ko/ja), `reading_tool_results`. Core pages ride in the cached system prompt (~5.3k tokens, one prefix per run); every page is available through `get_context(topic)`. The executable half is the schema catalog the index builds deterministically from every file (tables / collections, row counts, columns), exposed through `describe_evidence(evidence_id)` so `sql_probe` never guesses table names. Thesis mapping: wiki = semantic context, index + sql_probe = value lookup, the two tools = progressive disclosure. Interface decision pending: in-process tools only, or also an MCP server over the same functions.

## 10c. Safety instrumentation

- Guard refusals are classified by kind (`guards.refusal_kind`: hallucinated_file, post_audit_evidence, activity_as_state, unseen_resource, no_subject_identifier, file_lacks_record, uncited_status, delete_on_retained, duplicate_resource, wrong_system, not_deletion_with_resources) and counted per run (`tracing.refusal_counts`) — the measured "hallucinations caught" line of the report.
- Intent is reconciled from three signals (`merge.reconcile_intent`): model, text rule (scoping, negation in six languages, deletion-or-audit vocabulary), privacy-intake tag. The deterministic signals decide; the model's intent is advisory and every disagreement is recorded (`intent_signals`) and printed. Measured: the rules reproduce the 10 non-deletion requests on all 550 texts; the first asymmetric version (model NOT_DELETION stands) let a one-turn, no-tool model answer flip a plain deletion request (custom_07), so it was replaced.

## 10b. Code-rubric batch 1 (closed)

- Prompt refusals section (no instruction-following from request_text / evidence, no uncited claims, UNKNOWN over guessing, NOT_DELETION rule, any language) and the same data-only clause in the vision prompt; covered by `tests/test_prompts.py`.
- Function size: `build_tools` split into one factory per tool, graph nodes into `_JudgeNodes`, `extract_image` into cache / call / write, `image.parse` → `_attest`, bundle snapshots → `_snapshot`, window linking → `_window_links`; no function above 50 lines.
- `ghost/validate.py` + `--validate`: contract check of output.csv (rows, order, statuses, steps schema, operation/state pairs, phase and sort order, numbering, evidence in manifest and ≤ audit_at); runs after every full run; tested.
- `python3 code/main.py` with no arguments = full run (agent when `ANTHROPIC_API_KEY` is set, baseline otherwise, printed mode), vision-cache warning, validation; `--baseline-only` to force the floor.
- Packaging: tests moved to `code/tests`, `code/requirements.txt` exported from the lock, zip command includes `pyproject.toml`, `uv.lock`, `.env.example`.

## 11. Implementation steps

| # | Step | Deliverable | Measurement | Status |
|---|---|---|---|---|
| 1 | Skeleton | pyproject, lock, config, loaders, Observation model, `--check` | counts 518/32/32/850/306/4011/15/94; ruff + mypy clean | done |
| 2 | Parsers + index | `parsers/*`, `index.py` → `.cache/index.db` | 2,891 observations; sample resources: 165/193 by identifier, 1 by link, 27 unlinked (no identifiers after deletion → step 4 timing rule); citations 59 direct + 3 by absence proof of 75 | done |
| 3 | Identity | `identity.py` | 518/518 resolve (418 current, 100 historical); tests cover a two-rename chain and a decoy | done |
| 4 | Portfolio (Plan) | `portfolio.py`, `policy.py`, `linking.py` | sample resources recall 100/111, extras 6; status agreement 68/68 on finding + COMPLETE-verification steps; misses are the no-identifier class (6 analytics, 3 accounts tie-broken, 1 account with no activity, 2 audit records absent from all data) | done |
| 5 | Composer + harness + baseline | `compose.py`, `agent/schemas.py`, `agent/baseline.py`, `evaluation/metrics.py`, `runner.py`, `output.py` | baseline on 32 samples: status 100%, step P/R/F1 96.8/94.3/95.5%, field 100%, sequence 62.5%; remaining diffs are the ambiguity class only | done |
| 6 | Agent | `agent/{tools,prompts,graph,guards,merge}.py`, `tracing.py`, `evaluation/rescore.py`; raw FTS5 line index in `data/index.py` | Sonnet 5.5 on 32 samples: raw agent 73.0/97.9/83.6 (uncertain kept) / 92.0/94.8/93.4 (dropped); with hardening (no candidate promotion without record-level identifier, omitted records filled from baseline) identical to baseline 96.8/94.3/95.5, seq 62.5%; 0 fallbacks, 0 guard retries, 3.6 turns, $0.033/case | done (agent = baseline parity on samples; value to be measured on the 518) |
| 7 | Vision | `data/vision.py` (one-shot Opus 5.5 extraction, cached by sha256 under `.cache/vision`), `data/parsers/image.py`, `--extract-images` | 11 images read once for $0.59; every extracted identifier that should exist matches `user_identities.csv` exactly, decoys kept apart; 26 image observations; samples unchanged (100 / 96.8 / 94.3 / 95.5 / 62.5); 9 of 518 rows changed, 7 COMPLETE→INCOMPLETE where a post-claim screenshot shows the record alive (image family case_33–55) | done |
| 8 | Escalation | `agent/escalation.py`: deterministic triggers (T1 no accepted verdict, T2 no grounded record, T3 unknown-state record mentioned in an in-window file), second bounded loop (`GHOST_ESCALATION_TURN_LIMIT`), read-only time-filtered `sql_probe`, wider `read_evidence`; word-boundary fix in `search_evidence`; `tests/test_escalation.py` | triggers on the data: T2 ×1 (case_63), T3 ×0 (275 unknown-state records have no in-window mention; the 2 raw hits were substring false positives), T1 ×0 so far; case_63 escalated (7 extra turns, 4 `sql_probe` calls, 1 guard refusal corrected), result identical to baseline | done (thin: built and tested, not forced over non-triggering cases) |
| 9 | Full run | `python3 code/main.py` (agent + floor), traces/ | final run on the shipped code: 518 rows, 0 violations, 0 rows differ from the floor after hardening rule 5 (screen context rows, case_45); 1,738 calls, 3.36 turns, 0 fallbacks, 1 escalation, 15 refusals all corrected, $13.72 (+$0.59 vision) | done |
| 10 | Ship | 7 custom cases (7/7 floor and agent), format ablation (`--ablate`), both reports, README, `ARCHITECTURE.md` with four diagrams, review batches A–D (guard holes, robustness, dead code, docstrings, 90 tests), bundled vision cache, `code.zip` | fresh venv from `requirements.txt`: 90 tests pass, default run without a key reproduces the submitted output (0 rows differ), samples 100 / 95.5 | done |
