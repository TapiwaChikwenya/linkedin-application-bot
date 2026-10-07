# Contributing

1. Create a focused branch from `main`.
2. Install development dependencies with `python -m pip install -e ".[dev]"`.
3. Never commit credentials, profile paths, application data, screenshots, or page HTML.
4. Run the regression gate before considering a feature done: `python -m pytest tests -q`
   (or `powershell -File scripts/test-regression.ps1`). Then `python -m ruff check src tests`
   and `python -m build`. Do not start the LinkedIn worker or `ollama pull` large models
   for tests; mock Ollama HTTP and subprocesses.
5. Describe behavior changes and manual browser verification in the pull request.

Selectors should prefer accessible names, roles, and stable test attributes. Generated Ember IDs,
absolute XPath expressions, and automation intended to bypass security controls are not accepted.
