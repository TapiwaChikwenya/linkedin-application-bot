# Contributing

1. Create a focused branch from `main`.
2. Install development dependencies with `python -m pip install -e ".[dev]"`.
3. Never commit credentials, profile paths, application data, screenshots, or page HTML.
4. Run `python -m pytest`, `python -m ruff check src tests`, and `python -m build`.
5. Describe behavior changes and manual browser verification in the pull request.

Selectors should prefer accessible names, roles, and stable test attributes. Generated Ember IDs,
absolute XPath expressions, and automation intended to bypass security controls are not accepted.
