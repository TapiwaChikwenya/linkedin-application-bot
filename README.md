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

Validate without opening a browser:

```powershell
linkedin-easy-apply --check
```

Run the assistant:

```powershell
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
LINKEDIN_PHONE_NUMBER=5551234567
LINKEDIN_APPLICATION_CITY=Dallas, Texas, United States
```

Unknown screening questions are not guessed; the application is left for manual review. Never put a
password, session cookie, phone number, resume, or browser profile path in a committed file.

## Output and diagnostics

Runtime artifacts are written under `data/`:

- `Applied Jobs DATA - YYYYMMDD.txt` — local application audit log
- `easy_apply_failure_JOB_ID.png` — screenshot at failure
- `easy_apply_failure_JOB_ID.html` — application modal HTML
- `easy_apply_page_JOB_ID.html` — full page HTML for selector diagnosis

The entire directory is ignored by Git because these files can contain personal information.

## Development

```powershell
python -m pytest
python -m ruff check src tests
python -m build
```

CI runs these checks on Python 3.10, 3.11, and 3.12. See [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md) before opening a pull request.

## Project layout

```text
.
├── src/linkedin_easy_apply/  # installable CLI package
├── tests/                    # offline unit tests
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
- Employer-specific free-text questions require manual review unless an explicit truthful mapping exists.
- Applications submitted through LinkedIn cannot generally be edited or withdrawn through Easy Apply.
- Rapid or high-volume submissions may trigger platform limits.

## License

See [LICENSE.md](LICENSE.md).
