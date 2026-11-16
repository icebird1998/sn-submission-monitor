# Security and private data / 安全与隐私

This project accesses unpublished manuscript information and authenticates to third-party websites. It is unofficial software, not a credential vault. Only use accounts and submission records you are permitted to access, and follow publisher terms and institutional policy. Do not bypass CAPTCHA, MFA, SSO controls, or rate limits.

本项目会访问未公开稿件信息并登录第三方网站，不是凭据保险箱。仅使用有权访问的账号与稿件，遵守期刊条款和机构政策，不绕过验证码、MFA、统一认证或频率限制。

## Reporting a vulnerability privately

- **Do not open a public issue with an exploitable vulnerability, account data, or credentials.** Use GitHub's **Security → Report a vulnerability** for this repository if private reporting is available.
- If private reporting is unavailable, do not publish technical exploit details or secrets. A public issue may ask the maintainer to enable a private channel, without describing the vulnerability or identifying an account. No unverified contact address is advertised here.
- Provide the affected version/commit, relevant component, impact, and minimal synthetic reproduction. A private report still does not need real passwords, SMTP auth codes, cookies, browser profiles, or manuscript records.
- There is no guaranteed response time or support SLA. Security fixes are targeted at the current development/release line; older versions should be updated.

发现漏洞时不要把可利用细节或账号资料放入公开 issue。优先使用仓库 Security 页面中的私密漏洞报告；如果未启用，可仅请求维护者开启私密联系渠道，不公开漏洞细节。只提供版本、影响和虚构最小复现，不需要提供真实秘密值。

## What must remain private

Treat all of the following as sensitive, even if a file appears to contain no password:

- Config files, environment variables, account emails/passwords, SMTP app passwords, and recipient lists.
- Playwright storage state, cookies, browser sessions/profiles, authentication headers, local storage, and redirect URLs with authentication parameters.
- State snapshots, durable outbox entries, logs, trace archives, screenshots, and exported HTML. These may contain unpublished titles, manuscript numbers, author names, status history, links, and identifiers.
- Scheduled-task exports or arbitrary command-line arguments, which may expose local paths or secrets from older setups.

不要公开配置、环境变量值、会话、Cookie、状态、待重试通知、日志、截图、网页导出或旧计划任务导出；它们可能包含稿件隐私和认证信息。

## Local safeguards

1. Prefer a private OS-user directory or the ignored `.local/` for config and runtime files. `runtime_dir` can separate runtime data from config; protect both. Use restrictive file/directory permissions and full-disk encryption where appropriate. Avoid shared, public, or automatically synchronized folders.
2. Environment variables `SNM_SN_PASSWORD`, `SNM_MTS_PASSWORD`, `SNM_EM_PASSWORD`, and `SNM_SMTP_AUTH_CODE` override corresponding config values, but are **not encryption**. Do not put their values in command-line arguments, shell history, examples, or `.env` files committed to Git. There is no promise of an OS credential-store integration.
3. Browser session files can grant account access without entering a password again. Protect them at least as carefully as the password itself.
4. Use only expected publisher endpoints and a trusted SMTP server. Prefer the supported encrypted SMTP modes (`ssl` / `starttls`); do not disable certificate verification to work around connection errors. Configured `base` endpoints are destinations to which the tool may send credentials.
5. Use a dedicated SMTP app password where supported, and the least-privileged account appropriate to the task. Do not request or share other users' credentials to debug compatibility.
6. `.gitignore` is only a safety net: it does not remove files already tracked, encrypt anything, prevent backups, or sanitize Git history. Review your staged changes before publishing. Public example and fixture exceptions must contain fictional, non-authenticating content only.
7. `--check-config` and `--doctor` are local-only diagnostics, not penetration tests or proof of publisher compatibility. `--list`, `--once`, `--loop`, and `--test-email` are deliberate network operations.
8. The Windows task scripts run with the logged-in user's limited privileges. Do not elevate by default or weaken machine-wide execution policy just to install a task. Review script source before running it.

## If a secret or manuscript record leaks

Stop scheduled checks, revoke affected app passwords, rotate exposed passwords, and invalidate sessions through the relevant provider as appropriate. Remove public copies and contact the hosting service if needed. Removing a file from the latest commit does not remove it from history, forks, caches, or backups; remediation may require history cleanup and continued credential revocation.

`--reset --yes` is **not** a privacy wipe: it preserves credentials and queued notifications. Uninstalling a task only removes the task registration. After stopping every monitor process/task, handle local config, runtime directories, browser sessions, backups, and environment variables deliberately according to your retention requirements. A sent email cannot be recalled by resetting state.

发生泄露后应停止监控、撤销授权码、修改密码并按服务商方式失效会话。删掉当前文件不代表已清除历史记录或备份。`--reset --yes` 和任务卸载都不是隐私擦除功能；清理应在停止所有进程后谨慎进行。

## Reliability is not a security guarantee

Atomic state writes, locking, failure alerts, and durable notification retries reduce some failures; they do not guarantee availability or delivery. Retries are at least once and can duplicate messages after a crash or partial multi-recipient SMTP acceptance. Disabling email pauses existing queued mail, not erases it; account/recipient changes cancel old queued events for privacy. A sleeping/offline/logged-out machine cannot guarantee checks. SMTP acceptance is not inbox delivery, and no monitor can reconstruct every unseen intermediate state. Keep independent reminders for deadlines.
