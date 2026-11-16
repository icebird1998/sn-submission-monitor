# Changelog

User-facing changes are recorded here. An unreleased entry is not a published release or proof of completed live-platform testing.

## [Unreleased] — v0.2 scaffolding

### CLI and configuration

- Python 3.10+ package layout with `python -m submission_monitor`, optional installed `sn-submission-monitor` command, and the backward-compatible `monitor.py` wrapper.
- Interactive, non-overwriting `--init`; local-only `--check-config` and `--doctor`; cached status/outbox via `--show`; live, non-notifying `--list`; optional `--headed` and `--no-toast`.
- One-shot operation is the default and reports errors through a nonzero exit status. `--loop N` enforces a minimum five-minute interval. `--test-email` is explicitly labelled and does not change monitoring state.
- Default private `.local/config.json` with existing root-level `config.json` compatibility and explicit `--config` / `runtime_dir` support. Environment overrides remain available for provider passwords and SMTP auth codes.
- Explicit provider selectors for SN, eJournalPress MTS, and Editorial Manager, including EM manuscript-number or title/submission-date selectors. MTS is not ScholarOne.
- SMTP SSL / STARTTLS settings, optional email enablement, address-list recipients, and configurable network retries/timeouts.

### State and reliability

- Silent initial baselines, notifications for manuscripts discovered after the baseline, atomic state persistence, and cross-process locking.
- Durable per-channel notification retries with at-least-once delivery. Crashes around delivery acknowledgement or partial multi-recipient SMTP acceptance can cause duplicates.
- Disabled email pauses pending deliveries with a nonzero incomplete-delivery result; re-enabling resumes retries. Account/recipient changes cancel old queues for privacy. Narrowing watch scope preserves retained baselines and only keeps queued events wholly within that scope.
- Independent provider failure counters, failure alerts, and recovery notices.
- `--reset` requires `--yes`, clears baselines, and preserves queued notifications and credentials.

### User-facing release scaffolding

- Chinese and English setup/reference documentation, contribution guidance, private-data policy, and privacy-conscious issue templates.
- Explicit Windows dependency setup without automatic login, browser download, email, monitoring, or task registration.
- Configurable logged-in-user Windows tasks, preferring repository `.venv` Python, with a fifteen-minute default interval, overlap prevention, start-when-available, and bounded execution.
- Existing task replacement requires `-Force`; removal requires inspection/confirmation or explicit `-Force` and never deletes runtime data. Legacy `NCommsStatusMonitor` tasks remain untouched by default.
- Offline unit-test workflow configured for Windows / Ubuntu and Python 3.10 / 3.12; browser fixture downloads are opt-in via `RUN_BROWSER_FIXTURE_TESTS=1`.
- Expanded private-runtime ignore rules and consistent text line-ending attributes.

### Limits made explicit

- Known live-account validation is limited to SN Communications and Elsevier EM; no claim of universal journal/SSO coverage or ScholarOne support.
- No guaranteed checks while asleep/offline/logged out, no guarantee of all intermediate states, and no equivalence between SMTP acceptance and inbox delivery.
- No remote publication or new release tag is implied by this entry.
