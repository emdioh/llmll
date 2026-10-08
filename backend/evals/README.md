# Grader evaluation

Measures the production grader (LLM + reconciliation with LanguageTool) against hand-annotated
cases. Run it after every change to a grading prompt, the model, or `app/domain/reconcile.py`.
Design: `docs/design/M5.md` §1–2.

**It is not part of CI.** With a real API key every case is one `grade_sentence` call (70 cases
x `--repeat` runs, each with the full item context and glossary): it costs money and takes minutes.
Use `--repeat 1` for routine checks and `--repeat 3` only to measure consistency.

## Running

```sh
cd backend
export ANTHROPIC_API_KEY=sk-ant-...        # or the key of the provider you compare; without any key the fake LLM is used: numbers are meaningless
uv run python -m app.cli eval-grader --label baseline
```

Options:

| Option | Meaning |
| --- | --- |
| `--cases DIR` | Directory of `*.yaml` case files (default `evals/grader/cases`). |
| `--curriculum DIR` | Curriculum used to build the item contexts (default `../curriculum/de`). |
| `--task-config JSON\|FILE` | Per-task LLM overrides, same shape as `LLMLL_LLM_TASKS`, e.g. `'{"grade_sentence": {"model": "claude-opus-4-5", "effort": "max"}}'`. Use it to compare models or effort levels. |
| `--provider P` | Provider for `grade_sentence` (`anthropic`, `openai`, `google`, `openrouter`), overriding the configuration. Needs that provider's API key and, except for Anthropic, `--model`. |
| `--model M` | Model for `grade_sentence` (with `--provider`, or for the default provider). |
| `--repeat N` | Grade every case N times; reports agreement across runs (consistency). |
| `--label TEXT` | Note saved with the run and in its file name, e.g. `"prompt v2"`. |
| `--results-dir DIR` | Where every run is saved (default `evals/results`, see below). |
| `--no-save` | Don't save this run. |
| `--out FILE` | Also write the full JSON report to FILE. |
| `--max-fp RATE` | Fail (exit 1) when the false-positive rate on correct answers exceeds RATE (default 0.05). |
| `--no-languagetool` | Never query LanguageTool; use the matches recorded in the cases (if any). |
| `--quiet` | No per-case progress lines. |
| `--trace FILE` | Write every LLM call (full request and response, timings, tokens) to FILE, one JSON object per line, as the run proceeds. Large, so not saved by default; for offline inspection. |

Progress is printed on stderr, one line per graded run: the case id appears before the LLM call
(so a slow call is visible as such), then latency, `ok` / `MISS expected X, got Y` / `FAILED
<provider error>`, and the estimated time left. The summary goes to stdout. If the first 3 runs
all fail the run stops early; failures are grouped by error with a hint in the summary. Ctrl+C
stops the run and still prints (and writes, with `--out`) the report for the runs completed so far.

Each progress line ends with the timing breakdown of the call, e.g.

```
[ 3/70] ita-10                       12.3s  ok      correct  llm 12.3s = wait 0.0s + ttfb 12.1s + dl 0.1s | 1 try | out 412 (reasoning 2140) | 34 tok/s | lt 0.2s
```

`wait` is the time before the final HTTP attempt (failed attempts such as a 429 plus the SDK's
back-off), `ttfb` the final attempt's time to response headers (queue plus the whole generation:
calls are not streamed), `dl` the body transfer and parsing, `N tries (statuses)` appears when
there were retries, `out` are output tokens (with the hidden reasoning tokens when reported),
`tok/s` is `out / ttfb` and `lt` the LanguageTool time of the case. The summary adds a **latency**
block (p50 / p90 / max of total, ttfb, retry wait and LanguageTool time, total retries with the
status codes that caused them, mean output and reasoning tokens, median tok/s, OpenRouter upstream
providers). The saved report keeps these per run (`runs[].ttfb_ms`, `retry_wait_ms`, `attempts`,
`http_statuses`, `reasoning_tokens`, ...), per case (`languagetool_ms`) and as the top-level
`latency` block. Failed calls count in the block. To rule out silent retries while diagnosing, run
with `LLMLL_LLM_MAX_RETRIES=0`.

Exit codes: `0` ok, `1` false-positive rate above `--max-fp` or invalid cases, `2` some LLM calls
failed (the metrics are incomplete), `130` interrupted with Ctrl+C.

The run uses the real pipeline (`LLMClient.grade_sentence`, then `reconcile_answer`, the same function
the app calls) but **no database**: the curriculum is read from the YAML files, LLM calls are
recorded in memory (tokens, latency) instead of `llm_calls`, and every curriculum item counts as
known to the evaluated learner. LanguageTool is used when `LLMLL_LANGUAGETOOL_URL` is reachable,
otherwise the matches recorded in a case (`lt_matches`) stand in for it. As in the app, diagnostic
tags are offered for the exercise's targets only.

## Saved runs and comparing them

Every run is saved to [`evals/results/`](results/) as one JSON file named
`<UTC date-time>_<provider-model>[_<label>][_partial].json`, with the git commit, the label and a
fingerprint of the case set (compare metrics only between runs with the same fingerprint).

```sh
uv run python -m app.cli eval-compare               # table of the last 20 runs
uv run python -m app.cli eval-compare baseline v2   # two runs side by side + changed cases
```

The table also shows p50 / p90 total latency, p50 ttfb, retries and mean reasoning tokens
(`-` for runs saved before the latency block existed). Runs are picked by path or by any unique part of the file name (`scripts/eval-compare.sh` does
the same from the repo root).

## Comparing providers

`--provider` and `--model` override the `grade_sentence` route, so the same cases can be run on
several providers; the report records the result in `config.llm` (provider), `config.grader`
(`provider/model`) and `config.models`:

```sh
uv run python -m app.cli eval-grader --provider anthropic
uv run python -m app.cli eval-grader --provider openai --model <model id>
uv run python -m app.cli eval-grader --provider google --model <gemini model id>
uv run python -m app.cli eval-grader --provider openrouter --model <vendor>/<model>
uv run python -m app.cli eval-compare --last 4      # the four runs side by side
```

Token counts are those the provider reports (for OpenAI-compatible APIs the input count includes
cached tokens; Anthropic reports them separately), so compare cost with the provider's prices, not
with the raw sums. Other per-task settings (`params`, `structured_output`) go in `--task-config`.

## Metrics

All counts are summed over cases and runs.

- **Overall accuracy**: predicted `overall` label equals the expected one.
- **Error precision / recall / F1**: an expected error is detected when a predicted error has the same
  item id (null equals null) and its span overlaps the expected `span_text` (matched by text, not
  offsets; empty `span_text` matches any span). Matching is one-to-one.
- **False-positive rate** (the key trust metric, R§8): of the answers expected to be `correct`, the share
  where the grader reports any error or an overall other than `correct`. Cases of the categories
  `correct_reference`, `correct_variant` and `stylistic` all count.
- **Attribution accuracy**: of the errors whose span matches, the share attributed to the right item.
- **Tag accuracy**: of the detected errors with expected tags, the share whose predicted tags contain
  all the expected ones.
- **Correct-use recall**: expected `correct_uses` that the evaluation counts as correct or assisted.
- **Consistency** (`--repeat`): share of run pairs with the same overall label / the same set of error items.
- Cost: input/output/cache tokens and latency per call, summed or averaged in the report.

## Case format

One case per file or a list per file, in `evals/grader/cases/*.yaml`:

```yaml
- id: ita-10                      # unique
  category: italian_error         # correct_reference | correct_variant | stylistic |
                                  # italian_error | multi_error | off_task
  level: A2                       # learner level passed to the grader (default A2)
  exercise:
    type: translation             # translation | guided | transform | summary
    instructions: "Traduci in tedesco."
    prompt: "Sono andato a Roma."
    reference_solutions: ["Ich bin nach Rom gefahren."]
    glossary: []                  # optional [{item_id, de, translation}]
    targets:                      # real curriculum item ids
      - {item_id: "gram:perfekt"}
      - {item_id: "lex:fahren", role: secondary, weight: 0.5}
  answer: "Ich habe nach Rom gefahren."
  expected:
    overall: major_errors         # correct | minor_errors | major_errors | off_task
    errors:
      - item_id: "gram:perfekt"   # or null
        diagnostic_tags: [wrong-auxiliary]   # only on target items, from the item's diagnostic_tags
        severity: major
        span_text: "habe"         # substring of the answer
    correct_uses: []              # item ids used correctly
  lt_matches: null                # optional recorded LanguageTool matches
  notes: "avere/essere carry-over."
  draft: false                    # true = skipped until reviewed
```

The loader validates the structure; `tests/test_grader_eval.py` also checks every item id and tag
against the curriculum, and that the set has at least 60 cases (at least 20 correct answers that differ
from the reference). Write German carefully: a wrong expectation silently corrupts the metrics.
For a correct answer that only differs stylistically from the reference, the right category is
`stylistic`; for an alternative that is plainly equivalent, `correct_variant`.

## Turning contests into cases

Every contest the learner raised is a case where the grader was probably wrong:

```sh
cd backend
uv run python -m app.cli export-contests evals/grader/cases/new   # DATABASE: LLMLL_DATABASE_URL
```

This writes `contest-<id>.yaml` for each resolved production contest (existing files are kept):
the exercise, the learner's answer, the recorded LanguageTool matches and, as `expected`, the
**replacement** evaluation (the original one for rejected contests). Each draft has `draft: true`
and is ignored by `eval-grader` until a human has:

1. checked the German and the expectation (with the default `accept_all` resolver the expected result is
   simply "correct": verify that the answer really is),
2. set the right `category` (the exporter guesses `correct_variant`, `italian_error` or `multi_error`),
3. removed `draft: true`, and moved the file next to the other cases.
