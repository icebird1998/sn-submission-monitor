"""Springer Nature submissions, with verified-host browser authentication."""
import re
from urllib.parse import urlsplit

from .common import (browser_goto, challenge_check, click_verified, context_options,
                     credential_target, dismiss_cookies, fill_verified, launch_options,
                     network_options, norm, pagination_check, parse_html, poll,
                     provider_url, require_origin, same_location)

SN_BASE = "https://submission.springernature.com"
SN_HOST = "submission.springernature.com"
SN_LOGIN_HOSTS = {"idp.springernature.com", "idp-personal-authenticator.springernature.com"}


def _data(node, *names):
    return next((child for child in node.find_all()
                 if child.attrs.get("data-test") in names or child.attrs.get("data-testid") in names), None)


def _status(card):
    explicit = _data(card, "submission-status", "current-status", "submission-current-status", "status")
    lines = explicit.lines() if explicit is not None else card.lines()
    if explicit is None:
        index = next((i for i, line in enumerate(lines)
                      if "view submission details" in line.lower()), None)
        if index is None:
            raise RuntimeError("SN submission card has no explicit status or details/status boundary")
        boundary = lines[index]
        tail = re.split(r"view submission details", boundary, flags=re.I)[-1].strip()
        lines = ([tail] if tail else []) + lines[index + 1:]
    skip_date = False
    date = (r"(?:\d{1,4}[ /.,-]){1,3}\d{1,4}|"
            r"\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4}")
    for line in lines:
        metadata = re.search(r"\blast\s+updat(?:e|ed)\b", line, re.I)
        if metadata:
            line = line[:metadata.start()].strip(" :|-–")
            skip_date = True
        line = re.sub(r"^(?:current\s+)?status\s*:\s*", "", line, flags=re.I)
        if not line or re.fullmatch(r"(?:current\s+)?status|view submission details", line, re.I):
            continue
        if re.fullmatch(date, line) or (skip_date and re.match(r"^\d{1,2}\s+[A-Za-z]+\s+\d{4}(?:\b|$)", line)):
            skip_date = False
            continue
        return norm(line)
    raise RuntimeError("SN submission status could not be parsed (Last update is not a status)")


def parse_sn_cards(source):
    root = parse_html(source)
    challenge_check(source, "SN")
    cards = [node for node in root.find_all("li")
             if "submission-item" in (node.attrs.get("class") or "").split()]
    if not cards:
        if re.search(r"\byou have no (?:active |current )?submissions\b|"
                     r"\byou (?:do not|don.t) have any (?:active |current )?submissions\b|"
                     r"\bno (?:active |current )?submissions(?: found| yet| available)?\b|"
                     r"\byou (?:have not|haven.t) submitted\b", root.text(), re.I):
            pagination_check(root, 0)
            return {}
        raise RuntimeError("SN submission list could not be parsed; zero cards is not proof of an empty account")
    result = {}
    for card in cards:
        link = _data(card, "submission-details-link")
        title = _data(card, "submission-title")
        journal = _data(card, "journal-title")
        version = _data(card, "submission-version")
        if link is None or title is None or journal is None or not title.text() or not journal.text():
            raise RuntimeError("SN malformed submission card: missing link/title/journal")
        url = provider_url(link.attrs.get("href", ""), SN_BASE, "SN")
        location = urlsplit(url)
        match = re.fullmatch(r"/(?:submission-details|submissions)/([^/]+)/?", location.path)
        if match is None or location.query or location.fragment:
            raise RuntimeError("SN unexpected submission details link")
        uuid = match.group(1)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", uuid):
            raise RuntimeError("SN malformed submission identifier")
        status = _status(card)
        item = {"uuid": uuid, "title": title.text(), "journal": journal.text(),
                "version": version.text() if version else "", "status": status,
                "url": url}
        item["signature"] = norm(status) + "||" + norm(item["version"])
        if uuid in result:
            raise RuntimeError("SN duplicate submission identifier")
        result[uuid] = item
    pagination_check(root, len(result))
    return result


def sn_login(page, app):
    _, timeout = network_options(app)
    timeout_ms = timeout * 1000
    cfg = app.cfg["sn"]
    target = credential_target(page, "#login-email", SN_LOGIN_HOSTS, timeout_ms, "SN")
    dismiss_cookies(target)
    fill_verified(target, "#login-email", cfg["email"], SN_LOGIN_HOSTS, "SN")
    dismiss_cookies(target)
    click_verified(target, "#email-submit", SN_LOGIN_HOSTS, "SN")
    selector = 'input[type="password"]'
    target = credential_target(page, selector, SN_LOGIN_HOSTS, timeout_ms, "SN")
    dismiss_cookies(target)
    fill_verified(target, selector, cfg["password"], SN_LOGIN_HOSTS, "SN")
    dismiss_cookies(target)
    for selector in ('button:text-is("Log in")', 'button:text-is("Login")',
                     'button:text-is("Sign in")', 'button[type="submit"]',
                     'input[type="submit"]'):
        if target.locator(selector).first.is_visible():
            click_verified(target, selector, SN_LOGIN_HOSTS, "SN")
            break
    else:
        raise RuntimeError("SN verified password form has no supported login button")


def _watch_ids(watch):
    if watch is None:
        return None
    if isinstance(watch, str):
        watch = [watch]
    result = set()
    for value in watch:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("SN watch must contain submission IDs or HTTPS details URLs")
        if "://" in value:
            url = provider_url(value, SN_BASE, "SN")
            location = urlsplit(url)
            if location.query or location.fragment:
                raise ValueError("SN watch URL must not contain session/query parameters")
            value = location.path.rstrip("/").split("/")[-1]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("Invalid SN submission ID")
        result.add(value)
    if not result:
        raise ValueError("SN watch list must not be empty")
    return result


def sn_run(app, check_only_uuids=None):
    """Return all cards or the requested submission IDs; malformed lists raise."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("SN requires Playwright and its Chromium browser") from None
    only = _watch_ids(check_only_uuids)
    _, timeout = network_options(app)
    timeout_ms = timeout * 1000
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options(app))
        try:
            ctx = browser.new_context(**context_options(app.storage_file))
            ctx.set_default_timeout(timeout_ms)
            page = ctx.new_page()
            browser_goto(page, SN_BASE + "/your-submissions", app, "SN")
            def destination():
                if same_location(page.url, SN_HOST, "/your-submissions"):
                    return "list"
                try:
                    require_origin(page.url, SN_LOGIN_HOSTS, "SN")
                    return "login"
                except RuntimeError:
                    return None
            where = poll(page, destination, timeout_ms,
                         "SN did not reach its submission list or verified login host")
            if where == "login":
                app.log("SN session expired; signing in")
                sn_login(page, app)
                poll(page, lambda: True if same_location(page.url, SN_HOST, "/your-submissions") else None,
                     timeout_ms, "SN login did not reach the submission list; complete CAPTCHA/MFA manually if required")
            last_error = [None]
            def read_cards():
                if not same_location(page.url, SN_HOST, "/your-submissions"):
                    return None
                dismiss_cookies(page)
                try:
                    return parse_sn_cards(page.content())
                except RuntimeError as exc:
                    if "Pagination" in str(exc) or "truncated" in str(exc) or "manual" in str(exc):
                        raise
                    last_error[0] = str(exc)
                    return None
            try:
                result = poll(page, read_cards, timeout_ms,
                              "SN list parsing timed out; refusing an empty snapshot")
            except RuntimeError:
                if last_error[0]:
                    raise RuntimeError(last_error[0]) from None
                raise
            if only is not None:
                missing = only - result.keys()
                if missing:
                    raise RuntimeError("SN requested submissions not found: %s" % ", ".join(sorted(missing)))
                result = {key: item for key, item in result.items() if key in only}
            app.save_session(ctx, app.storage_file)
            return result
        finally:
            browser.close()
