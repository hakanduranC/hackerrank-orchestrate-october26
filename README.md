# Ghost Data Still Exists — deletion-audit agent

An AI-powered auditor that decides, for each of 518 deletion requests, whether a person's data
was actually removed by the audit time, and produces the ordered remediation and verification
plan. Submission for HackerRank Orchestrate, October 2026.

The system is a **plan–execute–verify agent with a deterministic parity floor**: code builds
the case (identity, evidence index, policy, linked records), a bounded tool-calling agent
investigates it and submits a typed verdict, deterministic guards verify every claim, and code
composes the output. Design and measurements: [`code/ARCHITECTURE.md`](code/ARCHITECTURE.md),
[`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md),
[`code/evaluation/usage_report.md`](code/evaluation/usage_report.md).

## Setup

Python 3.12. Dependencies are pinned in `pyproject.toml` / `uv.lock`; `code/requirements.txt`
is exported from the lock for pip users.

```bash
# with uv
uv sync

# or with pip
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r code/requirements.txt
```

Secrets come from the environment only. Copy `.env.example` to `.env` and set
`ANTHROPIC_API_KEY`. Every other setting has a default:

| Variable | Default | Meaning |
|---|---|---|
| `GHOST_MODEL` | `claude-sonnet-5-5` | judge model (the one that produced the submitted output) |
| `GHOST_VISION_MODEL` | `claude-opus-5-5` | one-shot screenshot reader |
| `GHOST_EFFORT` | `medium` | reasoning effort |
| `GHOST_JUDGE_TURN_LIMIT` / `GHOST_ESCALATION_TURN_LIMIT` | `8` / `12` | model-turn caps of the two loops |
| `GHOST_RETRY_ALLOWANCE` | `2` | guard refusals the model may answer before the floor takes over |
| `GHOST_CONCURRENCY` | `4` | cases processed in parallel |
| `GHOST_DATASET_DIR`, `GHOST_OUTPUT_PATH`, `GHOST_CACHE_DIR`, `GHOST_TRACE_DIR` | `dataset`, `output.csv`, `.cache`, `traces` | paths, relative to the repository root |
| `GHOST_DISABLE_PARSERS` | empty | format ablation: parsers to switch off (`sql,json,log,text,pdf,image`); raw lines stay searchable |
| `GHOST_MAX_TOKENS`, `GHOST_MODEL_TIMEOUT`, `GHOST_VISION_TIMEOUT`, `GHOST_MAX_RETRIES` | `8192`, `120`, `180`, `3` | model-call parameters (tokens per call, seconds, SDK retries on 429/5xx) |
| `GHOST_READ_WINDOW`, `GHOST_ESCALATION_READ_WINDOW` | `25`, `60` | lines each side that `read_evidence` may return in the judge / escalated loop |

## Run

```bash
python3 code/main.py                     # full run: all 518 requests -> output.csv (+ validation)
python3 code/main.py --baseline-only     # same, deterministic floor only (no API key needed)
python3 code/main.py --validate          # check output.csv against the output contract
python3 code/main.py --extract-images    # read the 11 screenshots once (cached by content hash)
```

With `ANTHROPIC_API_KEY` set, the default run sends every case through the agent; without it,
the deterministic floor produces the output and says so. The evidence index (`.cache/index.db`)
is built on first use. The 11 screenshot extractions (`.cache/vision/`) are produced once by
`--extract-images` (≈ $0.60) and reused by every run; without them the run prints a warning
and the nine rows that depend on screenshot evidence come out as if those files were empty.

Evaluation:

```bash
python3 code/evaluation/main.py --baseline --verbose      # floor vs sample_output.csv, per-case diff
python3 code/evaluation/main.py --agent --verbose         # agent vs sample_output.csv (+ usage line)
python3 code/evaluation/main.py --custom [--agent]        # the constructed cases
python3 code/evaluation/main.py --ablate sql [--agent]    # format ablation: one parser off, raw lines kept
python3 -m ghost.evaluation.rescore --harden              # re-score stored verdicts offline
python3 -m ghost.evaluation.diff a.csv b.csv              # row-by-row diff of two outputs
python3 -m pytest code/tests -q                           # unit tests
```

(`python -m ghost...` needs `code/` on `PYTHONPATH`; `uv run` and the entry points set it up.)

## Output

`output.csv` at the repository root: `case_id,deletion_status,steps_json`, one row per request,
sorted by case id. `deletion_status` is `COMPLETE`, `INCOMPLETE` or `NOT_APPLICABLE`;
`steps_json` is the ordered array of findings, remediation and verification steps defined in
`problem_statement.md`. The validator (`--validate`) enforces the contract: row set and order,
step schema and numbering, operation/state pairs, phase and sort order, findings citing files
from the manifest captured at or before `audit_at`.

## Layout

```
code/main.py                  entry point (CLI in ghost/cli.py)
code/evaluation/main.py       evaluation entry point
code/ghost/
  data/                       loaders, Observation model, parsers (sql/json/log/text/pdf/image),
                              vision extraction, SQLite evidence index (FTS5 raw lines, schema catalog)
  identity.py linking.py      current + historical identifiers; activity-window linking
  policy.py portfolio.py      deletion rules; the per-case portfolio (the plan)
  context/wiki/*.md           the context layer the agent reads (systems, identifiers, policy,
                              evidence formats, time rules, multilingual intent, tool results)
  agent/                      tools, prompts, LangGraph loop, guards, hardening, escalation, baseline
  compose.py validate.py      verdict -> ordered steps; output contract validation
  evaluation/                 metrics, custom cases, rescore, diff
```

`code/tests/` holds the unit tests (pytest).

Only files under `dataset/` are read. No case ids, labels or answers are hardcoded; the
constructed evaluation cases live in `code/ghost/evaluation/custom_cases.py` with their
expectations written by hand.

## Packaging

```bash
zip -r code.zip README.md code pyproject.toml uv.lock .env.example \
  -x "*/__pycache__/*" "*/.venv/*" "*/.git/*" "*/.DS_Store" "*/.mypy_cache/*" "*/.pytest_cache/*" "*/.ruff_cache/*"
```

The dataset, caches, traces and `.env` are never included.
