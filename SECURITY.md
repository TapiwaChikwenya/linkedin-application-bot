# Security Policy

## Reporting

Report vulnerabilities privately to the repository maintainers. Do not open a public issue containing
credentials, session cookies, resumes, application answers, screenshots, or saved page HTML.

## Credential handling

- Store secrets only in environment variables or an ignored `.env` file.
- Use a dedicated browser profile and enable multifactor authentication.
- Rotate any credential that was previously committed or shared.
- Review `git diff --cached` before every commit.

The project does not bypass CAPTCHA, multifactor authentication, platform limits, or security checks.

LinkedIn's own help pages prohibit third-party software that automates activity on the site.
This repository does not add stealth, challenge bypasses, or higher apply throughput. Operator
controls (start, stop, Ollama stop, resume delete) reject cross-origin browser requests so a
page on another site cannot drive the local console.
