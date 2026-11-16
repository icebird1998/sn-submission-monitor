# sn-submission-monitor

[简体中文](README.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md)

Periodically check your own manuscript submissions and notify you by email or Windows toast when an observed status changes. The first successful check creates a baseline silently; it does not send historical status notifications.

**Python 3.10+. Unofficial, not a real-time push service, and no ScholarOne support.** Check your account permissions, the journal's terms, and your institution's automation policy before use.

## Scope and limitations

| Platform setting | Recognize it by | Implementation |
| --- | --- | --- |
| `sn` | `submission.springernature.com` | Springer Nature submission platform; Playwright Chromium |
| `mts` | For example, `mts-ncomms.nature.com` | eJournalPress MTS; `requests`, no browser download needed |
| `em` | `editorialmanager.com/<journal-code>` | Editorial Manager; Playwright Chromium; optional Elsevier SSO |

- **eJournalPress MTS is not ScholarOne.** Check the actual submission URL rather than inferring support from a journal name.
- Known live-account validation is limited to **SN Communications and Elsevier EM**. Offline sample coverage is not proof of compatibility with every journal. Custom pages, university SSO, MFA, and other login flows may not work.
- Checks and notifications are not guaranteed while the computer is asleep, off, offline, the user is logged out, or a session has expired. A later check only sees the then-visible status; **not every intermediate state can be captured**.
- SMTP acceptance is not inbox delivery. Windows settings may suppress toast notifications. Do not rely on this tool for revision deadlines or other critical actions.

## Quick start

### Windows

Use a normal terminal; administrator access is not required:

```bat
git clone https://github.com/icebird1998/sn-submission-monitor.git
cd sn-submission-monitor
setup_windows.bat
```

`setup_windows.bat` creates or reuses the repository `.venv`, installs `requirements.txt`, and installs the editable package. It does not create config, log in, send mail, register tasks, or download Chromium. Without the Windows Python launcher, supply a full Python 3.10+ executable path:

```bat
setup_windows.bat "C:\Path\Python312\python.exe"
```

Configure and validate locally:

```bat
.venv\Scripts\python.exe -m submission_monitor --init
.venv\Scripts\python.exe -m submission_monitor --check-config
```

`--init` is interactive and **refuses to overwrite existing config**. MTS-only use can skip the browser download; SN or EM additionally needs:

```bat
.venv\Scripts\python.exe -m playwright install chromium
```

After installing the required browser (not needed for MTS), run local diagnostics. Only after they pass and permissions are checked, explicitly run a live check:

```bat
.venv\Scripts\python.exe -m submission_monitor --doctor
.venv\Scripts\python.exe -m submission_monitor --once
.venv\Scripts\python.exe -m submission_monitor --show
```

### Linux / macOS, or manual installation

```bash
git clone https://github.com/icebird1998/sn-submission-monitor.git
cd sn-submission-monitor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# Optional: install the package and sn-submission-monitor console command
python -m pip install -e . --no-deps
# SN / EM only; omit for MTS-only use
python -m playwright install chromium
python -m submission_monitor --init
python -m submission_monitor --check-config
python -m submission_monitor --doctor
```

Alternatively, `python -m pip install .` installs the package and its `requests` / `playwright` dependencies, but not a browser. Linux Chromium may need system libraries; follow Playwright's dependency guidance. `python monitor.py` remains a compatible wrapper, `python -m submission_monitor` uses the same CLI, and installing the package also exposes `sn-submission-monitor`.

## Configuration and private data

When running from the repository or an editable installation, the default is **`.local/config.json` in the repository**. If it is absent, an existing root-level `config.json` is accepted for compatibility. After a non-editable `pip install .`, the console command defaults to **`.local/config.json` in the current working directory**, not `site-packages`. To avoid dependence on the launch directory, prefer an absolute `--config` path:

```bash
python -m submission_monitor --config /absolute/private/path/config.json --init
python -m submission_monitor --config /absolute/private/path/config.json --check-config
```

Runtime state, queued notifications, sessions, locks, and logs live in the **config directory**, or the directory selected by top-level `runtime_dir`. Prefer an absolute private path or the ignored `.local/`; keep runtime data outside version control. If retaining legacy root-level config, migrate to a private directory and update scheduler paths deliberately. The scripts never relocate or delete existing data for you.

`config.example.json` contains placeholders, not usable credentials. Replace them after copying; placeholder passwords deliberately fail local validation. This minimal SN example also requires a real password supplied privately by the wizard or environment:

```json
{
  "platform": "sn",
  "sn": {
    "email": "you@example.com",
    "password": "",
    "watch": "all"
  },
  "notify": {
    "toast": true
  },
  "fail_alert_after": 6,
  "interval_minutes": 15,
  "network": {
    "attempts": 3,
    "timeout_seconds": 45
  }
}
```

### Platforms and selectors

`platform` accepts `"sn"`, `"mts"`, `"em"`, `"both"` (legacy alias for SN + MTS), `"all"` (all three), or a list such as `["sn", "em"]`. Configure accounts only for the providers you enable.

- **SN:** `email`, `password`; `watch: "all"` or a list of manuscript UUIDs / full manuscript URLs.
- **MTS:** `base`, `email`, `password`; use `watch: "all"` for all submissions, or `watch: "single"` with a numeric `ms_id`. The ID below is fictional:

  ```json
  {
    "platform": "mts",
    "mts": {
      "base": "https://mts-ncomms.nature.com",
      "email": "you@example.com",
      "password": "",
      "watch": "single",
      "ms_id": 123456
    }
  }
  ```

- **EM:** `base` is the journal URL, for example `https://www.editorialmanager.com/JOURNAL_CODE` (replace the code); also configure `email`, `password`, and optionally `label` for notifications. Add `"sso": "elsevier"` only for a journal using that Elsevier login flow; this is not generic university SSO support. Choose one of these `em.watch` values:

  ```json
  "all"
  ```

  ```json
  ["EM-DEMO-26-00001"]
  ```

  ```json
  [{"title": "Example manuscript title", "submitted": "Nov 1, 2026"}]
  ```

  The last form can match a manuscript without an assigned number. Match the displayed title and submission date. A base manuscript-number selector follows its `R1` / `R2` revisions; explicitly selecting a number with an `R1` suffix matches that revision only. These fragments are selector values, not complete config files.

### Notifications

`notify.toast` is a boolean; `--no-toast` disables desktop notifications for that invocation. Other operating systems do not depend on this channel. Email is optional: omit `notify.email`, or add it within `notify`:

```json
{
  "toast": true,
  "email": {
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "smtp_user": "you@example.com",
    "smtp_auth_code": "",
    "to": ["you@example.com"],
    "smtp_security": "ssl"
  }
}
```

This is illustrative, not a working SMTP configuration. `to` may be one address string or a list. The port defaults to `465`; `smtp_security` is `ssl` or `starttls` (usually with an explicit port `587`, according to your provider). Set optional `enabled: false` to disable email; when omitted, enabling is determined by a configured auth code. Explicit enabling requires complete settings. Follow your provider's guidance for a dedicated SMTP app password and never post it in an issue.

Environment variables override the corresponding secret fields:

| Variable | Overrides |
| --- | --- |
| `SNM_SN_PASSWORD` | SN password |
| `SNM_MTS_PASSWORD` | MTS password |
| `SNM_EM_PASSWORD` | EM password |
| `SNM_SMTP_AUTH_CODE` | SMTP app password / auth code |

Environment variables do not encrypt local data. Do not put their values in Git, command-line arguments, screenshots, or support requests. A scheduled task needs the variables available to its own user environment; temporary variables in a terminal are not a reliable task configuration.

## CLI reference

Here `python` means the project's virtual-environment interpreter. Every command accepts `--config PATH`.

| Command | Behavior |
| --- | --- |
| `python -m submission_monitor --init` | Interactive wizard; refuses to overwrite existing config |
| `python -m submission_monitor --check-config` | Local validation only; no login or email |
| `python -m submission_monitor --doctor` | Local config/runtime/dependency health; no login, credential dump, or raw log dump; invalid config, missing required dependencies, or a missing browser required by selected providers returns nonzero |
| `python -m submission_monitor --once` | One live check; also the default without a mode flag; nonzero exit on errors |
| `python -m submission_monitor --loop 15` | Live checks every 15 minutes; minimum 5 minutes; stop with `Ctrl+C` |
| `python -m submission_monitor --show` | Read cached status and pending outbox locally; no live query |
| `python -m submission_monitor --list` | Live listing within the configured selection (`watch: "all"` to list all); no baseline changes or notifications; may log in / refresh local sessions |
| `python -m submission_monitor --test-email` | Explicit, clearly labelled test mail; no monitoring state changes; success means SMTP acceptance only |
| `python -m submission_monitor --once --no-toast` | Suppress desktop notifications for this run |
| `python -m submission_monitor --once --headed` | Show the browser for browser-based providers; does not bypass authentication restrictions |
| `python -m submission_monitor --reset --yes` | Clear monitoring baselines with required explicit confirmation; **preserves credentials and queued notifications** |

### Baselines, delivery, and failures

- The first successful check establishes a silent baseline. After that, newly discovered manuscripts generate notifications too.
- State writes are atomic, with a cross-process lock for a shared runtime directory. Do not run older and newer versions against the same data simultaneously.
- A durable outbox tracks retries separately for each notification channel. One channel's success is independent of another's failure. Delivery is **at least once**: a crash between sending and saving its acknowledgement can cause a rare duplicate. Partial SMTP acceptance with multiple recipients can also cause already-accepted recipients to receive duplicates on retry.
- Temporarily setting `notify.email.enabled: false` pauses existing queued mail rather than dropping it. Pending items remain, and the run reports incomplete delivery with a nonzero status (exit code `3`); re-enabling resumes retries. Changing the account or recipients cancels the old queue for privacy, rather than sending old-account records to a new destination.
- Narrowing `watch` preserves the baseline for retained manuscripts instead of silently re-baselining them. A queued event entirely within the remaining scope is retained; an old multi-manuscript event containing an excluded item is cancelled as a whole.
- Provider failure counters are independent. By default, `6` consecutive failures trigger an alert, followed by a recovery notice when checks recover. An alert cannot be guaranteed if the notification channels are also unavailable; periodically inspect `--doctor` / `--show`.
- Defaults: `15` minutes between checks, `3` network attempts, `45` seconds timeout. Do not poll below 5 minutes or bypass CAPTCHA, MFA, access controls, or site terms.
- `--reset --yes` does not cancel queued notifications: a later run may still deliver them. Removing a scheduled task does not delete them either.

## Windows scheduled task

Complete local checks and one deliberate live check first. Then explicitly run, in a normal terminal:

```bat
install_task.bat
```

The default task is **`SNSubmissionMonitor`**, repeating every **15 minutes**, using the repository `.venv\Scripts\pythonw.exe` (or its sibling `python.exe` if unavailable). It does not guess a Python from PATH or Conda. The batch wrapper forwards parameters to PowerShell; direct use is also available:

```powershell
.\scripts\install_task.ps1 -TaskName SNSubmissionMonitor `
  -Config "C:\Users\YOU\AppData\Local\sn-submission-monitor\config.json" `
  -PythonExecutable "C:\path\sn-submission-monitor\.venv\Scripts\pythonw.exe" `
  -IntervalMinutes 15
```

- `TaskName`, `Config`, `PythonExecutable`, and `IntervalMinutes` are configurable; the interval must be 5–1440 minutes. Relative file paths resolve against the repository.
- Runs only as the **currently logged-in user**, with limited privileges, no elevation or stored Windows password. Settings use `MultipleInstances=IgnoreNew`, `StartWhenAvailable`, no wake-from-sleep request, and an execution cap of `min(10, interval−1)` minutes. A slow check can be terminated at that cap; review task results for larger accounts.
- Installation queries the task and runs local `--check-config` / `--doctor`; it does not log in or send test email. Once registered, the first scheduled trigger is about a minute later.
- An existing task is summarized and rejected unless **`-Force`** explicitly authorizes replacement. `-WhatIf` previews registration without changing tasks (local inspection and validation still happen).
- If execution policy blocks the script, review it and follow your organization's policy. Do not blindly elevate or permanently weaken machine-wide policy.
- Moving the repository, virtual environment, or config requires reviewing task paths. A pip installation alone never registers a task.

Uninstallation inspects the named task and asks you to type its exact name. It removes only the registration, never runtime data:

```bat
uninstall_task.bat
uninstall_task.bat -TaskName MySubmissionMonitor
```

Unattended removal requires explicit `-Force`. **An existing local `NCommsStatusMonitor` is left unchanged by default.** Inspect legacy tasks separately to avoid duplicate scheduling. To remove that legacy task deliberately, use `uninstall_task.bat -TaskName NCommsStatusMonitor` and confirm. Do not enable multiple loops or schedulers for the same monitor.

### Linux / macOS scheduling

Use `--loop 15`, or configure your own cron/service with absolute interpreter and config paths. For example, after creating and protecting the private directory:

```cron
*/15 * * * * /absolute/path/sn-submission-monitor/.venv/bin/python /absolute/path/sn-submission-monitor/monitor.py --config /home/YOU/.local/share/sn-submission-monitor/config.json --once >> /home/YOU/.local/share/sn-submission-monitor/cron.log 2>&1
```

Service environments differ from terminal environments. This example is not a claim of completed integration testing on every OS; check permissions, dependencies, and scheduler results on your system.

## Troubleshooting and participation

Start with local `--check-config`, `--doctor`, and cached `--show`; run live `--list` or `--test-email` only when intended. Publisher page changes, SSO, CAPTCHA, network restrictions, and browser dependencies can break checks. Do not upload config, state, sessions, or raw logs: even with passwords removed, they may expose cookies, manuscript titles, email addresses, or identifying links.

See [CONTRIBUTING.md](CONTRIBUTING.md) for offline tests and reports, and [SECURITY.md](SECURITY.md) for private-data handling. Licensed under [MIT](LICENSE), without guarantees of availability, publication outcomes, or notification delivery.
