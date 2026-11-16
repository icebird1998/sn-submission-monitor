"""Small, dependency-free helpers shared by the optional providers."""
import re
import time
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def norm(value):
    return re.sub(r"\s+", " ", unescape(unescape(value or ""))).replace("\xa0", " ").strip()


class Node:
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag = tag.lower()
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []

    def find_all(self, tag=None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.find_all(tag)

    def text(self):
        return norm(" ".join(c.text() if isinstance(c, Node) else c
                             for c in self.children))

    def lines(self):
        blocks = {"div", "p", "li", "br", "tr", "h1", "h2", "h3", "section", "dt", "dd"}
        def walk(node):
            parts = []
            for child in node.children:
                if isinstance(child, Node):
                    value = walk(child)
                    parts.append("\n" + value + "\n" if child.tag in blocks else value)
                else:
                    parts.append(child)
            return "".join(parts)
        return [norm(line) for line in walk(self).splitlines() if norm(line)]

    def ancestor(self, tag):
        node = self.parent
        while node is not None:
            if node.tag == tag:
                return node
            node = node.parent
        return None


def parse_html(source):
    class Parser(HTMLParser):
        void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                "meta", "param", "source", "track", "wbr"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.root = Node()
            self.current = self.root

        def handle_starttag(self, tag, attrs):
            node = Node(tag, attrs, self.current)
            self.current.children.append(node)
            if tag not in self.void:
                self.current = node

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag not in self.void:
                self.handle_endtag(tag)

        def handle_endtag(self, tag):
            node = self.current
            while node.parent is not None:
                if node.tag == tag:
                    self.current = node.parent
                    return
                node = node.parent

        def handle_data(self, data):
            if self.current.tag not in {"script", "style"}:
                self.current.children.append(data)

    parser = Parser()
    parser.feed(source)
    parser.close()
    return parser.root


def direct_cells(row):
    return [c for c in row.children if isinstance(c, Node) and c.tag in {"td", "th"}]


def table_rows(table):
    return [row for row in table.find_all("tr") if row.ancestor("table") is table]


def network_options(app):
    cfg = app.cfg.get("network") or {}
    attempts = int(cfg.get("attempts", 3))
    timeout = float(cfg.get("timeout_seconds", 45))
    if attempts < 1 or timeout <= 0:
        raise ValueError("network attempts and timeout_seconds must be positive")
    return attempts, timeout


def launch_options(app):
    result = {"headless": not bool(getattr(getattr(app, "args", None), "headed", False))}
    if app.cfg.get("proxy"):
        result["proxy"] = {"server": app.cfg["proxy"]}
    return result


def context_options(storage_file):
    return {"user_agent": UA, "locale": "en-US",
            "storage_state": str(storage_file) if Path(storage_file).exists() else None}


def _location(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme.lower() != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)
                or re.search(r"[\s\\\x00-\x1f\x7f]", url)):
            raise ValueError()
        return parsed
    except (TypeError, ValueError):
        raise RuntimeError("Refused an invalid or non-HTTPS provider URL") from None


def require_origin(url, hosts, label="Provider"):
    location = _location(url)
    if location.hostname.lower() not in {h.lower() for h in hosts}:
        raise RuntimeError("%s refused an unexpected host; credentials were not submitted" % label)
    return location


def provider_url(href, base, label="Provider", journal_path=False):
    origin = _location(base)
    if not isinstance(href, str) or not href.strip() or href != href.strip():
        raise RuntimeError("%s encountered an invalid manuscript/folder link" % label)
    # Reject backslashes/control characters before urljoin can normalize them.
    if re.search(r"[\\\x00-\x20\x7f]", href):
        raise RuntimeError("%s encountered an unsafe manuscript/folder link" % label)
    url = urljoin(base.rstrip("/") + "/", href)
    location = require_origin(url, {origin.hostname}, label)
    if journal_path:
        prefix = origin.path.rstrip("/") + "/"
        path = unquote(unquote(location.path))
        if (not path.lower().startswith(prefix.lower()) or "\\" in path
                or re.search(r"[\x00-\x1f\x7f]", path)
                or any(part in {".", ".."} for part in path.split("/"))):
            raise RuntimeError("%s refused a link outside the configured journal" % label)
    return url


def same_location(url, host, path):
    try:
        location = require_origin(url, {host})
        return location.path.lower() == path.lower()
    except RuntimeError:
        return False


def find_frame(page, host, path):
    for frame in page.frames:
        try:
            if not frame.is_detached() and same_location(frame.url, host, path):
                return frame
        except Exception as exc:
            if not frame_race(exc):
                raise
    return None


def frame_race(exc):
    text = str(exc).lower()
    return any(s in text for s in ("detached", "execution context was destroyed",
                                   "cannot find context", "frame was replaced",
                                   "navigation interrupted", "is navigating"))


def poll(page, callback, timeout_ms, label, required=True):
    """Bounded polling; callback must reacquire frames on every invocation."""
    deadline = time.monotonic() + timeout_ms / 1000
    for _ in range(max(1, int(timeout_ms / 200) + 2)):
        try:
            result = callback()
            if result is not None:
                return result
        except Exception as exc:
            if not frame_race(exc):
                raise
        remaining = (deadline - time.monotonic()) * 1000
        if remaining <= 0:
            break
        page.wait_for_timeout(min(200, remaining))
    if required:
        raise RuntimeError(label)
    return None


def browser_goto(page, url, app, label):
    attempts, timeout = network_options(app)
    transient = ("ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET", "ERR_TIMED_OUT",
                 "ERR_NETWORK_CHANGED", "Timeout", "timeout")
    for attempt in range(attempts):
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            status = getattr(response, "status", 200) if response is not None else 200
            if isinstance(status, int) and status >= 400:
                raise RuntimeError("%s returned HTTP %s; check network or complete security verification manually" % (label, status))
            return response
        except Exception as exc:
            if str(exc).startswith(label + " returned HTTP "):
                raise
            if attempt + 1 == attempts or not any(code in str(exc) for code in transient):
                raise RuntimeError("%s navigation failed; check network/TLS and sign in manually if needed" % label) from None
            app.log("%s navigation retry %d/%d" % (label, attempt + 1, attempts))


def dismiss_cookies(target):
    # Never opt into optional cookies, including on the second login step.
    for selector in ("#onetrust-reject-all-handler",
                     'button:has-text("Reject optional cookies")',
                     'button:has-text("Reject all cookies")',
                     'button:has-text("Reject all")',
                     'button:has-text("Necessary cookies only")',
                     'button:has-text("Only necessary cookies")',
                     'button:has-text("Use necessary cookies only")'):
        try:
            button = target.locator(selector).first
            if button.is_visible():
                button.click(timeout=1500)
                return True
        except Exception as exc:
            if frame_race(exc):
                return False
    return False


def challenge_check(source, label):
    text = parse_html(source).text()
    if re.search(r"verify (?:that )?you are human|complete (?:the )?captcha|"
                 r"enter (?:your |the )?(?:verification|authentication|security) code|"
                 r"multi.factor authentication|two.factor authentication|unusual traffic",
                 text, re.I):
        raise RuntimeError("%s requires manual CAPTCHA/MFA or security verification; automation stopped" % label)


def credential_target(page, selector, hosts, timeout_ms, label):
    def ready():
        for frame in page.frames:
            try:
                if frame.is_detached():
                    continue
                require_origin(frame.url, hosts, label)
            except RuntimeError:
                continue
            challenge_check(frame.content(), label)
            if frame.locator(selector).first.is_visible():
                return frame
        return None
    return poll(page, ready, timeout_ms,
                "%s login field not found on a verified HTTPS host; sign in manually (CAPTCHA/MFA is not automated)" % label)


def credential_guard(target, selector, hosts, label):
    require_origin(target.url, hosts, label)
    action = target.locator(selector).first.evaluate(
        "el => { const f = el.closest('form'); return f ? f.action : ''; }")
    if action:
        require_origin(urljoin(target.url, action), hosts, label)


def fill_verified(target, selector, value, hosts, label):
    credential_guard(target, selector, hosts, label)
    target.locator(selector).first.fill(value)


def click_verified(target, selector, hosts, label):
    credential_guard(target, selector, hosts, label)
    target.locator(selector).first.click()


def pagination_check(root, row_count=None):
    """Detect unsupported/truncated lists rather than accepting their first page."""
    text = root.text()
    for match in re.finditer(r"\bpage\s+\d+\s+(?:of|/)\s*(\d+)", text, re.I):
        if int(match.group(1)) > 1:
            raise RuntimeError("Pagination detected; refusing a truncated manuscript list")
    for match in re.finditer(r"\b(?:showing|displaying)?\s*(\d+)\s*(?:-|–|to)\s*(\d+)\s+of\s+(\d+)", text, re.I):
        if int(match.group(1)) != 1 or int(match.group(2)) < int(match.group(3)):
            raise RuntimeError("Pagination detected; refusing a truncated manuscript list")
    for node in root.find_all():
        disabled = ("disabled" in node.attrs or node.attrs.get("aria-disabled", "").lower() == "true"
                    or "disabled" in (node.attrs.get("class") or "").lower().split())
        if node.parent is not None:
            disabled = disabled or "disabled" in (node.parent.attrs.get("class") or "").lower().split()
        if node.tag in {"a", "button", "input"} and not disabled:
            name = norm(node.attrs.get("aria-label") or node.attrs.get("title")
                        or node.attrs.get("value") or node.text())
            if (node.attrs.get("rel", "").lower() == "next"
                    or re.fullmatch(r"next(?: page)?|last(?: page)?|[»›>]+", name, re.I)
                    or (node.tag == "a" and re.search(r"(?:[?&]|__doPostBack\([^)]*)page(?:index|number)?[=$,]", node.attrs.get("href", ""), re.I))):
                raise RuntimeError("Pagination detected; refusing a truncated manuscript list")
        if node.tag == "select" and re.search(r"page(?:index|number|no)?$", node.attrs.get("name", "") + node.attrs.get("id", ""), re.I):
            if len(list(node.find_all("option"))) > 1:
                raise RuntimeError("Pagination detected; refusing a truncated manuscript list")
        for attr in ("data-page-count", "data-total-pages"):
            if (node.attrs.get(attr) or "").isdigit() and int(node.attrs[attr]) > 1:
                raise RuntimeError("Pagination detected; refusing a truncated manuscript list")
        if row_count is not None:
            for attr in ("data-total-records", "data-total-count"):
                value = node.attrs.get(attr, "")
                if value.isdigit() and int(value) != row_count:
                    raise RuntimeError("Manuscript count mismatch; list may be truncated")
