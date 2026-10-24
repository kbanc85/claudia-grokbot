# Scanner evals

About 25 fictional email, calendar, and call snippets with known expected promises, including tricky negatives (vague catch-ups, already-done items, third-party promises, questions, hypotheticals).

Made with Grok Bot.

```bash
python3 evals/run_eval.py
```

Default thresholds: precision ≥ 0.85, recall ≥ 0.80. The runner exits non-zero below threshold.
