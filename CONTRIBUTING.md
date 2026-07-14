# Contributing to VerdictAI

Thanks for your interest in improving VerdictAI! This document explains how to
get set up and what we expect from contributions.

## Code of Conduct

By participating you agree to uphold our [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting set up

```bash
git clone https://github.com/Manseez31/VerdictAI.git
cd VerdictAI
uv sync
cp .env.example .env      # add your GROQ_API_KEY
uv run pytest             # should be 77 passing
uv run uvicorn backend:app --reload
```

## Ground rules

VerdictAI is a **legal-education tool**. Two rules are non-negotiable:

1. **Safety.** Contributions must never make the system assist with committing
   crimes, evading law enforcement, destroying or tampering with evidence, or any
   other unlawful conduct. Every agent prompt carries a safety preamble — do not
   weaken or remove it.
2. **Grounding.** Legal outputs must be grounded in retrieved sources and cited.
   Do not add features that invent statutes, sections, or evidence.

## Making a change

1. **Open an issue first** for anything non-trivial, so we can agree on the approach.
2. Branch from `main`: `git checkout -b feat/short-description`.
3. Write your change **plus tests**. Tests must run offline — fake the LLM and the
   retriever (see `tests/test_case_intelligence.py` for the pattern). No test may
   require an API key, network access, or a model download.
4. Run the suite: `uv run pytest`.
5. If you touched retrieval, **run the benchmark** and include the numbers in your
   PR: `uv run python benchmark.py --k 5 --no-hallucination`. Retrieval changes are
   judged on measurements, not intuition.
6. Open a pull request using the template.

## Style

- **Python** — follow the surrounding code: type hints on public functions,
  docstrings that explain *why* rather than *what*, `logging` (never `print`).
- **JavaScript** — vanilla ES modules, no build step, no new dependencies. Use the
  existing `el()` DOM helper and the shared design tokens; do not hand-roll colors.
- **Accessibility is required** — semantic HTML, ARIA where needed, keyboard
  navigation, and visible focus states.
- Keep comments minimal and meaningful; explain constraints, not the obvious.

## What we especially welcome

- **More Acts in the knowledge base** — the biggest quality lever (see Roadmap).
- Improved retrieval (a multilingual reranker, better chunking).
- Span-level relevance labels for the evaluation gold set.
- Bug fixes, accessibility improvements, and documentation.

## Reporting security issues

Please do **not** open a public issue. See [SECURITY.md](SECURITY.md).
