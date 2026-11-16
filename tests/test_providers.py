"""Offline, synthetic provider tests. No credentials, sessions or network are used."""
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

# Also support `python -I -m unittest discover -s <absolute tests path>`.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from submission_monitor.providers import collect
from submission_monitor.providers import common, em, mts, sn

FIXTURES = Path(__file__).with_name("fixtures")
SN_HTML = (FIXTURES / "providers_sn.html").read_text(encoding="utf-8")
EM_HTML = (FIXTURES / "providers_em.html").read_text(encoding="utf-8")
MTS_HTML = (FIXTURES / "providers_mts.html").read_text(encoding="utf-8")
EM_BASE = "https://www.editorialmanager.com/synthetic"
MTS_BASE = "https://mts-synthetic.nature.com"


def locator(visible=False):
    item = Mock()
    item.first = item
    item.is_visible.return_value = visible
    item.evaluate.return_value = ""
    return item


def frame(url, source="", detached=False):
    item = Mock()
    item.url = url
    item.is_detached.return_value = detached
    item.content.return_value = source
    item.locator.return_value = locator()
    return item


class ProviderParsingTests(unittest.TestCase):
    def test_sn_status_ignores_last_update_and_description(self):
        rows = sn.parse_sn_cards(SN_HTML)
        self.assertEqual(set(rows), {"synthetic-001", "synthetic-002"})
        self.assertEqual(rows["synthetic-001"]["status"], "Under review")
        self.assertEqual(rows["synthetic-001"]["signature"], "Under review||Version 1")
        self.assertEqual(rows["synthetic-001"]["title"], "Synthetic climate study")
        self.assertEqual(rows["synthetic-002"]["status"], "Editor assigned")
        changed_date = SN_HTML.replace("15 November 2026", "16 November 2026")
        self.assertEqual(rows, sn.parse_sn_cards(changed_date))

    def test_sn_inline_boundary_and_separate_last_update_date(self):
        source = ('<li class="submission-item"><span data-test="submission-title">Inline title</span>'
                  '<span data-test="journal-title">Synthetic</span><span data-test="submission-version">v.1.0</span>'
                  '<a data-test="submission-details-link" href="/submission-details/inline-1">View submission details</a>'
                  '<div>Technical check</div><div>Last update</div><div>16 November 2026</div></li>')
        self.assertEqual(sn.parse_sn_cards(source)["inline-1"]["status"], "Technical check")
        no_status = source.replace("<div>Technical check</div>", "")
        with self.assertRaises(RuntimeError):
            sn.parse_sn_cards(no_status)
        with self.assertRaises(RuntimeError):
            sn.parse_sn_cards("<p>You have submissions (2)</p><p>Loading</p>")

    def test_sn_empty_requires_explicit_proof(self):
        self.assertEqual(sn.parse_sn_cards("<h1>No submissions yet</h1>"), {})
        for source in ("<h1>Your submissions</h1>", "<div>Loading...</div>",
                       SN_HTML.replace('data-test="submission-title"', 'data-test="unknown"')):
            with self.subTest(source=source[:30]), self.assertRaises(RuntimeError):
                sn.parse_sn_cards(source)

    def test_sn_only_metadata_is_not_a_status(self):
        source = SN_HTML.split('<li class="submission-item">')[1]
        source = '<li class="submission-item">' + source.split("</li>")[0] + "</li>"
        source = source.replace("<div>Under review</div>", "").replace("<p>Reviewers are evaluating your manuscript.</p>", "")
        with self.assertRaisesRegex(RuntimeError, "Last update"):
            sn.parse_sn_cards(source)

    def test_sn_unsafe_links_and_pagination_fail(self):
        for link in ("http://submission.springernature.com/submission-details/a",
                     "https://submission.springernature.com.evil.test/submission-details/a",
                     "https://evil.test/submission-details/a", "javascript:alert(1)"):
            with self.subTest(link=link), self.assertRaises(RuntimeError):
                sn.parse_sn_cards(SN_HTML.replace("/submission-details/synthetic-001", link, 1))
        with self.assertRaisesRegex(RuntimeError, "Pagination"):
            sn.parse_sn_cards(SN_HTML + '<a rel="next" href="?page=2">Next</a>')

    def test_em_direct_rows_ignore_nested_layout_action_tables(self):
        rows = em.parse_em_rows(EM_HTML)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["number"], "SYN-26-0001R1")
        self.assertEqual(rows[0]["status"], "Under Review")
        self.assertEqual(rows[1]["submitted"], "16 Nov 2026")
        self.assertNotIn("url", rows[0])
        self.assertNotIn("secret", json.dumps(rows))

    def test_em_headers_case_insensitive_not_substring(self):
        rows = em.parse_em_rows(EM_HTML.replace("Title</th>", "TITLE</th>"))
        self.assertEqual(len(rows), 2)
        with self.assertRaisesRegex(RuntimeError, "Title header"):
            em.parse_em_rows(EM_HTML.replace("Title</th>", "Other Title Field</th>"))
        with self.assertRaises(RuntimeError):
            em.parse_em_rows("<table><tr><td>Title</td><td>Previous Current Status</td></tr></table>")

    def test_em_missing_status_and_missing_table_are_not_empty(self):
        for source in ("<p>Submissions Being Processed (2)</p><p>Loading</p>",
                       EM_HTML.replace("Under Review", ""),
                       EM_HTML.replace("<td>Synthetic ocean study</td>", "")):
            with self.subTest(source=source[:40]), self.assertRaises(RuntimeError):
                em.parse_em_rows(source)
        self.assertEqual(em.parse_em_rows("<p>No manuscripts found</p>"), [])

    def test_em_detects_pagination_and_count_truncation(self):
        for extra in ('<p>Page 1 of 2</p>', '<p>Showing 1-2 of 7</p>',
                      '<a href="javascript:goNext()">Next Page</a>',
                      '<div data-total-records="8"></div>',
                      '<div data-page-count="2"></div>'):
            with self.subTest(extra=extra), self.assertRaises(RuntimeError):
                em.parse_em_rows(EM_HTML + extra)
        self.assertEqual(len(em.parse_em_rows(EM_HTML + '<a aria-disabled="true">Next Page</a>')), 2)

    def test_mts_detail_direct_fields_and_safe_url(self):
        item = mts.parse_mts_detail(MTS_HTML, MTS_BASE, "77")
        self.assertEqual(item["status"], "Manuscript Under Consideration")
        self.assertEqual(item["title"], "A synthetic & reproducible study")
        self.assertEqual(item["ms_number"], "SYN-26-0001")
        self.assertEqual(item["url"], MTS_BASE + "/cgi-bin/main.plex")
        with self.assertRaises(RuntimeError):
            mts.parse_mts_detail(MTS_HTML.replace("Current <span>Stage</span>", "Other"), MTS_BASE, "77")

    def test_mts_escaped_queries_and_unsafe_links(self):
        html = '<a href="/cgi-bin/main.plex?form_type=view_ms&amp;ms_id=77&amp;ms_id_key=fake">View</a>'
        links = mts._links(html, MTS_BASE, "view_ms")
        self.assertEqual(next(iter(links.values()))["ms_id"], ["77"])
        for href in ("http://mts-synthetic.nature.com/cgi-bin/main.plex?form_type=view_ms&ms_id=77",
                     "https://evil.test/cgi-bin/main.plex?form_type=view_ms&ms_id=77"):
            with self.assertRaises(RuntimeError):
                mts._links('<a href="' + href + '">View</a>', MTS_BASE, "view_ms")
        with self.assertRaises(ValueError):
            mts._base({"base": "https://mc.manuscriptcentral.com/synthetic"})


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.items = {number: {"number": number, "title": "Synthetic", "submitted": "01 Nov 2026", "status": "Under Review"}
                      for number in ("SYN-1", "SYN-1R1", "SYN-1R2", "SYN-10")}

    def test_all_and_number_family_and_explicit_revision(self):
        self.assertIs(em.select_em_manuscripts(self.items, "all"), self.items)
        self.assertEqual(set(em.select_em_manuscripts(self.items, ["syn-1"])), {"SYN-1", "SYN-1R1", "SYN-1R2"})
        self.assertEqual(set(em.select_em_manuscripts(self.items, ["syn-1r1"])), {"SYN-1R1"})
        with self.assertRaises(RuntimeError):
            em.select_em_manuscripts(self.items, ["SYN-2"])

    def test_title_date_binding_then_changed_revision_title(self):
        watch = [{"title": "Synthetic", "submitted": "01 Nov 2026"}]
        numberless = {"Synthetic": {"number": "", "title": "Synthetic", "submitted": "01 Nov 2026"}}
        initial = em.select_em_manuscripts(numberless, watch)
        self.assertEqual(initial["Synthetic"]["_watch_number"], "")
        numbered = em.select_em_manuscripts({"SYN-1": self.items["SYN-1"]}, watch, initial)
        self.assertEqual(numbered["SYN-1"]["_watch_number"], "SYN-1")
        later = {"SYN-1R2": {"number": "SYN-1R2", "title": "Changed title", "submitted": "16 Nov 2026"}}
        selected = em.select_em_manuscripts(later, watch, numbered)
        self.assertEqual(set(selected), {"SYN-1R2"})
        self.assertEqual(selected["SYN-1R2"]["_watch_number"], "SYN-1")
        self.assertNotIn("_watch_key", later["SYN-1R2"])

    def test_title_date_ambiguity_and_invalid_filters(self):
        with self.assertRaisesRegex(RuntimeError, "multiple"):
            em.select_em_manuscripts(self.items, [{"title": "Synthetic", "submitted": "01 Nov 2026"}])
        for watch in ([], None, [""], [{"title": "Synthetic"}], [{"title": "Synthetic", "submitted": ""}]):
            with self.subTest(watch=watch), self.assertRaises(ValueError):
                em.select_em_manuscripts(self.items, watch)

    def test_number_selector_does_not_remove_binding(self):
        row = {"SYN-1": self.items["SYN-1"]}
        title = {"title": "Synthetic", "submitted": "01 Nov 2026"}
        for watch in ([title, "SYN-1"], ["SYN-1", title]):
            self.assertIn("_watch_key", em.select_em_manuscripts(row, watch)["SYN-1"])

    def test_sn_id_filters(self):
        self.assertEqual(sn._watch_ids(["synthetic-001", sn.SN_BASE + "/submission-details/synthetic-002"]),
                         {"synthetic-001", "synthetic-002"})
        for watch in ([], [""], ["https://evil.test/a"], ["../a"]):
            with self.subTest(watch=watch), self.assertRaises((ValueError, RuntimeError)):
                sn._watch_ids(watch)


class BrowserGuardTests(unittest.TestCase):
    def test_exact_host_and_https(self):
        for url in ("https://id.elsevier.com.evil.test/", "http://id.elsevier.com/",
                    "https://user:password@id.elsevier.com/", "https://id.elsevier.com:444/"):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                common.require_origin(url, {"id.elsevier.com"})
        self.assertEqual(common.require_origin("https://id.elsevier.com/login", {"id.elsevier.com"}).hostname,
                         "id.elsevier.com")

    def test_author_menu_exact_host_path_not_query_or_substring(self):
        path = "/synthetic/AuthorMainMenu.aspx"
        invalid = [frame(EM_BASE + "/Other.aspx?next=AuthorMainMenu.aspx"),
                   frame("https://evil.test/synthetic/AuthorMainMenu.aspx"),
                   frame(EM_BASE + "/AuthorMainMenu.aspx.extra")]
        good = frame(EM_BASE + "/AUTHormainmenu.aspx?ignored=1")
        page = Mock(frames=invalid + [good])
        self.assertIs(common.find_frame(page, "www.editorialmanager.com", path), good)

    def test_cookie_reject_preferred_never_accept(self):
        calls = []
        target = Mock()
        def get(selector):
            calls.append(selector)
            return locator("Reject optional cookies" in selector)
        target.locator.side_effect = get
        self.assertTrue(common.dismiss_cookies(target))
        self.assertFalse(any("Accept all" in call for call in calls))

    def test_credential_form_action_guard_prevents_fill(self):
        target = frame("https://id.elsevier.com/login")
        field = locator(True)
        field.evaluate.return_value = "https://evil.test/collect"
        target.locator.return_value = field
        with self.assertRaises(RuntimeError):
            common.fill_verified(target, "input", "synthetic-not-a-secret", {"id.elsevier.com"}, "Elsevier")
        field.fill.assert_not_called()

    def test_frame_replacement_reacquired_during_folder_poll(self):
        old = frame(EM_BASE + "/auth_status.aspx")
        old.content.side_effect = RuntimeError("Frame was detached")
        new = frame(EM_BASE + "/auth_status.aspx", EM_HTML)
        page = Mock()
        app = SimpleNamespace(cfg={"network": {"timeout_seconds": 1, "attempts": 1}}, log=Mock())
        with patch.object(em, "browser_goto"), patch.object(em, "dismiss_cookies"), \
                patch.object(em, "find_frame", side_effect=[old, new]):
            rows = em._read_folder(page, app, EM_BASE, "www.editorialmanager.com",
                                   {"url": EM_BASE + "/auth_status.aspx", "count": 2}, 500)
        self.assertEqual(len(rows), 2)

    def test_nonzero_folder_parse_failure_never_returns_empty(self):
        broken = frame(EM_BASE + "/auth_status.aspx", "<p>Loading</p>")
        app = SimpleNamespace(cfg={}, log=Mock())
        with patch.object(em, "browser_goto"), patch.object(em, "dismiss_cookies"), \
                patch.object(em, "find_frame", return_value=broken), self.assertRaisesRegex(RuntimeError, "parsing failed"):
            em._read_folder(Mock(), app, EM_BASE, "www.editorialmanager.com",
                            {"url": EM_BASE + "/auth_status.aspx", "count": 2}, 1)
        empty = frame(EM_BASE + "/auth_status.aspx", "<p>No manuscripts found</p>")
        for count in (2, None):
            with patch.object(em, "browser_goto"), patch.object(em, "dismiss_cookies"), \
                    patch.object(em, "find_frame", return_value=empty), self.assertRaisesRegex(RuntimeError, "count mismatch"):
                em._read_folder(Mock(), app, EM_BASE, "www.editorialmanager.com",
                                {"url": EM_BASE + "/auth_status.aspx", "count": count}, 1)

    def test_sn_cookies_before_both_submits_and_headed_default(self):
        first, second = frame("https://idp-personal-authenticator.springernature.com/gateway"), frame("https://idp.springernature.com/gateway")
        second.locator.return_value = locator(True)
        app = SimpleNamespace(cfg={"sn": {"email": "synthetic@example.test", "password": "synthetic"}})
        events = []
        with patch.object(sn, "credential_target", side_effect=[first, second]), \
                patch.object(sn, "dismiss_cookies", side_effect=lambda target: events.append(("cookies", target))), \
                patch.object(sn, "fill_verified", side_effect=lambda target, *args: events.append(("fill", target))), \
                patch.object(sn, "click_verified", side_effect=lambda target, *args: events.append(("submit", target))):
            sn.sn_login(Mock(), app)
        for index, event in enumerate(events):
            if event[0] == "submit":
                self.assertEqual(events[index - 1], ("cookies", event[1]))
        self.assertEqual(common.launch_options(app), {"headless": True})
        app.args = SimpleNamespace(headed=True)
        self.assertEqual(common.launch_options(app), {"headless": False})

    def test_http_errors_and_challenges_fail_clearly(self):
        page = Mock()
        page.goto.return_value = SimpleNamespace(status=403)
        app = SimpleNamespace(cfg={}, log=Mock())
        with self.assertRaises(RuntimeError):
            common.browser_goto(page, EM_BASE, app, "EM")
        for source in ("<p>Verify you are human</p>", "<p>Enter your verification code</p>"):
            with self.assertRaisesRegex(RuntimeError, "manual CAPTCHA/MFA"):
                common.challenge_check(source, "EM")

    def test_mts_cross_host_redirect_not_followed(self):
        session = Mock()
        session.request.return_value = SimpleNamespace(status_code=302, headers={"Location": "https://evil.test/"})
        app = SimpleNamespace(cfg={}, log=Mock())
        with self.assertRaises(RuntimeError):
            mts._request(session, "POST", MTS_BASE + "/cgi-bin/main.plex", MTS_BASE, app,
                         data={"password": "synthetic"})
        self.assertEqual(session.request.call_count, 1)
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])

    def test_menu_folder_urls_stay_in_journal(self):
        links = em._menu_links('<a href="auth_status.aspx">Submissions Being Processed (2)</a>', EM_BASE)
        self.assertEqual(links[0]["count"], 2)
        for href in ("https://evil.test/auth_status.aspx", "/other/auth_status.aspx", "http://www.editorialmanager.com/synthetic/auth_status.aspx"):
            with self.assertRaises(RuntimeError):
                em._menu_links('<a href="' + href + '">Submissions (2)</a>', EM_BASE)
        self.assertEqual(em._menu_links("<p>Submissions Being Processed (0)</p>", EM_BASE), [])
        self.assertIsNone(em._menu_links("<p>Loading author menu</p>", EM_BASE))


class MockedRunnerTests(unittest.TestCase):
    def playwright(self, page):
        context = Mock()
        context.new_page.return_value = page
        browser = Mock()
        browser.new_context.return_value = context
        engine = Mock()
        engine.chromium.launch.return_value = browser
        manager = Mock()
        manager.__enter__ = Mock(return_value=engine)
        manager.__exit__ = Mock(return_value=False)
        module = ModuleType("playwright.sync_api")
        module.sync_playwright = Mock(return_value=manager)
        package = ModuleType("playwright")
        package.sync_api = module
        return {"playwright": package, "playwright.sync_api": module}, context, browser

    def test_em_runner_safe_landing_and_inflight_watch_binding(self):
        menu_url = EM_BASE + "/AuthorMainMenu.aspx"
        menu = frame(menu_url, '<a href="auth_status.aspx">Submissions Being Processed (2)</a>')
        folder = frame(EM_BASE + "/auth_status.aspx", EM_HTML)
        page = Mock()
        def goto(url, **kwargs):
            page.frames = [menu if "auth_status" not in url.lower() else folder]
            return SimpleNamespace(status=200)
        page.goto.side_effect = goto
        modules, context, browser = self.playwright(page)
        watch = [{"title": "Older title", "submitted": "01 Jan 2026"}]
        marker = json.dumps(watch[0], ensure_ascii=False, sort_keys=True)
        app = SimpleNamespace(cfg={"em": {"base": EM_BASE, "watch": watch},
                                   "network": {"timeout_seconds": 1}},
                              em_storage_file=ROOT / "tests" / "synthetic-nonexistent-em-session.json",
                              state_for_provider=Mock(return_value={"em": {"old": {"_watch_key": marker, "_watch_number": "SYN-26-0001"}}}),
                              load_state=Mock(side_effect=AssertionError("must prefer in-flight state")),
                              log=Mock(), save_session=Mock())
        with patch.dict(sys.modules, modules), patch.object(em, "dismiss_cookies"):
            result = em.em_run(app)
        self.assertEqual(set(result), {"SYN-26-0001R1"})
        self.assertEqual(result["SYN-26-0001R1"]["url"], menu_url)
        self.assertEqual(result["SYN-26-0001R1"]["_watch_number"], "SYN-26-0001")
        self.assertNotIn("sessionid", json.dumps(result))
        app.save_session.assert_called_once_with(context, app.em_storage_file)
        browser.close.assert_called_once()

    def test_sn_runner_filter_atomic_session_and_closes_on_error(self):
        page = Mock()
        page.url = sn.SN_BASE + "/your-submissions"
        page.content.return_value = SN_HTML
        page.goto.return_value = SimpleNamespace(status=200)
        modules, context, browser = self.playwright(page)
        app = SimpleNamespace(cfg={"sn": {}, "network": {"timeout_seconds": 1}},
                              storage_file=ROOT / "tests" / "synthetic-nonexistent-sn-session.json",
                              save_session=Mock(), log=Mock())
        with patch.dict(sys.modules, modules), patch.object(sn, "dismiss_cookies"):
            result = sn.sn_run(app, {"synthetic-002"})
        self.assertEqual(set(result), {"synthetic-002"})
        app.save_session.assert_called_once_with(context, app.storage_file)
        browser.close.assert_called_once()
        app.save_session.reset_mock()
        browser.close.reset_mock()
        with patch.dict(sys.modules, modules), patch.object(sn, "dismiss_cookies"), self.assertRaises(RuntimeError):
            sn.sn_run(app, {"missing"})
        app.save_session.assert_not_called()
        browser.close.assert_called_once()

    def test_mts_runner_and_relative_redirect_stay_https(self):
        def response(text, status=200, headers=None, url=None):
            return SimpleNamespace(text=text, status_code=status, headers=headers or {},
                                   url=url or MTS_BASE + "/cgi-bin/main.plex", raise_for_status=Mock())
        endpoint = MTS_BASE + "/cgi-bin/main.plex"
        login = '<form action="/cgi-bin/main.plex"><input type="hidden" name="j_id" value="synthetic"><input name="password"></form>'
        menu = '<a href="?form_type=logout">Log Out</a><a href="/cgi-bin/main.plex?form_type=dt_folder&amp;folder=1">Folder</a>'
        listing = '<a href="/cgi-bin/main.plex?form_type=view_ms&amp;ms_id=77">Manuscript</a>'
        session = Mock()
        session.request.side_effect = [response(login), response(menu), response(listing), response(MTS_HTML)]
        module = ModuleType("requests")
        module.Session = Mock(return_value=session)
        app = SimpleNamespace(cfg={"mts": {"base": MTS_BASE, "email": "synthetic@example.test", "password": "synthetic"}}, log=Mock())
        with patch.dict(sys.modules, {"requests": module}):
            result = mts.mts_run(app, "77")
        self.assertEqual(set(result), {"77"})
        self.assertEqual(result["77"]["url"], endpoint)
        session.close.assert_called_once()
        redirect_session = Mock()
        redirect_session.request.side_effect = [response("", 302, {"Location": "main.plex?form_type=home"}), response(menu)]
        mts._request(redirect_session, "POST", endpoint, MTS_BASE, app, data={"password": "synthetic"})
        second = redirect_session.request.call_args_list[1]
        self.assertEqual(second.args, ("GET", endpoint + "?form_type=home"))
        self.assertNotIn("data", second.kwargs)


class DispatchTests(unittest.TestCase):
    def test_collect_preserves_filters(self):
        app = SimpleNamespace(cfg={"sn": {"watch": ["synthetic-001"]},
                                   "mts": {"ms_id": "77"}, "em": {}})
        with patch.object(sn, "sn_run", return_value={}) as run:
            collect(app, "sn")
            run.assert_called_once_with(app, ["synthetic-001"])
        with patch.object(mts, "mts_run", return_value={}) as run:
            collect(app, "mts")
            run.assert_called_once_with(app, "77")
        with patch.object(em, "em_run", return_value={}) as run:
            collect(app, "em")
            run.assert_called_once_with(app)
        with self.assertRaises(ValueError):
            collect(app, "all")

    def test_imports_do_not_load_optional_dependencies(self):
        code = ("import sys; sys.path.insert(0, " + repr(str(ROOT)) + "); "
                "import submission_monitor.providers; "
                "import submission_monitor.providers.sn, submission_monitor.providers.em, submission_monitor.providers.mts; "
                "assert 'requests' not in sys.modules; assert 'playwright' not in sys.modules")
        result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
