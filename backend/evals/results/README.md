# Saved grader evaluation runs

Every `eval-grader` run is saved here as one JSON file (unless `--no-save`), named

    <UTC date-time>_<provider-model>[_<label>][_partial].json

so the folder lists runs chronologically. Each file is the full report: summary metrics, metrics
per category, every case with the grader's answer, token usage, and a `run` block with what's
needed to compare runs fairly:

| `run` field | Meaning |
|---|---|
| `started_at` | When the run started (UTC) |
| `label` | Optional note from `--label`, e.g. `"prompt v2"` |
| `git` | Commit of the code that ran, and whether the working tree had uncommitted changes |
| `cases_fingerprint` | Hash of the case set: compare metrics only between runs with the same value |
| `n_cases` | Number of cases |

Compare runs:

```sh
scripts/eval-compare.sh                 # table of the last 20 runs
scripts/eval-compare.sh baseline v2     # two runs (any unique part of the file name):
                                        # metrics side by side + cases whose outcome changed
```

Reports contain the eval cases and the model's grading, no API keys. They are committed like
any other file, so the history of the grader's quality is kept with the code; delete runs you
don't want to keep (e.g. experiments with a broken configuration).
