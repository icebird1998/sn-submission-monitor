"""eJournalPress/Aries MTS (mts-*.nature.com); this is not ScholarOne."""
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from .common import (UA, challenge_check, direct_cells, network_options, norm,
                     pagination_check, parse_html, provider_url, require_origin)


def _base(cfg):
    base = cfg["base"].rstrip("/")
    location = urlsplit(base)
    host = (location.hostname or "").lower()
    if not re.fullmatch(r"mts-[a-z0-9-]+\.nature\.com", host):
        raise ValueError("MTS supports eJournalPress/Aries mts-*.nature.com, not ScholarOne")
    require_origin(base, {host}, "MTS")
    if location.query or location.fragment or location.path not in {"", "/"}:
        raise ValueError("MTS base must be the HTTPS MTS origin")
    return base


def _request(session, method, url, base, app, **kwargs):
    """Follow only same-origin HTTPS redirects; never replay credentials externally."""
    attempts, timeout = network_options(app)
    url = provider_url(url, base, "MTS")
    for _ in range(6):
        response = None
        for attempt in range(attempts):
            try:
                response = session.request(method, url, timeout=timeout,
                                           allow_redirects=False, **kwargs)
            except Exception as exc:
                # Requests exceptions are loaded only with this provider.
                import requests
                if not isinstance(exc, (requests.Timeout, requests.ConnectionError)) or attempt + 1 == attempts:
                    raise RuntimeError("MTS request failed; check network/TLS or sign in manually") from None
                continue
            if response.status_code in {429, 502, 503, 504} and attempt + 1 < attempts:
                continue
            break
        if response.status_code in {301, 302, 303, 307, 308}:
            href = response.headers.get("Location", "")
            next_url = provider_url(urljoin(url, href), base, "MTS")
            require_origin(next_url, {urlsplit(base).hostname}, "MTS")
            if response.status_code in {301, 302, 303} and method == "POST":
                method, kwargs = "GET", {}
            url = next_url
            continue
        try:
            response.raise_for_status()
        except Exception:
            raise RuntimeError("MTS returned HTTP %s; sign in manually if security verification is required" % response.status_code) from None
        require_origin(response.url or url, {urlsplit(base).hostname}, "MTS")
        challenge_check(response.text, "MTS")
        return response
    raise RuntimeError("MTS exceeded the safe redirect limit")


def _links(source, base, form_type):
    root = parse_html(source)
    links = {}
    for anchor in root.find_all("a"):
        href = anchor.attrs.get("href", "")
        query = parse_qs(urlsplit(href).query)
        if not any(value.lower() == form_type for value in query.get("form_type", [])):
            continue
        url = provider_url(href, base, "MTS")
        if urlsplit(url).path.lower() != "/cgi-bin/main.plex":
            raise RuntimeError("MTS unexpected manuscript/folder path")
        links[url] = parse_qs(urlsplit(url).query)
    pagination_check(root)
    return links


def parse_mts_detail(source, base, mid):
    root = parse_html(source)
    fields = {}
    for row in root.find_all("tr"):
        cells = direct_cells(row)
        if len(cells) < 2:
            continue
        label = norm(cells[0].text()).rstrip(":").strip().lower()
        if label in {"current stage", "manuscript #", "manuscript number", "title"}:
            value = cells[1].text()
            if label in fields and fields[label] != value:
                raise RuntimeError("MTS conflicting manuscript fields")
            fields[label] = value
    status = fields.get("current stage")
    if not status or not fields.get("title"):
        raise RuntimeError("MTS manuscript ms_id=%s is missing Current Stage/Title" % mid)
    return {"status": status, "ms_number": fields.get("manuscript #") or fields.get("manuscript number", ""),
            "title": fields["title"], "journal": base,
            "url": base + "/cgi-bin/main.plex", "signature": norm(status)}


def _only_ids(value):
    if value is None:
        return None
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    result = {str(v).strip() for v in values}
    if not result or any(not re.fullmatch(r"\d+", v) for v in result):
        raise ValueError("MTS filters must be numeric ms_id values")
    return result


def mts_run(app, check_only_ms=None):
    """Collect MTS folders and details, respecting a scalar or list of ms_id filters."""
    try:
        import requests
    except ImportError:
        raise RuntimeError("MTS requires requests") from None
    cfg = app.cfg["mts"]
    base = _base(cfg)
    only = _only_ids(check_only_ms)
    session = requests.Session()
    try:
        session.headers.update({"User-Agent": UA})
        if app.cfg.get("proxy"):
            session.proxies.update({"https": app.cfg["proxy"]})
        endpoint = base + "/cgi-bin/main.plex"
        first = _request(session, "GET", endpoint, base, app)
        root = parse_html(first.text)
        forms = [form for form in root.find_all("form")
                 if any(node.attrs.get("name") == "password" for node in form.find_all("input"))]
        if len(forms) != 1:
            raise RuntimeError("MTS login form not found or ambiguous; no credentials submitted")
        form = forms[0]
        action = provider_url(form.attrs.get("action") or endpoint, base, "MTS")
        if urlsplit(action).path.lower() != "/cgi-bin/main.plex":
            raise RuntimeError("MTS refused an unexpected login action")
        hidden = {node.attrs["name"]: node.attrs.get("value", "")
                  for node in form.find_all("input")
                  if node.attrs.get("name") and node.attrs.get("type", "").lower() == "hidden"}
        hidden.update({"form_type": "login_results", "login": cfg["email"],
                       "password": cfg["password"], "submit_login": "Login"})
        logged = _request(session, "POST", action, base, app, data=hidden)
        if not re.search(r"form_type=logout|log\s*out", logged.text, re.I):
            raise RuntimeError("MTS login failed; check credentials or complete CAPTCHA/MFA manually")
        folders = _links(logged.text, base, "dt_folder")
        manuscripts = _links(logged.text, base, "view_ms")
        seen = set()
        queue = list(folders)
        while queue:
            url = queue.pop(0)
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            # Session-key changes do not make a new folder.
            identity = (parsed.path, tuple(sorted((k, tuple(v)) for k, v in query.items() if k != "ms_id_key")))
            if identity in seen:
                continue
            if len(seen) >= 100:
                raise RuntimeError("MTS folder limit reached; refusing a truncated list")
            seen.add(identity)
            folder = _request(session, "GET", url, base, app)
            manuscripts.update(_links(folder.text, base, "view_ms"))
            queue.extend(_links(folder.text, base, "dt_folder"))
        by_id = {}
        for link, query in manuscripts.items():
            ids = query.get("ms_id", [])
            if len(ids) != 1 or not re.fullmatch(r"\d+", ids[0]):
                raise RuntimeError("MTS malformed manuscript link")
            by_id[ids[0]] = link
        if not by_id:
            # Historical MTS installations do not have a stable universal empty indicator.
            raise RuntimeError("MTS found no manuscripts; refusing an unverified empty list")
        if only is not None:
            missing = only - by_id.keys()
            if missing:
                raise RuntimeError("MTS requested ms_id not found: %s" % ", ".join(sorted(missing)))
            by_id = {mid: link for mid, link in by_id.items() if mid in only}
        return {mid: parse_mts_detail(_request(session, "GET", link, base, app).text, base, mid)
                for mid, link in by_id.items()}
    finally:
        session.close()
