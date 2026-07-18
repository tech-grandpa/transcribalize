# Contributing

Thank you for helping improve Transcribalize.

## Development setup

Requirements:

- Python 3.12
- FFmpeg
- Docker with Compose for image verification

From the repository root:

```bash
python3.12 -m venv transcriber/.venv
source transcriber/.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r transcriber/requirements-test.txt ruff
pytest -q transcriber/tests
```

Real API credentials are not required for the test suite. Never commit `.env`
files, credentials, private hostnames, private addresses, production media, or
transcripts.

## Before submitting a pull request

Run:

```bash
ruff check transcriber
pytest -q transcriber/tests
python -m compileall -q transcriber/app transcriber/tests
docker compose -f transcriber/docker-compose.yml config -q
```

Keep pull requests focused and include regression tests for behavior changes.
Use synthetic media and transcript fixtures only.

## Security reports

Do not disclose vulnerabilities in a public issue. Follow `SECURITY.md` and use
the repository's private vulnerability-reporting feature.

## License

By contributing, you agree that your contribution is licensed under the
repository's Apache-2.0 license. Include upstream attribution and license notices
for any third-party material; do not copy material whose redistribution terms
are unknown.
