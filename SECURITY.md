# Security Policy

## Supported versions

Security fixes are applied to the latest commit on `main`. Older commits and
private deployment customizations are not maintained as separate release lines.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability.

Use the repository's **Security** tab and choose **Report a vulnerability** to
open a private GitHub Security Advisory. Include:

- the affected commit or release
- reproduction steps or a minimal proof of concept
- expected and observed behavior
- potential impact
- any suggested mitigation

Do not include real credentials, private addresses, production recordings, or
transcripts in the report. Use synthetic examples and redact infrastructure
details.

## Deployment responsibility

The application does not provide authentication by itself. Internet-facing
deployments must add TLS, authentication, request-size and rate limits, and
appropriate network isolation at the reverse proxy or platform boundary. Keep
API keys and deployment configuration outside the repository.
