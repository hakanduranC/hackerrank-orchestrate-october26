# Usage report — final full-dataset run

Provider: Anthropic (Claude API, via `langchain-anthropic`). Two model roles:

| Role | Model | When it runs |
|---|---|---|
| Judge (agent loop, escalation) | `claude-sonnet-5-5`, adaptive thinking, effort `medium` | once per case during the full run |
| Vision reader | `claude-opus-5-5`, adaptive thinking | once per screenshot, cached by content hash (`.cache/vision/`); the full run makes no vision call |

Token counts are taken from the API's usage block per call and summed per case in the traces
(`traces/<case_id>.json`). Prices used for the estimates (per million tokens): Sonnet 5.5
input $2.00, cache write $2.50, cache read $0.20, output $10.00; Opus 5.5 input $4.00,
output $20.00.

## Vision reads (one-off, reused by every run)

| Images | Calls | Input tokens | Output tokens | Estimated cost |
|---|---|---|---|---|
| 11 | 11 | 31,827 | 23,360 | $0.59 |

## Judge — full run over 518 requests

Summed from the 518 traces of the final run (`python3 code/main.py`, the shipped code) that
produced the submitted `output.csv`.

| Metric | Value |
|---|---|
| Cases | 518 |
| Model calls | 1,738 (3.36 per request) |
| Input tokens (total, incl. cached) | 25,537,513 |
| – of which cache reads | 23,846,797 (93.4 %) |
| – of which cache writes | 1,686,172 |
| – uncached | 4,544 |
| Output tokens | 472,462 |
| Total tokens | 26,009,975 |
| Average tokens per request | 50,212 (49,300 in / 912 out) |
| Average model turns per request | 3.36 (1–8) |
| Estimated cost | **$13.72 total, $0.0265 per request** (cache reads $4.77, cache writes $4.22, uncached $0.01, output $4.72) |
| Fallbacks to the deterministic floor | 0 |
| Escalations | 1 (`case_63`, T2) |
| Guard refusals | 15 in 14 cases, all `file_lacks_record`, all corrected on retry |

## Overall

| | Calls | Input tokens | Output tokens | Estimated cost |
|---|---|---|---|---|
| Sonnet 5.5 (judge) | 1,738 | 25,537,513 | 472,462 | $13.72 |
| Opus 5.5 (vision) | 11 | 31,827 | 23,360 | $0.59 |
| **Total** | **1,749** | **25,569,340** | **495,822** | **$14.31** ($0.0276 per request) |

Reference points from development runs (same code path): 32 samples $0.84 ($0.026/case);
100-case slices $2.51 ($0.025/case); format ablations $1.1–1.5 per configuration; the earlier
full run before the review fixes $12.54. 93 % of judge input tokens are served from the prompt
cache (one cached system prefix per run and a moving cache breakpoint per turn); the cost
increase of the final run comes from the larger cached prefix (the `evidence_formats` page).
