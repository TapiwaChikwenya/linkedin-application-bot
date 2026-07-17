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
