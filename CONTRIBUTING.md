# Contributing / 贡献指南

Thank you for helping improve the monitor. Start with [README.md](README.md) / [README.en.md](README.en.md), and read [SECURITY.md](SECURITY.md) before sharing diagnostic information.

欢迎反馈与贡献。请先阅读使用说明和安全说明；**不要提供真实账号、密码、SMTP 授权码、Cookie、会话、原始状态、日志或稿件信息**。

## Report a bug or request a feature

Use the GitHub issue templates. Include:

- Version or commit, OS, Python version, and the relevant provider (`sn`, `mts`, or `em`).
- The CLI mode used and expected versus actual behavior. Replace personal paths with placeholders.
- A short, manually reviewed error excerpt, if essential, with all identifying details removed. Do not attach raw logs, config, state, browser storage, screenshots, or trace archives.
- A synthetic offline reproduction where possible. A publisher hostname without user-specific paths may help identify a platform; no account access is needed.

Use a private vulnerability report instead of a public issue for security problems. Do not send credentials to maintainers, even if requested in a comment. ScholarOne is not supported; eJournalPress MTS is a separate platform. New journal/SSO support requires evidence from safely constructed fixtures, not a claim of universal compatibility.

## Development setup

Python **3.10+** is required. Use a separate virtual environment and a disposable private runtime directory; do not point development or tests at your normal config/state.

```bash
git clone https://github.com/icebird1998/sn-submission-monitor.git
cd sn-submission-monitor
python -m venv .venv
# POSIX: source .venv/bin/activate
# Windows cmd: .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
python -m unittest discover -s tests -v
```

Dependency installation downloads packages, but **unit tests must not contact publisher login pages, authenticate real accounts, or send real email**. Playwright's Python package is installed; its browser is unnecessary for ordinary offline tests. The configured CI matrix runs unit tests on Windows and Ubuntu with Python 3.10 and 3.12. A configured matrix is not a claim that every platform/provider has already passed live validation.

### Optional browser fixture tests

Only tests using synthetic local pages may opt in with `RUN_BROWSER_FIXTURE_TESTS=1`. If such tests are present, install Chromium first:

```bash
python -m playwright install chromium
# POSIX
RUN_BROWSER_FIXTURE_TESTS=1 python -m unittest discover -s tests -v
```

```bat
rem Windows cmd, after installing Chromium
set RUN_BROWSER_FIXTURE_TESTS=1
python -m unittest discover -s tests -v
set RUN_BROWSER_FIXTURE_TESTS=
```

On Linux, local Chromium may need system libraries; CI uses Playwright's `--with-deps` only when browser fixture tests are explicitly enabled. The workflow defaults the flag to `0`; maintainers can opt in using the repository Actions variable `RUN_BROWSER_FIXTURE_TESTS=1`. Never use that flag to enable live account tests.

## Test and fixture requirements

- Mock HTTP, browser, SMTP, notification, time, and filesystem boundaries as appropriate. Use temporary directories. Make fake credentials obviously non-authenticating and avoid real manuscript identifiers, emails, links, or copied session exports.
- Prefer minimal invented HTML/JSON fixtures over copied account pages. Synthetic files under `tests/fixtures/` may use otherwise-ignored runtime filenames; the ignore exceptions are **not permission to commit real runtime data**.
- Keep parsing tests separate from authentication and orchestration. Cover changed status, first-run silence, newly discovered manuscripts, partial provider failures/recovery, per-channel retries, crash-safe persistence/locking, and CLI failure exits when relevant.
- Test local-only modes without making network calls. In particular, `--check-config`, `--doctor`, and `--show` must not log in; `--list` must not alter baselines or send notifications; `--test-email` must not alter monitoring state; reset must require `--yes` and preserve queued notifications.
- Browser tests, if added, must operate only on synthetic local fixtures and skip unless explicitly enabled. Ordinary CI does not need a browser download.
- Do not run task installers/uninstallers, change real tasks, collect secrets, or perform a live check as part of a test suite. Task-script tests should parse or mock operations rather than register tasks.

## Changes and pull requests

1. Keep changes focused and explain user-visible behavior. Include regression tests for fixes and update both READMEs / changelog when the CLI, config, or behavior changes.
2. Preserve `monitor.py` compatibility and the module/console CLI contract unless a deliberate migration is documented. Target Python 3.10 syntax and later.
3. Respect the minimum five-minute polling interval and publisher terms. Do not add anti-bot bypasses, credential collection, or account-sharing mechanisms.
4. Preserve atomic state handling, cross-process locking, and durable per-channel retries. Delivery is at least once, not exactly once; do not hide that distinction.
5. Keep config/runtime data untracked and avoid logging secrets or unpublished metadata. Review changed files before committing; ignore rules do not remove tracked files.
6. For Windows scripts, preserve logged-in-user-only operation, no default elevation, explicit task-replacement consent, confirmed uninstall, bounded execution, and no deletion of private data. Existing `NCommsStatusMonitor` tasks must remain untouched by default.
7. Describe checks actually run and checks not run. Do not claim universal journal/SSO compatibility or live-account coverage based on fixture tests.

By contributing, you agree that your contribution is provided under the repository's [MIT license](LICENSE). Please be respectful and do not include someone else's unpublished material.
