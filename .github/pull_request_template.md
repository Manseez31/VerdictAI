## What does this PR do?

<!-- A short description of the change and why it's needed. -->

## Related issue

<!-- e.g. Closes #12 -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Retrieval / model change
- [ ] Documentation
- [ ] Refactor / chore

## Checklist

- [ ] Tests added or updated, and `uv run pytest` passes
- [ ] New tests run **offline** (LLM and retriever faked — no API key or network)
- [ ] No secrets committed (`.env` is git-ignored)
- [ ] Existing functionality and API contracts are preserved
- [ ] Comments/docs updated where behavior changed

## Safety

- [ ] This change does not weaken the agent safety preambles
- [ ] Legal output remains grounded in retrieved sources and cited

## Retrieval changes only

If you changed retrieval, paste benchmark results
(`uv run python benchmark.py --k 5 --no-hallucination`):

```
Metric                  Dense only    Hybrid    (your change)
Recall@5
MRR
Context Precision@5
Retrieval latency (ms)
```
