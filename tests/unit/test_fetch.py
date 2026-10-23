"""fetch.py: URL cleaning, labels, model matching, verification from the front pages, the
catalog and its freshness rule, the local manuals folder, the web-search hand-off, and get
against a fake HTTP server (a tiny PDF generated here). Nothing touches the network."""
import contextlib
import email.message
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import cleanup  # noqa: E402
import fetch  # noqa: E402
import storage  # noqa: E402

try:
    import pypdfium2  # noqa: F401
    HAVE_PDFIUM = True
except ImportError:
    HAVE_PDFIUM = False

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch.py"
D = 86400

# A fictional title page and a parameter page; nothing here is copied from a real manual.
TITLE = ["Control User Guide", "Widgetron W200/", "W300", "Variable speed drive for widgets",
         "Part Number: 0478-0535-02", "Issue: 2"]
FRONT = ["Firmware compatibility", "This guide applies to drive firmware V1.10 and later.",
         "Revision history", "Issue 1 2018-05", "Issue 2 2019-10"]
PARAMS = ["Menu 11", "11.029 Software Version 00.00.00 to 99.99.99",
          "Displays the software version in the drive."]
OTHER = ["Gizmotron G5", "Instruction Manual", "Edition 03", "The quick brown fox jumps over the lazy dog and this is English text."]


def make_pdf(pages, title=None):
    """A minimal valid PDF, one Helvetica text block per page, text placed below the header band."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    body = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    for i, lines in enumerate(pages):
        content = "BT /F1 12 Tf 72 700 Td 16 TL " + " T* ".join(f"({esc(ln)}) Tj" for ln in lines) + " ET"
        body.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> "
                     f"/Contents {5 + 2 * i} 0 R >>").encode())
        body.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode())
    if title is not None:
        body.append(f"<< /Title ({esc(title)}) >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, b in enumerate(body, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + b + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(body) + 1}\n".encode() + b"0000000000 65535 f \n"
    for o in offsets:
        out += f"{o:010d} 00000 n \n".encode()
    info = f" /Info {len(body)} 0 R" if title is not None else ""
    out += f"trailer\n<< /Size {len(body) + 1} /Root 1 0 R{info} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


MANUAL = make_pdf([TITLE, FRONT, PARAMS], title="Widgetron W200-W300 Control UG Issue 1.book")
MANUAL_V2 = make_pdf([TITLE[:-1] + ["Issue: 3"], FRONT, PARAMS], title="Widgetron W200-W300 Control UG Issue 1.book")
OTHER_PDF = make_pdf([OTHER])


@contextlib.contextmanager
def every_connection_closed(testcase):
    """Every SQLite connection opened inside the block must be closed when it ends. Left to
    garbage collection, a connection keeps the index file open on Windows (seen on Python
    3.15), and an open index cannot be rebuilt or cleaned up."""
    real, opened = sqlite3.connect, []

    def connect(*a, **k):
        db = real(*a, **k)
        opened.append(db)
        return db
    with mock.patch.object(sqlite3, "connect", connect):
        yield opened
    testcase.assertTrue(opened, "nothing opened an index")
    for db in opened:
        with testcase.assertRaises(sqlite3.ProgrammingError, msg="an index connection was left open"):
            db.execute("SELECT 1")


# ---- fake HTTP --------------------------------------------------------------
class FakeResponse:
    def __init__(self, body, status, headers, url):
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = email.message.Message()
        for k, v in headers.items():
            if v is not None:
                self.headers[k] = v
        self._url = url

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHTTP:
    """urllib.request.urlopen stand-in: routes by URL, records (method, url) calls."""

    def __init__(self):
        self.routes, self.calls = {}, []

    def serve(self, url, body=b"", status=200, etag=None, ctype="application/pdf", error=None, last_modified=None):
        self.routes[url] = {"body": body, "status": status, "etag": etag, "ctype": ctype, "error": error,
                            "last_modified": last_modified}

    def __call__(self, req, timeout=None):
        url, method = req.full_url, req.get_method()
        self.calls.append((method, url))
        r = self.routes.get(url)
        if r is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)
        if r["error"]:
            raise r["error"]
        if r["status"] >= 400:
            raise urllib.error.HTTPError(url, r["status"], "Blocked", email.message.Message(), None)
        headers = {"Content-Type": r["ctype"], "ETag": r["etag"], "Last-Modified": r["last_modified"],
                   "Content-Length": str(len(r["body"]))}
        return FakeResponse(b"" if method == "HEAD" else r["body"], r["status"], headers, url)


class Home(unittest.TestCase):
    """A private data folder per test, like the other unit tests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get(storage.ENV_HOME)
        os.environ[storage.ENV_HOME] = self.tmp.name
        storage.reset()
        self.http = FakeHTTP()
        self.patches = [mock.patch.object(urllib.request, "urlopen", self.http),
                        mock.patch.object(fetch.env_check, "network_available", lambda *a, **k: True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        if self.old is None:
            os.environ.pop(storage.ENV_HOME, None)
        else:
            os.environ[storage.ENV_HOME] = self.old
        storage.reset()
        self.tmp.cleanup()

    def write(self, name, data):
        p = Path(self.tmp.name) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def catalog(self):
        return json.loads(storage.catalog_path().read_text())["manuals"]


# ---- URLs -------------------------------------------------------------------
class CleanUrl(unittest.TestCase):
    def test_tracking_parameters_go_and_the_rest_stays_in_order(self):
        url = ("https://Vendor.Example/Docs/manual.pdf?id=3949&utm_source=mail&utm_medium=x&gclid=abc&lang=en"
               "&fbclid=1&_gl=2&mc_cid=3&mc_eid=4&ref=home#page=3")
        self.assertEqual(fetch.clean_url(url), "https://vendor.example/Docs/manual.pdf?id=3949&lang=en")

    def test_a_plain_url_is_unchanged_and_an_emptied_query_is_dropped(self):
        self.assertEqual(fetch.clean_url("https://a.example/m.pdf"), "https://a.example/m.pdf")
        self.assertEqual(fetch.clean_url("https://a.example/m.pdf?utm_campaign=c"), "https://a.example/m.pdf")
        self.assertEqual(fetch.clean_url("  https://a.example/m.pdf?x=1%2F2  "), "https://a.example/m.pdf?x=1%2F2")

    def test_not_a_url_is_returned_as_is(self):
        self.assertEqual(fetch.clean_url("manuals/c200.pdf"), "manuals/c200.pdf")


# ---- labels -----------------------------------------------------------------
class Labels(unittest.TestCase):
    FROM = {"min": "1.10", "max": None, "kind": "from", "text": "firmware V1.10 and later", "page": 2}
    BETWEEN = {"min": "2.0", "max": "2.5", "kind": "between", "text": "firmware 2.0 to 2.5", "page": 2}

    def test_the_three_labels(self):
        self.assertEqual(fetch.label_for("exact", self.FROM, "1.12")[0], "Verified")
        self.assertEqual(fetch.label_for("exact", None, "1.12")[0], "Likely")
        self.assertEqual(fetch.label_for("exact", self.BETWEEN, "3.0")[0], "Unsure")

    def test_firmware_unknown_is_likely_even_with_a_range(self):
        label, reasons, warning = fetch.label_for("exact", self.FROM, None)
        self.assertEqual(label, "Likely")
        self.assertTrue(any("unknown" in r for r in reasons))
        self.assertIsNone(warning)

    def test_manual_newer_than_the_device_warns_and_suggests_an_older_issue(self):
        label, reasons, warning = fetch.label_for("exact", self.FROM, "1.05")
        self.assertEqual(label, "Unsure")
        self.assertIn("older issue", warning)
        self.assertIn("may not exist", warning)

    def test_partial_match_doc_type_and_language_make_it_unsure(self):
        self.assertEqual(fetch.label_for("partial", None, None)[0], "Unsure")
        self.assertEqual(fetch.label_for("exact", None, None, doc_type_fits_=False)[0], "Unsure")
        self.assertEqual(fetch.label_for("exact", None, None, language_fits=False)[0], "Unsure")
        self.assertEqual(fetch.label_for("none", None, None)[0], "Unsure")

    def test_family_and_metadata_title_matches_cap_at_likely(self):
        self.assertEqual(fetch.label_for("family", self.FROM, "1.12")[0], "Likely")
        self.assertEqual(fetch.label_for("title", self.FROM, "1.12")[0], "Likely")

    def test_version_comparison_pads_and_ignores_leading_zeros(self):
        self.assertGreater(fetch.compare_versions("1.10", "1.9"), 0)
        self.assertEqual(fetch.compare_versions("01.07.01", "1.7.1"), 0)
        self.assertEqual(fetch.compare_versions("2.0", "2.0.0"), 0)
        self.assertLess(fetch.compare_versions("V2.3.1", "2.10"), 0)
        self.assertEqual(fetch.firmware_check(self.BETWEEN, "2.5"), "covered")
        self.assertEqual(fetch.firmware_check(self.BETWEEN, "1.9"), "newer_manual")
        self.assertEqual(fetch.firmware_check(self.BETWEEN, "2.6"), "older_manual")


# ---- model matching ----------------------------------------------------------
class ModelMatch(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(fetch.model_tokens("Widgetron W200"), ["widgetron", "w200"])
        self.assertEqual(fetch.model_tokens("AX8108-1000 series"), ["ax8108", "1000"])
        self.assertEqual(fetch.model_tokens("the model Unidrive"), ["unidrive"])

    def test_exact_match_with_evidence_line(self):
        m = fetch.match_model(["widgetron", "w200"], "\n".join(TITLE))
        self.assertEqual(m["kind"], "exact")
        self.assertEqual(m["evidence"], "Widgetron W200/")
        self.assertEqual(fetch.match_model(["widgetron", "w200"], "WIDGETRON w200 guide")["kind"], "exact")

    def test_placeholders_as_in_id_patterns(self):
        self.assertEqual(fetch.match_model(["ax8108"], "Narrow modules AX81x8")["kind"], "exact")
        self.assertEqual(fetch.match_model(["ax8108"], "AX8xxx servo")["kind"], "exact")
        self.assertEqual(fetch.token_match("ax8108", "ax81x8"), "placeholder")

    def test_family_name_with_trailing_zeros_and_variant_suffixes(self):
        self.assertEqual(fetch.match_model(["ax8108"], "AX8000 Multi-axis servo system")["kind"], "family")
        m = fetch.match_model(fetch.model_tokens("HCS01.1E-W0054"), "IndraDrive\nPower Sections HCS01")
        self.assertEqual(m["kind"], "family")
        self.assertEqual(m["evidence"], "Power Sections HCS01")
        self.assertEqual(m["missing"], ["1e", "w0054"])

    def test_partial_and_none(self):
        self.assertEqual(fetch.match_model(["widgetron", "w200"], "W200 drive")["kind"], "partial")
        self.assertEqual(fetch.match_model(["widgetron", "w200"], "Gizmotron G5 manual")["kind"], "none")
        self.assertEqual(fetch.match_model([], "anything")["kind"], "unknown")


# ---- verification from the front pages -----------------------------------------
class Verify(unittest.TestCase):
    PAGES = [(1, "\n".join(TITLE)), (2, "\n".join(FRONT))]
    META = "Widgetron W200-W300 Control UG Issue 1.book"

    def test_title_page_wins_over_stale_metadata(self):
        v = fetch.verify(self.PAGES, self.META, "Widgetron W200", firmware="1.12")
        self.assertEqual(v["label"], "Verified")
        self.assertEqual(v["version"], "Issue 2")          # the PDF title says Issue 1
        self.assertEqual(v["title"], "Control User Guide Widgetron W200/W300")
        self.assertEqual(v["doc_type"], "user-guide")
        self.assertEqual(v["date"], "2019-10")             # newest row of the revision history
        self.assertEqual(v["firmware_range"]["min"], "1.10")
        self.assertEqual(v["firmware_range"]["page"], 2)
        self.assertEqual(v["language"], "en")
        self.assertEqual([e["page"] for e in v["evidence"]], [1, 1, 2])
        self.assertIn("Widgetron W200/", v["evidence"][0]["text"])
        self.assertIn("Issue: 2", v["evidence"][1]["text"])

    def test_without_firmware_the_label_is_likely(self):
        v = fetch.verify(self.PAGES, self.META, "Widgetron W200")
        self.assertEqual(v["label"], "Likely")
        self.assertEqual(fetch.verify(self.PAGES, self.META, "Widgetron W200", firmware="1.0")["label"], "Unsure")

    def test_metadata_title_is_only_a_weak_fallback(self):
        pages = [(1, "Variable speed drive\nControl User Guide\nIssue: 4")]
        v = fetch.verify(pages, "Widgetron W200 Control User Guide", "Widgetron W200")
        self.assertEqual((v["model_match"], v["label"]), ("title", "Likely"))
        self.assertTrue(any("metadata" in r for r in v["reasons"]))
        self.assertEqual(fetch.verify(pages, "Other product", "Widgetron W200")["label"], "Unsure")

    def test_doc_type_detection_and_fit(self):
        for text, kind in [("Operating instructions | EN\nAX8000", "operating-instructions"),
                           ("Unidrive M\nParameter Reference Guide", "parameter-reference"),
                           ("Power Installation Guide", "installation"), ("Release Notes V2.1", "release-notes"),
                           ("Data sheet", "datasheet"), ("Instruction Manual", "operating-instructions"),
                           ("Control User Guide", "user-guide"), ("Drive Controllers\nHCS01", "other")]:
            self.assertEqual(fetch.detect_doc_type(text), kind, text)
        self.assertTrue(fetch.doc_type_fits("user-guide", "operating-instructions"))
        self.assertTrue(fetch.doc_type_fits(None, "installation"))
        self.assertTrue(fetch.doc_type_fits("installation", "other"))
        self.assertFalse(fetch.doc_type_fits("installation", "user-guide"))
        v = fetch.verify(self.PAGES, self.META, "Widgetron W200", doc_type="installation")
        self.assertEqual(v["label"], "Unsure")

    def test_firmware_range_statements(self):
        self.assertEqual(fetch.parse_firmware_range("software versions 2.0 to 2.3 are covered")["max"], "2.3")
        self.assertEqual(fetch.parse_firmware_range("valid from firmware V3.1")["kind"], "from")
        self.assertEqual(fetch.parse_firmware_range("applies to firmware 1.4")["kind"], "stated")
        self.assertIsNone(fetch.parse_firmware_range("11.029 Software Version 00.00.00 to 99.99.99"))
        self.assertIsNone(fetch.parse_firmware_range("no statement here"))
        self.assertEqual(fetch.range_text({"min": "1.10", "max": None, "kind": "from"}), "1.10 and later")

    def test_dates(self):
        self.assertEqual(fetch.find_date("Part Number: 0478-0535-02"), "")
        self.assertEqual(fetch.find_date("2026-04-22 | Version 2.3.1"), "2026-04-22")
        self.assertEqual(fetch.find_date("April 2019"), "2019-04")
        self.assertEqual(fetch.find_date("22.04.2026"), "2026-04-22")
        self.assertEqual(fetch.find_date("05/2012"), "2012-05")
        self.assertEqual(fetch.newest_date([(2, "IB01 2009-09\nIB07 2019-02"), (3, "2011-05")]), "2019-02")

    def test_language(self):
        en = " ".join(["the drive is connected to the supply and the motor"] * 5)
        de = " ".join(["der Umrichter ist mit der Versorgung und dem Motor zu verbinden"] * 5)
        self.assertEqual(fetch.detect_language(en), "en")
        self.assertEqual(fetch.detect_language(de), "de")
        self.assertEqual(fetch.detect_language("W200"), "unknown")
        v = fetch.verify([(1, "\n".join(TITLE) + "\n" + de)], "", "Widgetron W200", languages=["en"])
        self.assertEqual(v["label"], "Unsure")
        self.assertEqual(fetch.verify([(1, "\n".join(TITLE) + "\n" + de)], "", "Widgetron W200", languages=["de"])["label"], "Likely")


# ---- catalog ----------------------------------------------------------------
class Catalog(Home):
    def test_update_merges_models_keeps_values_and_bumps_last_used(self):
        e = fetch.catalog_update("a" * 16, {"title": "T", "models": ["Widgetron W200"], "label": "Likely"})
        self.assertIn("added", e)
        e = fetch.catalog_update("a" * 16, {"title": None, "models": ["W200 drive"], "label": "Verified"})
        self.assertEqual(e["title"], "T")                      # None leaves the stored value
        self.assertEqual(e["models"], ["W200 drive", "Widgetron W200"])
        self.assertEqual(e["label"], "Verified")
        time.strptime(e["last_used"], cleanup.ISO)
        self.assertEqual(set(fetch.catalog_read()["manuals"]), {"a" * 16})

    def test_candidates_match_models_and_relabel_with_the_firmware(self):
        fetch.catalog_update("a" * 16, {"title": "Control User Guide Widgetron W200/W300", "models": ["Widgetron W200"],
                                        "firmware_range": {"min": "1.10", "max": None, "kind": "from"},
                                        "doc_type": "user-guide", "language": "en", "url": "https://v.example/m.pdf"})
        fetch.catalog_update("b" * 16, {"title": "Gizmotron G5 Instruction Manual", "models": ["Gizmotron G5"]})
        c = fetch.catalog_candidates("Widgetron W200", None, "1.12", ["en"])
        self.assertEqual([x["doc_id"] for x in c], ["a" * 16])
        self.assertEqual(c[0]["label"], "Verified")
        self.assertEqual(fetch.catalog_candidates("Widgetron W200", None, None, ["en"])[0]["label"], "Likely")
        self.assertEqual(fetch.catalog_candidates("W300", None, None, ["en"])[0]["model_match"], "exact")  # title

    def test_freshness_decision(self):
        now = time.time()
        iso = lambda age: time.strftime(cleanup.ISO, time.localtime(now - age))  # noqa: E731
        self.assertTrue(fetch.needs_check({}, now))
        self.assertFalse(fetch.needs_check({"checked_at": iso(3600)}, now))
        self.assertTrue(fetch.needs_check({"checked_at": iso(2 * D)}, now))
        self.assertTrue(fetch.unchanged({"etag": "x"}, {"etag": "x"}))
        self.assertFalse(fetch.unchanged({"etag": "x"}, {"etag": "y"}))
        self.assertTrue(fetch.unchanged({"last_modified": "Mon"}, {"last_modified": "Mon", "etag": None}))
        self.assertIsNone(fetch.unchanged({"etag": "x"}, {"etag": None}))
        self.assertIsNone(fetch.unchanged({}, None))

    def test_resolve_bumps_last_used_and_stops_at_verified(self):
        old = time.strftime(cleanup.ISO, time.localtime(time.time() - 30 * D))
        storage.write_json(storage.catalog_path(), {"manuals": {"a" * 16: {
            "title": "Widgetron W200 user guide", "models": ["Widgetron W200"], "last_used": old, "added": old,
            "firmware_range": {"min": "1.10", "max": None, "kind": "from"}, "doc_type": "user-guide"}}})
        storage.write_json(storage.config_path(), {"manuals_dirs": [self.tmp.name]})
        self.write("manuals/widgetron-w200.pdf", MANUAL)
        r = fetch.resolve("Widgetron W200", firmware="1.12")
        self.assertEqual(r["label"], "Verified")
        self.assertEqual([c["source"] for c in r["candidates"]], ["catalog"])   # the local folder was not needed
        self.assertNotEqual(self.catalog()["a" * 16]["last_used"], old)
        self.assertNotIn("next", r)

    def test_two_copies_of_one_manual_are_one_candidate_not_close(self):
        for d, date, host in (("a", "2019-10", "cdn.example"), ("b", "2022-12", "vendor.example")):
            fetch.catalog_update(d * 16, {"title": "Control User Guide Widgetron W200/W300", "version": "Issue 2",
                                          "pages": 176, "date": date, "models": ["Widgetron W200"],
                                          "doc_type": "user-guide", "url": f"https://{host}/w200.pdf"})
        r = fetch.resolve("Widgetron W200")
        self.assertEqual(r["label"], "Likely")
        self.assertNotIn("close", r)
        self.assertEqual(len(r["candidates"]), 1)
        self.assertEqual(r["candidates"][0]["doc_id"], "b" * 16)          # the newest copy
        self.assertEqual(r["candidates"][0]["duplicates"][0]["doc_id"], "a" * 16)
        r = fetch.resolve("Widgetron W200", vendor="cdn.example")
        self.assertEqual(r["candidates"][0]["doc_id"], "a" * 16)          # the vendor's own host

    def test_close_candidates_make_the_result_unsure(self):
        for d in ("a", "b"):
            fetch.catalog_update(d * 16, {"title": f"Widgetron W200 guide {d}", "models": ["Widgetron W200"],
                                          "doc_type": "user-guide", "version": "Issue 2"})
        r = fetch.resolve("Widgetron W200")
        self.assertEqual(r["label"], "Unsure")
        self.assertTrue(r["close"])
        self.assertEqual(len(r["candidates"]), 2)
        self.assertTrue(r["ask_firmware"])


# ---- web search hand-off --------------------------------------------------------
class WebSearch(Home):
    def test_nothing_found_hands_over_with_queries(self):
        r = fetch.resolve("Widgetron W200")
        self.assertEqual(r["next"], "web_search")
        self.assertEqual(r["candidates"], [])
        self.assertIn("Widgetron W200 user guide pdf", r["queries"])
        self.assertIn("--url", r["then"])
        self.assertIsNone(r["label"])
        r = fetch.resolve("Widgetron W200", doc_type="parameter-reference", vendor="widgets.example")
        self.assertTrue(r["queries"][0].startswith("site:widgets.example Widgetron W200 parameter reference guide"))

    def test_cli_prints_one_json_object(self):
        out = subprocess.check_output([sys.executable, str(SCRIPT), "resolve", "--model", "Widgetron W200"], encoding="utf-8")
        self.assertEqual(json.loads(out)["next"], "web_search")
        p = subprocess.run([sys.executable, str(SCRIPT), "get"], capture_output=True, encoding="utf-8")
        self.assertNotEqual(p.returncode, 0)   # argparse: --url or --path is required


# ---- vendor maps (M6 builds them; M4 reads them) ---------------------------------
class VendorMaps(Home):
    def test_rows_naming_the_model_become_candidates_and_are_verified_by_download(self):
        storage.write_json(storage.vendors_dir() / "widgets.example.json", {"vendor": "widgets.example", "documents": [
            {"title": "Widgetron W200/W300 Control User Guide", "models": ["W200", "W300"], "doc_type": "user-guide",
             "language": "en", "issue": "2", "url": "https://widgets.example/w200.pdf?utm_source=map"},
            {"title": "Gizmotron G5 manual", "models": ["G5"], "url": "https://widgets.example/g5.pdf"}]})
        self.assertEqual(len(fetch.vendor_map_candidates("Widgetron W200", None, ["en"])), 1)
        if not HAVE_PDFIUM:
            return
        self.http.serve("https://widgets.example/w200.pdf", MANUAL, etag='"v2"')
        r = fetch.resolve("Widgetron W200")
        c = r["candidates"][0]
        self.assertEqual((c["source"], c["label"], c["vendor_map"]), ("vendor_map", "Likely", "widgets.example"))
        self.assertEqual(self.http.calls, [("GET", "https://widgets.example/w200.pdf")])   # clean URL
        self.assertIn(c["doc_id"], self.catalog())


# ---- local manuals folder ---------------------------------------------------------
@unittest.skipUnless(HAVE_PDFIUM, "pypdfium2 reads the title pages")
class LocalFolder(Home):
    def setUp(self):
        super().setUp()
        self.write("manuals/widgetron-w200-user-guide.pdf", MANUAL)
        self.write("manuals/sub/misc.pdf", OTHER_PDF)
        self.write("manuals/notes.txt", b"not a pdf")
        storage.write_json(storage.config_path(), {"manuals_dirs": [str(Path(self.tmp.name) / "manuals")]})

    def test_found_by_file_name(self):
        r = fetch.resolve("Widgetron W200")
        c = r["candidates"][0]
        self.assertEqual((c["source"], c["label"], c["name_match"]), ("local", "Likely", True))
        self.assertTrue(c["path"].endswith("widgetron-w200-user-guide.pdf"))
        self.assertEqual(c["version"], "Issue 2")
        self.assertTrue(r["ask_firmware"])
        self.assertNotIn("next", r)

    def test_found_by_title_page_when_no_file_name_matches(self):
        r = fetch.resolve("Gizmotron G5")
        c = r["candidates"][0]
        self.assertTrue(c["path"].endswith("misc.pdf"))
        self.assertEqual((c["name_match"], c["doc_type"], c["version"]), (False, "operating-instructions", "Edition 03"))
        self.assertEqual(len(r["candidates"]), 1)

    def test_unknown_model_hands_over_to_the_web(self):
        self.assertEqual(fetch.resolve("Thingamajig T9")["next"], "web_search")


# ---- get ----------------------------------------------------------------------
@unittest.skipUnless(HAVE_PDFIUM, "the index needs pypdfium2")
class Get(Home):
    URL = "https://widgets.example/docs/w200.pdf?id=7&utm_source=newsletter&gclid=1"
    CLEAN = "https://widgets.example/docs/w200.pdf?id=7"

    def setUp(self):
        super().setUp()
        self.http.serve(self.CLEAN, MANUAL, etag='"v2"', last_modified="Tue, 01 Oct 2019 10:00:00 GMT")

    def test_every_index_connection_is_closed_again(self):
        with every_connection_closed(self):
            r = fetch.get(url=self.URL, model="Widgetron W200")           # download, build, verify, hint
            self.assertEqual(r["firmware_hint"]["page"], 3)                 # the early-return path
            fetch.get(url=self.URL, model="Widgetron W200")               # cached: read_meta, index_front
            fetch.resolve("Widgetron W200")                                 # the catalog route
        with every_connection_closed(self):
            self.assertIsNone(fetch.firmware_hint(storage.index_path(r["doc_id"]).with_suffix(".missing")))
            (Path(self.tmp.name) / "not-an-index.sqlite").write_bytes(b"not sqlite")
            self.assertIsNone(fetch.firmware_hint(Path(self.tmp.name) / "not-an-index.sqlite"))

    def test_download_hash_index_and_catalog(self):
        r = fetch.get(url=self.URL, model="Widgetron W200")
        doc_id = r["doc_id"]
        self.assertEqual(doc_id, storage.doc_id_for(self.write("expected.pdf", MANUAL)))   # the content hash
        self.assertEqual(Path(r["pdf"]), storage.pdf_path(doc_id))
        self.assertTrue(Path(r["pdf"]).exists() and Path(r["index"]).exists())
        self.assertFalse(r["cached"])
        self.assertEqual(self.http.calls, [("GET", self.CLEAN)])          # tracking parameters never leave
        self.assertEqual((r["label"], r["version"], r["pages"]), ("Likely", "Issue 2", 3))
        self.assertEqual(r["firmware_hint"]["page"], 3)
        self.assertIn("11.029 Software Version", r["firmware_hint"]["text"])
        self.assertTrue(r["ask_firmware"])
        e = self.catalog()[doc_id]
        self.assertEqual((e["url"], e["etag"], e["label"], e["models"]), (self.CLEAN, '"v2"', "Likely", ["Widgetron W200"]))
        for k in ("checked_at", "added", "last_used"):
            time.strptime(e[k], cleanup.ISO)
        self.assertEqual(e["evidence"][0]["text"], "Widgetron W200/")
        self.assertEqual(r["cleanup"]["removed"], 0)
        import page
        db, meta, _ = storage.open_index(doc_id)
        db.close()                                                         # an open index blocks the temp folder's removal on Windows
        self.assertEqual(meta["source_url"], self.CLEAN)                   # what page.py link opens
        self.assertTrue(page.source_url(meta).startswith(self.CLEAN))

    def test_second_get_is_served_from_the_cache_without_network(self):
        fetch.get(url=self.URL, model="Widgetron W200")
        r = fetch.get(url=self.CLEAN)
        self.assertTrue(r["cached"])
        self.assertEqual(r["freshness"], "checked today")
        self.assertEqual(len(self.http.calls), 1)
        self.assertEqual(r["label"], "Likely")                              # from the catalog, no --model needed

    def test_daily_head_check_keeps_an_unchanged_file(self):
        r = fetch.get(url=self.URL)
        old = time.strftime(cleanup.ISO, time.localtime(time.time() - 2 * D))
        fetch.catalog_update(r["doc_id"], {"checked_at": old})
        r2 = fetch.get(url=self.URL)
        self.assertTrue(r2["cached"])
        self.assertEqual(self.http.calls[-1], ("HEAD", self.CLEAN))
        self.assertEqual(r2["freshness"], "unchanged")
        self.assertNotEqual(self.catalog()[r["doc_id"]]["checked_at"], old)
        self.assertEqual(len(self.http.calls), 2)

    def test_a_changed_file_gets_a_new_doc_id_and_the_old_one_is_left_alone(self):
        r = fetch.get(url=self.URL)
        fetch.catalog_update(r["doc_id"], {"checked_at": time.strftime(cleanup.ISO, time.localtime(time.time() - 2 * D))})
        self.http.serve(self.CLEAN, MANUAL_V2, etag='"v3"')
        r2 = fetch.get(url=self.URL, model="Widgetron W200")
        self.assertNotEqual(r2["doc_id"], r["doc_id"])
        self.assertEqual(r2["replaced"], r["doc_id"])
        self.assertEqual((r2["version"], r2["cached"]), ("Issue 3", False))
        self.assertEqual([m for m, _ in self.http.calls], ["GET", "HEAD", "GET"])
        self.assertTrue(Path(r["pdf"]).exists() and Path(r["index"]).exists())   # cleanup.py owns deletion
        self.assertEqual(set(self.catalog()), {r["doc_id"], r2["doc_id"]})

    def test_force_downloads_again(self):
        r = fetch.get(url=self.URL)
        r2 = fetch.get(url=self.URL, force=True)
        self.assertEqual((r2["doc_id"], r2["cached"], r2["index_cached"] if "index_cached" in r2 else True),
                         (r["doc_id"], False, True))
        self.assertEqual([m for m, _ in self.http.calls], ["GET", "GET"])

    def test_path_uses_the_users_file_in_place(self):
        p = self.write("mine/manual.pdf", MANUAL)
        r = fetch.get(path=str(p), model="Widgetron W200")
        self.assertEqual(Path(r["pdf"]), p.resolve())
        self.assertEqual(list(storage.pdf_dir().glob("*.pdf")), [])      # never copied
        self.assertTrue(Path(r["index"]).exists())
        e = self.catalog()[r["doc_id"]]
        self.assertEqual((e["path"], e.get("url")), (str(p.resolve()), None))
        self.assertEqual(self.http.calls, [])
        out = json.loads(subprocess.check_output([sys.executable, str(SCRIPT), "get", "--path", str(p)], encoding="utf-8"))
        self.assertTrue(out["cached"])

    def test_resolve_with_a_link_verifies_catalogs_and_is_found_next_time(self):
        r = fetch.resolve("Widgetron W200", url=self.URL)
        c = r["candidates"][0]
        self.assertEqual((c["source"], c["label"], c["model_match"], c["url"]), ("user", "Likely", "exact", self.CLEAN))
        self.assertEqual(r["firmware_hint"]["page"], 3)
        r2 = fetch.resolve("W300")
        self.assertEqual(r2["candidates"][0]["source"], "catalog")
        self.assertEqual(r2["candidates"][0]["doc_id"], c["doc_id"])
        self.assertEqual(len(self.http.calls), 1)

    def test_a_link_to_the_wrong_manual_is_unsure_and_the_search_goes_on(self):
        r = fetch.resolve("Gizmotron G5", url=self.URL)
        self.assertEqual((r["candidates"][0]["model_match"], r["label"]), ("none", "Unsure"))
        self.assertEqual(r["next"], "web_search")
        self.assertEqual(self.catalog()[r["candidates"][0]["doc_id"]]["models"], [])   # not recorded as a G5 manual


class DownloadErrors(Home):
    URL = "https://widgets.example/w200.pdf"

    def handled(self, **kw):
        with self.assertRaises(fetch.Handled) as cm:
            fetch.get(**kw)
        return cm.exception

    def test_403_and_bot_checks_are_download_blocked(self):
        self.http.serve(self.URL, status=403)
        e = self.handled(url=self.URL)
        self.assertEqual(e.error, "download_blocked")
        self.assertIn("web fetch", e.fallback)
        self.assertIn("--path", e.fallback)
        self.http.serve(self.URL, status=429)
        self.assertEqual(self.handled(url=self.URL).error, "download_blocked")

    def test_a_web_page_instead_of_a_pdf(self):
        self.http.serve(self.URL, b"<!DOCTYPE html><html><body>Please verify you are human</body></html>", ctype="text/html")
        e = self.handled(url=self.URL)
        self.assertEqual(e.error, "not_a_pdf")
        self.assertIn("web page", e.message)
        self.assertEqual(list(storage.pdf_dir().glob("*")), [])   # nothing kept

    def test_no_network_and_other_failures(self):
        self.http.serve(self.URL, error=urllib.error.URLError("no route to host"))
        with mock.patch.object(fetch.env_check, "network_available", lambda *a, **k: False):
            e = self.handled(url=self.URL)
        self.assertEqual(e.error, "no_network")
        self.assertIn("manuals_dirs", e.fallback)
        self.assertEqual(self.handled(url=self.URL).error, "download_failed")
        self.http.serve(self.URL, status=404)
        self.assertEqual(self.handled(url=self.URL).error, "not_found")
        self.assertEqual(self.handled(url="ftp://x/y.pdf").error, "bad_url")
        self.assertEqual(self.handled(path=str(Path(self.tmp.name) / "missing.pdf")).error, "no_file")
        self.assertEqual(self.handled(path=str(self.write("x.pdf", b"not a pdf"))).error, "not_a_pdf")

    def test_the_cli_prints_the_handled_error_with_exit_code_0(self):
        p = subprocess.run([sys.executable, str(SCRIPT), "get", "--path", str(Path(self.tmp.name) / "nope.pdf")],
                           capture_output=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["error"], "no_file")

    @unittest.skipUnless(HAVE_PDFIUM, "the index needs pypdfium2")
    def test_offline_with_a_cached_copy_still_works(self):
        self.http.serve(self.URL, MANUAL, etag='"v2"')
        r = fetch.get(url=self.URL, model="Widgetron W200")
        fetch.catalog_update(r["doc_id"], {"checked_at": time.strftime(cleanup.ISO, time.localtime(time.time() - 2 * D))})
        self.http.serve(self.URL, error=urllib.error.URLError("no route to host"))
        with mock.patch.object(fetch.env_check, "network_available", lambda *a, **k: False):
            r2 = fetch.get(url=self.URL)
        self.assertTrue(r2["cached"])
        self.assertEqual(r2["freshness"], "offline, not checked")


class Duplicates(unittest.TestCase):
    """resolve: two copies of one manual (same title, issue and page count) are one candidate."""

    def cand(self, **kw):
        c = {"source": "catalog", "label": "Likely", "title": "Control User Guide Widgetron W200/W300",
             "version": "Issue 2", "pages": 176, "model_match": "exact", "date": "2019-10",
             "url": "https://cdn.example/w200.pdf", "doc_id": "a" * 16, "last_used": "2026-10-22T22:21:35"}
        c.update(kw)
        return c

    def test_same_title_issue_and_pages_collapse_to_the_newest(self):
        old = self.cand()
        new = self.cand(date="2022-12", url="https://vendor.example/w200.pdf", doc_id="b" * 16)
        out = fetch.collapse_duplicates([old, new])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["doc_id"], "b" * 16)
        self.assertEqual([d["doc_id"] for d in out[0]["duplicates"]], ["a" * 16])
        self.assertEqual(out[0]["duplicates"][0]["url"], "https://cdn.example/w200.pdf")

    def test_the_vendor_host_wins_over_a_newer_mirror(self):
        old = self.cand()
        new = self.cand(date="2022-12", url="https://vendor.example/w200.pdf", doc_id="b" * 16)
        out = fetch.collapse_duplicates([new, old], vendor="cdn.example")
        self.assertEqual(out[0]["doc_id"], "a" * 16)
        self.assertEqual(out[0]["duplicates"][0]["doc_id"], "b" * 16)

    def test_the_better_label_wins_over_the_newer_date(self):
        unsure = self.cand(label="Unsure", date="2024-01", doc_id="c" * 16)
        likely = self.cand()
        out = fetch.collapse_duplicates([unsure, likely])
        self.assertEqual(out[0]["doc_id"], "a" * 16)

    def test_another_issue_or_page_count_stays_apart(self):
        self.assertEqual(len(fetch.collapse_duplicates([self.cand(), self.cand(version="Issue 3", doc_id="d" * 16)])), 2)
        self.assertEqual(len(fetch.collapse_duplicates([self.cand(), self.cand(pages=180, doc_id="e" * 16)])), 2)
        self.assertNotIn("duplicates", fetch.collapse_duplicates([self.cand()])[0])

    def test_without_a_title_or_issue_nothing_collapses(self):
        self.assertEqual(len(fetch.collapse_duplicates([self.cand(title=""), self.cand(title="")])), 2)
        self.assertEqual(len(fetch.collapse_duplicates([self.cand(version=""), self.cand(version="")])), 2)


if __name__ == "__main__":
    unittest.main()
