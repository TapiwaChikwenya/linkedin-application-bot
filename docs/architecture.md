# Architecture

The console script in `linkedin_easy_apply.cli` validates configuration and delegates to the legacy-compatible
workflow in `linkedin.py`. Browser lifecycle is owned by `Linkedin`; `finally` guarantees cleanup. Search URL
generation and audit persistence remain isolated in `utils.py`, while `config.py` contains non-secret policy
and truthful answer mappings.

Application controls are located through semantic text, accessible roles, native dialog attributes, and stable
test attributes. Each form step is reacquired after navigation because LinkedIn replaces DOM nodes during modal
transitions. Unknown required questions produce a local diagnostic rather than guessed answers.

Future refactoring should move `linkedin.py`, `config.py`, `constants.py`, and `utils.py` fully beneath the package
without changing the public CLI entry point.
