# Open-Source Hardening Implementation Plan

> **For Hermes:** Execute task-by-task with TDD for behavior changes and run the full pre-commit verification gate before publishing.

**Goal:** Remove the code-level privacy and security blockers found in the final pre-open-source audit while preserving application behavior.

**Architecture:** Validate chunk-upload identifiers at the filesystem boundary, render Markdown through locally vendored and sanitized libraries, remove browser-time third-party asset calls, parameterize sample deployment endpoints, and strengthen repository/container ignore rules. Keep the existing private GitHub repository untouched as an archive; prepare a history-free local export only after the source branch passes all checks.

**Tech Stack:** Python 3.12, FastAPI, pytest, vanilla JavaScript, Docker Compose, Gitleaks.

---

### Task 1: Reject unsafe upload IDs

**Files:**
- Modify: `transcriber/tests/test_api.py`
- Modify: `transcriber/app/main.py`

1. Add a focused test proving traversal-like and malformed upload IDs receive HTTP 400 and never resolve outside the upload root.
2. Run the focused test and verify RED.
3. Add centralized 32-character lowercase hexadecimal validation at `_upload_dir`.
4. Run the focused test and full API suite; verify GREEN.

### Task 2: Make Markdown rendering local and safe

**Files:**
- Create: `transcriber/static/vendor/marked.min.js`
- Create: `transcriber/static/vendor/purify.min.js`
- Create: vendor license files under `transcriber/static/vendor/`
- Modify: `transcriber/static/index.html`
- Modify: `transcriber/static/settings.html`
- Modify: `transcriber/tests/test_api.py`

1. Add a test asserting frontend HTML has no remote script/font dependencies and sanitizes rendered Markdown.
2. Run it and verify RED.
3. Vendor pinned Marked and DOMPurify distributions with their licenses.
4. Replace Google Fonts with system font stacks and the CDN script with local scripts.
5. Sanitize `marked.parse` output before assigning to `innerHTML`.
6. Run the focused and full test suites; verify GREEN.

### Task 3: Remove unlicensed sample deployment material

**Files:**
- Delete: `transcriber/sample-knowledge/modal-whisper-server/`

1. Confirm the directory is unused by application or tests.
2. Confirm no upstream license or redistribution grant is present.
3. Remove the directory rather than guessing at redistribution rights.
4. Re-run tests and source scans.

### Task 4: Harden repository and container hygiene

**Files:**
- Modify: `.gitignore`
- Create: `transcriber/.dockerignore`
- Modify: `transcriber/docker-compose.yml`
- Modify: `README.md`
- Modify: `docs/DEPLOYMENT.md`

1. Ignore nested environment files, local virtual environments, caches, logs, OS metadata, and model artifacts while preserving example files.
2. Exclude secrets, Git metadata, caches, tests, and local artifacts from Docker build context.
3. Make Compose read the documented repository-root `.env` path.
4. Update configuration/deployment documentation.
5. Validate ignore behavior and `docker compose config`.

### Task 5: Verify and prepare clean export

1. Run pytest, Ruff, compileall, shell syntax, Compose validation, Gitleaks tree scan, custom private-IP/domain scan, and `git diff --check`.
2. Dispatch an independent reviewer with the final diff and fail closed on security or logic findings.
3. Commit verified changes on `fix/open-source-hardening`.
4. Export the verified tree into a separate local history-free repository using safe noreply metadata.
5. Re-run source and Git-history scans on the export. Do not publish or change GitHub visibility in this task.
