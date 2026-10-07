# LinkedIn Easy Apply Assistant

A configurable Selenium assistant for reviewing and completing LinkedIn Easy Apply forms. It supports
Firefox and Chrome, authenticated Firefox profiles, contact-field completion, truthful experience
answers, explicit Yes/No screening mappings, application audit logs, and failure diagnostics.

> **Responsible use:** Review your answers and target roles that match your qualifications. This project
> does not bypass CAPTCHA, multifactor authentication, security checks, or LinkedIn submission limits.
> LinkedIn may limit or restrict automated activity. You are responsible for complying with platform
> terms, applicable law, and employer requirements.

## Requirements

- Python 3.10–3.12
- Firefox or Chrome
- A LinkedIn account with an authenticated browser profile, or credentials supplied through local
  environment variables

## Installation

Clone the repository and create a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Create local configuration:

```powershell
Copy-Item .env.example .env
```

Edit `.env`; it is ignored by Git. Prefer `FIREFOX_PROFILE_PATH` so LinkedIn login is retained without
storing an account password. See [configuration documentation](docs/configuration.md).

The easiest way to run it on Windows is to double-click **one** shortcut:

- `Start-Dashboard.cmd` — opens the operator console at `http://127.0.0.1:8787`. That is the only `.cmd` you need after setup.

From the console:

- **Start run** starts Ollama (serves and pulls `llama3.2` if missing) then the Easy Apply worker.
- **Stop** stops the worker only.
- **Login** opens the bot Firefox profile at LinkedIn login (same flow as `--login`). Sign in, confirm the feed, close Firefox, then Start run.
- **Stop Ollama** stops the `ollama serve` process this dashboard started.
- **Questions** reviews captured and generated prompts. Email is a text/email input (never a select of `[email]`); refresh the page so live SQLite backfill can rewrite old rows. **Generate seed questions** inserts pending-only rows (never auto-approved).
- **Settings** picks the Ollama model, uploads a resume PDF, sets daily/run caps, and optional local-timezone run windows.
- Live / Applications / Diagnostics cover status, history, screenshots, and operator metrics (`GET /api/metrics`). See [docs/metrics.md](docs/metrics.md).

You do **not** need `Start-Bot.cmd`, `Start-Ollama.cmd`, or `Login-LinkedIn.cmd` once the dashboard is open. Those files remain as optional fallbacks (headless worker, a manual `ollama pull`, or login from Explorer).

From PowerShell in the project folder:

```powershell
.\Start-Dashboard.cmd
```

Then click **Start run**. Close the dashboard window to stop the console (the worker can keep running until you click Stop). The dashboard window stays open so errors are readable. Do not type `Activate.ps1` or `linkedin-easy-apply.exe` from inside `.venv\Scripts`; PowerShell ignores files in the current folder unless they are prefixed with `.\`.

The default pace is human-like: long pauses between jobs, slower typing, and a stop after 12 applies in a run or 25 in a **local** calendar day. Dashboard **Settings** edits model, resume PDF, caps, and local-timezone schedule (SQLite overlay; `.env` is fallback). See [running documentation](docs/running.md).

The worker fills approved dashboard answers first, then specific `config.py` bootstrap maps, then a local Llama sidecar for leftovers. Approving questions grows SQLite RAG memory; it does not fine-tune Ollama. Install Ollama from https://ollama.com/download once. After that, dashboard **Start run** starts `ollama serve` and pulls `llama3.2` if it is missing. `Start-Ollama.cmd` is only a manual fallback. Details are in [docs/llm.md](docs/llm.md). Keeping the model loaded uses RAM.

The package command still works after the virtual environment is activated:

```powershell
.\.venv\Scripts\Activate.ps1
linkedin-easy-apply --check
linkedin-easy-apply
```

The legacy command remains supported:

```powershell
python linkedin.py
```

Run only one instance at a time. Complete any CAPTCHA, multifactor authentication, or account-security
prompt manually.

## Configuration

Search preferences and truthful screening mappings are defined in `config.py`. Secrets and personal
values are environment-based:

```dotenv
FIREFOX_PROFILE_PATH=C:\Users\you\AppData\Roaming\Mozilla\Firefox\Profiles\profile-name
LINKEDIN_EMAIL=you@example.com
LINKEDIN_PASSWORD=
LINKEDIN_PHONE_NUMBER=5551234567
LINKEDIN_APPLICATION_CITY=Dallas, Texas, United States
LINKEDIN_RESUME_PATH=C:\path\to\resume.pdf
```
If you use Chrome, set `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD`. Firefox can reuse `FIREFOX_PROFILE_PATH` instead of storing a password.

Unknown screening questions are not invented as long essays. Approved SQLite answers fill first,
then specific `config.py` **bootstrap** maps (not generic experience→Yes or
`years_experience["default"]` — those catch-alls are removed). If Ollama is running
locally, leftover short fields can be generated from resume facts plus those approved answers.
Approving Questions rows grows RAG memory; it does not fine-tune Ollama.
See [docs/llm.md](docs/llm.md). Never put a password, session cookie, phone number, resume, or
browser profile path in a committed file.

## Output and diagnostics

Runtime artifacts are written under `data/`:

- `Applied Jobs DATA - YYYYMMDD.txt` — local application audit log
- `easy_apply_failure_JOB_ID.png` — screenshot at failure
- `easy_apply_failure_JOB_ID.html` — application modal HTML
- `easy_apply_page_JOB_ID.html` — full page HTML for selector diagnosis

The entire directory is ignored by Git because these files can contain personal information.

## Development

The default **regression gate** for any new feature is:

```powershell
python -m pytest tests -q
```

That is also `addopts = "-q"` plus `testpaths = ["tests"]` in `pyproject.toml`. Run it
before considering a feature done. It uses TestClient and mocks; it does **not** start
the LinkedIn worker and does **not** `ollama pull` large models.

```powershell
.\scripts\test-regression.ps1
python -m ruff check src tests
python -m build
```

`scripts/test-regression.ps1` runs `pytest tests -q` then Ruff. GitHub Actions
(`.github/workflows/ci.yml`) runs the same pytest command on Python 3.10–3.12.
Settings/Ollama picker/resume coverage lives in `tests/test_models_settings.py`
(plus dashboard/store/question tests). See [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md) before opening a pull request.

## Project layout

```text
.
├── Start-Dashboard.cmd       # the only required shortcut: opens 127.0.0.1:8787
├── Start-Ollama.cmd          # optional fallback: pull llama3.2 by hand
├── Start-Bot.cmd             # optional fallback: worker without the dashboard
├── Login-LinkedIn.cmd        # optional fallback: same as dashboard Login
├── src/linkedin_easy_apply/  # installable CLI package
├── tests/                    # offline unit tests (pytest tests -q)
├── scripts/test-regression.ps1  # local regression gate wrapper
├── docs/                     # operator documentation
├── .github/workflows/        # continuous integration
├── linkedin.py               # browser workflow and compatibility entry point
├── config.py                 # non-secret search and answer rules
├── constants.py              # timing and pagination constants
├── utils.py                  # URL, browser, and audit helpers
└── pyproject.toml            # package and tool configuration
```

## Known limitations

- LinkedIn changes its DOM frequently; accessible selectors and saved diagnostics reduce but do not
  eliminate maintenance.
- Employer-specific free-text questions still need facts in the resume, `config.py`, or
  `LINKEDIN_APPLICANT_SUMMARY`. The local model will not invent essays.
- Applications submitted through LinkedIn cannot generally be edited or withdrawn through Easy Apply.
- Rapid or high-volume submissions may trigger platform limits.

## Roadmap

A local Llama sidecar scores job fit and fills leftover Easy Apply fields from
approved RAG memory. See [llm.md](docs/llm.md). Daily operation is: open
`Start-Dashboard.cmd`, click **Start run**. Search keywords stay in `config.py`;
model / resume / caps / schedule are on **Settings**.

## License

See [LICENSE.md](LICENSE.md).
