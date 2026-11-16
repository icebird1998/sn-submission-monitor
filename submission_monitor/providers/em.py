"""Editorial Manager lists and native/Elsevier SSO login.

Pagination is deliberately fail-closed: a paginated folder is not a complete
snapshot and must never be silently accepted as one.
"""
import json
import re
from urllib.parse import quote, urlsplit

from .common import (browser_goto, challenge_check, click_verified, context_options,
                     credential_target, direct_cells, dismiss_cookies, fill_verified,
                     find_frame, frame_race, launch_options, network_options, norm, pagination_check,
                     parse_html, poll, provider_url, require_origin, table_rows)

ELSEVIER_HOSTS = {"id.elsevier.com"}


def _header(text):
    return norm(text).rstrip(":*").strip().lower()


def parse_em_rows(source):
    """Parse direct table rows/cells, not nested layout-table descendants.

    Missing/malformed list tables raise, including a header without usable rows.
    An explicit empty-state message is the only valid empty table.
    """
    root = parse_html(source)
    challenge_check(source, "EM")
    candidates = []
    for table in root.find_all("table"):
        rows = table_rows(table)
        for hi, row in enumerate(rows):
            heads = [_header(c.text()) for c in direct_cells(row)]
            if "current status" in heads:
                candidates.append((rows, hi, heads))
                break
    if not candidates:
        if re.search(r"\bno (?:submissions|manuscripts|records|items)(?:\s+(?:found|available|to display))?\b", root.text(), re.I):
            pagination_check(root, 0)
            return []
        raise RuntimeError("EM manuscript table not found; list parsing failed (not an empty folder)")

    parsed = []
    for rows, hi, heads in candidates:
        if "title" not in heads:
            raise RuntimeError("EM manuscript table is missing the Title header")
        if heads.count("title") != 1 or heads.count("current status") != 1:
            raise RuntimeError("EM manuscript table has ambiguous headers")
        indexes = {name: heads.index(name) for name in heads if name}
        def field(cells, *names):
            index = next((indexes[n] for n in names if n in indexes), None)
            return cells[index].text() if index is not None and index < len(cells) else ""
        rows_here = []
        for row in rows[hi + 1:]:
            cells = direct_cells(row)
            if not cells or not any(c.text() for c in cells):
                continue
            texts = [_header(c.text()) for c in cells]
            if texts == heads:
                continue
            # Pager and explicit empty-state rows are not manuscripts.
            if len(cells) < len(heads):
                if re.search(r"\bno (?:submissions|manuscripts|records|items)\b", row.text(), re.I):
                    continue
                if any("colspan" in c.attrs for c in cells):
                    pagination_check(row)
                raise RuntimeError("EM malformed manuscript row (missing direct cells)")
            title = field(cells, "title")
            status = field(cells, "current status")
            if not title or not status:
                raise RuntimeError("EM malformed manuscript row: Title/Current Status is empty")
            rows_here.append({"number": field(cells, "manuscript number", "manuscript #"),
                              "title": title,
                              "submitted": field(cells, "initial date submitted", "initial date", "date submitted", "submitted"),
                              "status_date": field(cells, "status date", "date status changed"),
                              "status": status})
        parsed.extend(rows_here)
    if not parsed and not re.search(r"\bno (?:submissions|manuscripts|records|items)\b", root.text(), re.I):
        raise RuntimeError("EM table contains no parseable manuscripts; refusing an empty result")
    pagination_check(root, len(parsed))
    return parsed


def _number(value):
    return norm(value).upper()


def _root(number):
    return re.sub(r"R\d+$", "", _number(number))


def select_em_manuscripts(items, watch, previous=None):
    if watch == "all":
        return items
    if not isinstance(watch, list) or not watch:
        raise ValueError("em.watch must be all or a nonempty manuscript filter list")
    previous = previous or {}
    selected = {}
    for target in watch:
        matches = {}
        if isinstance(target, str) and target.strip():
            number = _number(target)
            is_revision = re.search(r"R\d+$", number) is not None
            for key, item in items.items():
                current = _number(item.get("number") or key)
                if current == number or (not is_revision and re.fullmatch(re.escape(number) + r"R\d+", current)):
                    matches[key] = item
            label = number
        elif (isinstance(target, dict) and set(target) == {"title", "submitted"}
              and all(isinstance(v, str) and v.strip() for v in target.values())):
            marker = json.dumps({k: norm(v) for k, v in target.items()},
                                ensure_ascii=False, sort_keys=True)
            bound = {_root(v["_watch_number"]) for v in previous.values()
                     if v.get("_watch_key") == marker and v.get("_watch_number")}
            if len(bound) > 1:
                raise RuntimeError("EM stored title/date binding is ambiguous; use a manuscript number")
            for key, item in items.items():
                current = _number(item.get("number") or "")
                root = _root(current)
                if ((bound and root in bound) or (not bound
                        and norm(item.get("title")) == norm(target["title"])
                        and norm(item.get("submitted")) == norm(target["submitted"]))):
                    matches[key] = dict(item, _watch_key=marker, _watch_number=root)
            roots = {v.get("_watch_number") or k for k, v in matches.items()}
            if len(roots) > 1:
                raise RuntimeError("EM title/date matches multiple manuscripts; use a manuscript number")
            label = target["title"]
        else:
            raise ValueError("em.watch entries must be numbers or title/submitted objects")
        if not matches:
            raise RuntimeError("EM requested manuscript not found: %s" % label)
        for key, item in matches.items():
            # A simultaneous numbered selector must not discard a title binding.
            if key not in selected or item.get("_watch_key"):
                selected[key] = item
    return selected


def _base(cfg):
    base = cfg["base"].rstrip("/")
    location = urlsplit(base)
    host = (location.hostname or "").lower()
    if host != "editorialmanager.com" and not host.endswith(".editorialmanager.com"):
        raise ValueError("EM base must be an Editorial Manager journal URL")
    require_origin(base, {host}, "EM")
    if location.query or location.fragment or not location.path.strip("/"):
        raise ValueError("EM base must identify a journal, without query/fragment")
    return base, location


def _menu_links(source, base):
    root = parse_html(source)
    links = {}
    for anchor in root.find_all("a"):
        href = anchor.attrs.get("href", "")
        if not re.search(r"(?:auth_|revision).*\.aspx?\b|(?:auth_|revision)", href, re.I):
            continue
        url = provider_url(href, base, "EM", journal_path=True)
        path = urlsplit(url).path
        if not path.lower().endswith((".asp", ".aspx")):
            raise RuntimeError("EM author folder link is not a supported ASP page")
        text = anchor.text()
        count_match = re.search(r"\(\s*(\d+)\s*\)\s*$", text)
        count = int(count_match.group(1)) if count_match else None
        if count == 0:
            continue
        if url in links and links[url]["count"] != count:
            raise RuntimeError("EM conflicting folder counts")
        links[url] = {"url": url, "count": count}
    if links:
        return list(links.values())
    text = root.text()
    counts = re.findall(r"(?:submissions?[^()]{0,90}|revisions?[^()]{0,90})\(\s*(\d+)\s*\)", text, re.I)
    if (counts and all(int(c) == 0 for c in counts)) or re.search(r"\bno (?:submissions|manuscripts)\b", text, re.I):
        return []
    return None


def _login(page, app, base, location, timeout_ms):
    cfg = app.cfg["em"]
    host = location.hostname
    menu_path = location.path.rstrip("/") + "/AuthorMainMenu.aspx"
    if cfg.get("sso") == "elsevier":
        app.log("EM signing in using Elsevier SSO")
        sso_url = ("https://id.editorialmanager.com?journalAcronym="
                   + quote(location.path.rstrip("/").split("/")[-1], safe="")
                   + "&redirectUrl=" + quote(base + "/AuthorMainMenu.aspx", safe="")
                   + "&embedded=true&provider=ELSEVIER")
        browser_goto(page, sso_url, app, "EM")
        selector = 'input[name="pf.username"]'
        target = credential_target(page, selector, ELSEVIER_HOSTS, timeout_ms, "Elsevier SSO")
        dismiss_cookies(target)
        fill_verified(target, selector, cfg["email"], ELSEVIER_HOSTS, "Elsevier SSO")
        dismiss_cookies(target)
        click_verified(target, "#bdd-elsPrimaryBtn", ELSEVIER_HOSTS, "Elsevier SSO")
        selector = 'input[type="password"]'
        target = credential_target(page, selector, ELSEVIER_HOSTS, timeout_ms, "Elsevier SSO")
        dismiss_cookies(target)
        fill_verified(target, selector, cfg["password"], ELSEVIER_HOSTS, "Elsevier SSO")
        dismiss_cookies(target)
        click_verified(target, "#bdd-elsPrimaryBtn", ELSEVIER_HOSTS, "Elsevier SSO")
    else:
        browser_goto(page, base + "/default.aspx", app, "EM")
        dismiss_cookies(page)
        target = credential_target(page, "a#Login", {host}, timeout_ms, "EM")
        click_verified(target, "a#Login", {host}, "EM")
        # Some journals hide username/password until the Author role is selected.
        def login_ready():
            for frame in page.frames:
                try:
                    require_origin(frame.url, {host}, "EM")
                except RuntimeError:
                    continue
                if frame.is_detached() or not urlsplit(frame.url).path.lower().endswith("/login.ashx"):
                    continue
                dismiss_cookies(frame)
                role = frame.locator('[name="authorLogin"]').first
                if role.is_visible():
                    click_verified(frame, '[name="authorLogin"]', {host}, "EM")
                if frame.locator("#username").first.is_visible():
                    return frame
            return None
        poll(page, login_ready, timeout_ms, "EM verified native login frame not found")
        # Selecting a role may replace the login frame; never reuse its handle.
        target = credential_target(page, "#username", {host}, timeout_ms, "EM")
        if not urlsplit(target.url).path.lower().endswith("/login.ashx"):
            raise RuntimeError("EM credential form is not the expected login.ashx page")
        dismiss_cookies(target)
        fill_verified(target, "#username", cfg["email"], {host}, "EM")
        fill_verified(target, 'input[name="password"]', cfg["password"], {host}, "EM")
        dismiss_cookies(target)
        click_verified(target, "#loginButton", {host}, "EM")

    def menu_ready():
        frame = find_frame(page, host, menu_path)
        if frame is not None:
            dismiss_cookies(frame)
            return frame
        # Ping's optional stay-signed-in prompt, only on the verified identity host.
        for frame in page.frames:
            try:
                require_origin(frame.url, ELSEVIER_HOSTS, "Elsevier SSO")
            except RuntimeError:
                continue
            challenge_check(frame.content(), "Elsevier SSO")
            if re.search(r"stay signed in", frame.locator("body").inner_text(), re.I):
                button = frame.locator('#accept-button, button:has-text("Yes")').first
                if button.is_visible():
                    click_verified(frame, '#accept-button, button:has-text("Yes")', ELSEVIER_HOSTS, "Elsevier SSO")
        return None
    return poll(page, menu_ready, timeout_ms,
                "EM login did not reach the author menu; check credentials or complete CAPTCHA/MFA manually")


def _read_folder(page, app, base, host, link, timeout_ms):
    url = provider_url(link["url"], base, "EM", journal_path=True)
    path = urlsplit(url).path
    browser_goto(page, url, app, "EM")
    last_error = [None]
    def read():
        frame = find_frame(page, host, path)
        if frame is None:
            return None
        dismiss_cookies(frame)
        try:
            rows = parse_em_rows(frame.content())
        except RuntimeError as exc:
            if frame_race(exc):
                raise
            if "Pagination" in str(exc) or "truncated" in str(exc) or "manual" in str(exc):
                raise
            last_error[0] = str(exc)
            return None
        if not rows and link["count"] != 0:
            last_error[0] = "EM folder count mismatch: linked author folder returned no manuscripts; refusing an unverified empty list"
            return None
        if link["count"] is not None and len(rows) != link["count"]:
            last_error[0] = "EM folder count mismatch; list is empty, stale or truncated"
            return None
        return rows
    try:
        return poll(page, read, timeout_ms,
                    "EM folder could not be parsed; refusing an incomplete/empty snapshot")
    except RuntimeError:
        if last_error[0]:
            raise RuntimeError(last_error[0]) from None
        raise


def em_run(app):
    """Return a complete, filtered EM snapshot; notification URLs contain no session tokens."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("EM requires Playwright and its Chromium browser") from None
    cfg = app.cfg["em"]
    base, location = _base(cfg)
    _, timeout = network_options(app)
    timeout_ms = timeout * 1000
    host = location.hostname
    menu_path = location.path.rstrip("/") + "/AuthorMainMenu.aspx"
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options(app))
        try:
            ctx = browser.new_context(**context_options(app.em_storage_file))
            ctx.set_default_timeout(timeout_ms)
            page = ctx.new_page()
            browser_goto(page, base + "/default2.aspx", app, "EM")
            menu = poll(page, lambda: find_frame(page, host, menu_path),
                        min(timeout_ms, 5000), "", required=False)
            if menu is None:
                # A valid session can be parked on a replaced content frame.
                browser_goto(page, base + "/AuthorMainMenu.aspx", app, "EM")
                menu = poll(page, lambda: find_frame(page, host, menu_path),
                            min(timeout_ms, 3000), "", required=False)
            if menu is None:
                app.log("EM session expired; signing in")
                _login(page, app, base, location, timeout_ms)
            def read_menu():
                frame = find_frame(page, host, menu_path)
                if frame is None:
                    return None
                dismiss_cookies(frame)
                return _menu_links(frame.content(), base)
            links = poll(page, read_menu, timeout_ms,
                         "EM author menu has no parseable folder links or explicit empty state")
            journal = cfg.get("label") or location.path.rstrip("/").split("/")[-1].upper()
            result = {}
            for link in links:
                for row in _read_folder(page, app, base, host, link, timeout_ms):
                    key = row["number"] or norm(row["title"])[:80]
                    row["journal"] = journal
                    row["url"] = base + "/AuthorMainMenu.aspx"
                    row["signature"] = "%s|%s|%s" % (norm(row["title"])[:120], norm(row["status"]), norm(row["status_date"]))
                    if key in result and result[key] != row:
                        raise RuntimeError("EM conflicting/duplicate manuscript keys; refusing a partial snapshot")
                    result[key] = row
            state = app.state_for_provider() if callable(getattr(app, "state_for_provider", None)) else app.load_state()
            selected = select_em_manuscripts(result, cfg.get("watch", "all"), state.get("em", {}))
            app.save_session(ctx, app.em_storage_file)
            return selected
        finally:
            browser.close()
