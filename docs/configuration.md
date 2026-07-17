# Configuration

Runtime secrets and personal information are read from environment variables. Copy `.env.example` to
`.env` for local use. The `.env` file is ignored by Git.

| Variable | Required | Purpose |
|---|---:|---|
| `FIREFOX_PROFILE_PATH` | Recommended | Reuses an authenticated Firefox profile |
| `LINKEDIN_EMAIL` | Optional | Login fallback when no authenticated profile is available |
| `LINKEDIN_PASSWORD` | Optional | Login fallback password |
| `LINKEDIN_PHONE_NUMBER` | Optional | Easy Apply contact forms |
| `LINKEDIN_APPLICATION_CITY` | Optional | City autocomplete on contact forms |

Non-secret search filters and truthful screening answers currently live in `config.py`. Unknown
screening questions are deliberately left for manual review.
