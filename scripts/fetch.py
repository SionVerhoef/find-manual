#!/usr/bin/env python3
"""Find, download, verify and catalog the manual for a model. Prints one JSON object.

Usage: fetch.py resolve --model M [--doc-type T] [--lang L] [--url U | --path P] [--firmware V] [--vendor DOMAIN]
       fetch.py get --url U | --path P [--force] [--model M] [--doc-type T] [--firmware V]

resolve looks, in this order, at the link or file given (--url / --path), the catalog of
manuals used before, the vendor document maps in <data>/vendors/, and the local manuals
folders from the settings. It stops at the first Verified candidate; otherwise every
candidate comes back, best first. When nothing matches it returns next: "web_search" with
ready-made queries: the agent searches with its own tool (the vendor's own domain first)
and calls resolve again with --url for each candidate link, which downloads, verifies and
labels it.

Verification reads the document itself, never a listing: the title page and the front
matter (the first pages, revision history). The model's tokens must appear on the title
page (placeholders as in reference/id-patterns.md; the PDF metadata title only as a
weaker fallback, it is often stale), the doc type must fit, a stated firmware range must
cover --firmware, the issue/edition and date are read from the title page, and the
language must be one of manual_languages. Labels: Verified (model and firmware range
stated and covering the device), Likely (model matches, no range stated or device
firmware unknown), Unsure (partial match, doc type or firmware mismatch, close candidates).

get downloads a private working copy to the cache (a file given with --path is used in
place, never copied), builds or reuses the index, and writes the catalog entry.
Tracking parameters are stripped from every URL; the clean URL is stored with the index
and is what page.py link opens. A cached URL gets at most one HEAD check a day (ETag,
Last-Modified); a changed file is downloaded again and gets a new doc_id and index, the
old ones are left to cleanup.py, which runs first and is the only thing that deletes.
"""
import argparse
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_index  # noqa: E402
import cleanup  # noqa: E402
import env_check  # noqa: E402
import storage  # noqa: E402
from patterns import VERSION_RE  # noqa: E402

try:
    import pypdfium2 as pdfium
except ImportError:  # reported as a handled error where a PDF has to be read
    pdfium = None

USER_AGENT = "find-manual/0.1 (+https://github.com/SionVerhoef/find-manual)"
TIMEOUT_S = 60
CHUNK = 1 << 20
FRESH_S = 86400            # a cached URL is checked (HEAD) at most once a day
FRONT_PAGES = 6            # title page plus front matter read for verification
ISO = "%Y-%m-%dT%H:%M:%S"
MAX_LOCAL_FILES = 2000     # PDFs listed per manuals folder
LOCAL_TITLE_PAGES = 300    # local PDFs whose title page is read when no file name matches
EVIDENCE_CHARS = 120
VERIFY_FROM_MAPS = 2       # vendor-map rows downloaded for verification per lookup

LABELS = ("Verified", "Likely", "Unsure")
LABEL_RANK = {"Verified": 0, "Likely": 1, "Unsure": 2, None: 3}
MATCH_RANK = {"exact": 0, "family": 1, "title": 2, "partial": 3, "unknown": 4, "none": 5}

DOC_TYPES = ["user-guide", "parameter-reference", "installation", "operating-instructions",
             "datasheet", "release-notes", "other"]
# Detected from the title page; the first pattern that matches wins.
DOC_TYPE_RES = [
    ("parameter-reference", re.compile(r"(?i)\bparameters?\s+(reference|guide|description|descriptions|list|manual)\b")),
    ("release-notes", re.compile(r"(?i)\brelease\s+notes?\b|\bfirmware\s+(release|history)\b|\bversion\s+history\b|\bchange\s?log\b")),
    ("datasheet", re.compile(r"(?i)\bdata\s?sheet\b|\btechnical\s+data\b|\bproduct\s+data\b")),
    ("user-guide", re.compile(r"(?i)\buser'?s?\s+(guide|manual|handbook)\b|\bowner'?s?\s+manual\b|\boperating\s+manual\b"
                              r"|\bprogramming\s+(guide|manual)\b|\breference\s+(guide|manual)\b")),
    ("installation", re.compile(r"(?i)\b(installation|mounting|assembly)\s+(guide|manual|instructions?)\b"
                                r"|\bhardware\s+(installation|manual)\b|\bcommissioning\s+(guide|manual)\b")),
    ("operating-instructions", re.compile(r"(?i)\boperating\s+instructions?\b|\boperation\s+(manual|instructions?)\b"
                                          r"|\binstruction\s+manual\b|\binstructions?\s+for\s+use\b")),
    ("user-guide", re.compile(r"(?i)\bmanual\b|\bguide\b|\bhandbook\b")),
]
COMPATIBLE = {("user-guide", "operating-instructions"), ("operating-instructions", "user-guide")}
DOC_WORDS = {"user-guide": "user guide", "parameter-reference": "parameter reference guide",
             "installation": "installation guide", "operating-instructions": "operating instructions",
             "datasheet": "datasheet", "release-notes": "release notes", "other": "manual"}

TRACKING_RE = re.compile(r"(?i)^(utm_\w*|gclid|gclsrc|dclid|gbraid|wbraid|fbclid|msclkid|twclid|ttclid|yclid|igshid"
                         r"|li_fat_id|mc_cid|mc_eid|mkt_tok|_gl|_ga|_gid|_hsenc|_hsmi|hsa_\w*|ref|ref_src|ref_url"
                         r"|referrer|_branch_match_id|s_kwcid|ef_id|vero_id|oly_anon_id|oly_enc_id|spm|_openstat)$")

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
GENERIC_WORDS = {"series", "model", "type", "the", "a", "an", "of", "for", "and", "with", "version", "v", "my"}

# Firmware range statements in the front matter, generic forms only.
_FW = r"(?:firmware|software|system software|drive software)(?:\s+version)?s?"
_V = r"[Vv]?\s?(\d+(?:\.\d+)+)"
FW_RANGE_RES = [
    ("between", re.compile(rf"(?i)\b{_FW}\s*[:]?\s*{_V}\s*(?:to|through|up to|[-–])\s*{_V}")),
    ("from", re.compile(rf"(?i)\b{_FW}\s*[:]?\s*{_V}\s*(?:and|or)\s+(?:later|higher|above|newer|greater|onwards?)")),
    ("from", re.compile(rf"(?i)\b(?:from|as of|starting (?:with|from)|since)\s+{_FW}\s*[:]?\s*{_V}")),
    ("from", re.compile(rf"(?i)\b{_FW}\s*(?:>=|≥)\s*{_V}")),
    ("stated", re.compile(rf"(?i)\b(?:valid|applies|applicable|refers|relates|corresponds)\s+(?:for|to)\s+{_FW}\s*[:]?\s*{_V}")),
    ("stated", re.compile(rf"(?i)\b{_FW}\s*:\s*{_V}")),
]
DATE_RES = [  # each yields (year, month, day or None); years outside 1990-2099 are ignored
    re.compile(r"\b((?:19|20)\d{2})-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?\b"),
    re.compile(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+((?:19|20)\d{2})\b"),
    re.compile(r"\b(0?[1-9]|1[0-2])/((?:19|20)\d{2})\b"),
    re.compile(r"\b(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.((?:19|20)\d{2})\b"),
]
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
TITLE_STOP_RE = re.compile(r"(?i)^(part\s*(number|no\.?)|order\s*(number|no\.?)|issue|edition|version|revision|rev\.?"
                           r"|document|doc\.?\s*(no|number)|date|catalog(ue)?\s*(no|number)|\d{4}-\d{2})\b")
HINT_NAME_RE = re.compile(r"(?i)\b(software|firmware)\s+version\b")
HINT_READ_RE = re.compile(r"(?i)(display|show|read|indicat|view)\w*\s+(the\s+)?(current\s+)?(software|firmware)\s+version"
                          r"|(software|firmware)\s+version\s+(of|in|on)\s+the")
LANG_WORDS = {
    "en": {"the", "and", "of", "to", "for", "with", "this", "that", "is", "are", "on", "from", "by", "not"},
    "de": {"der", "die", "das", "und", "nicht", "mit", "für", "ist", "sind", "ein", "eine", "von", "zu", "bei"},
    "fr": {"le", "la", "les", "des", "et", "pour", "avec", "est", "sont", "une", "dans", "sur", "pas"},
    "es": {"el", "los", "las", "y", "para", "con", "es", "son", "una", "del", "en", "por"},
    "it": {"il", "gli", "le", "e", "per", "con", "è", "sono", "una", "del", "nel", "che"},
    "nl": {"het", "een", "en", "van", "voor", "met", "is", "zijn", "niet", "op", "dit", "wordt"},
}
BLOCKED_FALLBACK = ("fetch the link with the agent's own web fetch tool and save the PDF, or ask the user to download "
                    "it and give the path; then run scripts/fetch.py get --path <file>")
NO_NETWORK_FALLBACK = ("use a local manuals folder (scripts/settings.py add manuals_dirs <path>), the cached index of "
                       "this manual, or a PDF the user attaches (scripts/fetch.py get --path <file>)")


class Handled(Exception):
    """A handled error: main() prints it as {"error", "message", "fallback"} with exit code 0."""

    def __init__(self, error, message, fallback):
        super().__init__(message)
        self.error, self.message, self.fallback = error, message, fallback

    def as_dict(self):
        return {"error": self.error, "message": self.message, "fallback": self.fallback}


def now_iso(t=None):
    return time.strftime(ISO, time.localtime(t if t is not None else time.time()))


# ---- URLs -------------------------------------------------------------------
def clean_url(url):
    """Strip tracking parameters (utm_*, gclid, fbclid, _gl, mc_cid, ref, ...) and the
    fragment; lower-case the scheme and host; leave every other part exactly as given."""
    url = (url or "").strip()
    p = urllib.parse.urlsplit(url)
    if not p.scheme or not p.netloc:
        return url
    keep = []
    for part in p.query.split("&"):
        if not part:
            continue
        key = urllib.parse.unquote_plus(part.split("=", 1)[0])
        if not TRACKING_RE.match(key):
            keep.append(part)
    netloc = p.netloc if "@" in p.netloc else p.netloc.lower()
    return urllib.parse.urlunsplit((p.scheme.lower(), netloc, p.path or "/", "&".join(keep), ""))


def host_of(url):
    return urllib.parse.urlsplit(url).hostname or ""


# ---- versions ---------------------------------------------------------------
def version_tuple(v):
    return tuple(int(n) for n in re.findall(r"\d+", str(v or "")))


def compare_versions(a, b):
    """-1, 0, 1 for a < b, a == b, a > b; shorter tuples are padded with zeros."""
    ta, tb = version_tuple(a), version_tuple(b)
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


def parse_firmware_range(text, page=None):
    """The first firmware range statement in `text`: {"min", "max", "kind", "text", "page"} or None.
    kind: between (min to max), from (min and later), stated (one version named)."""
    for kind, rx in FW_RANGE_RES:
        m = rx.search(text)
        if not m:
            continue
        lo = m.group(1)
        hi = m.group(2) if kind == "between" else None
        if not any(version_tuple(lo)) or (hi and all(n == 99 for n in version_tuple(hi))):
            continue   # "00.00.00 to 99.99.99" is a parameter's value range, not a firmware range
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.end())
        line = text[start:end if end >= 0 else None].strip()
        return {"min": lo, "max": hi, "kind": kind, "text": shorten(line or m.group(0)), "page": page}
    return None


def firmware_check(rng, firmware):
    """covered | newer_manual (device older than the range) | older_manual | unknown."""
    if not rng or not firmware:
        return "unknown"
    if compare_versions(firmware, rng["min"]) < 0:
        return "newer_manual"
    if rng.get("max") and compare_versions(firmware, rng["max"]) > 0:
        return "older_manual"
    return "covered"


# ---- model matching ---------------------------------------------------------
def model_tokens(model):
    toks = [t.lower() for t in TOKEN_RE.findall(model or "")]
    return [t for t in toks if t not in GENERIC_WORDS]


def token_match(q, d):
    """How one query token matches one document token: exact, placeholder (AX8108 ~ AX81x8),
    family (AX8108 ~ AX8000: a family name with trailing zeros), or None."""
    if q == d:
        return "exact"
    if len(q) != len(d) or not re.search(r"\d", q):
        return None
    if "x" in d and re.search(r"\d", d) and all(a == b or b == "x" for a, b in zip(q, d)):
        return "placeholder"
    m = re.match(r"^(.*?)(00+)$", d)
    if m and re.search(r"[a-z]", m.group(1)) and re.search(r"\d", m.group(1)) and q.startswith(m.group(1)):
        return "family"
    return None


def match_model(tokens, text):
    """Match the model's tokens against a text (title page, file name, PDF title, catalog models).

    Returns {"kind": exact|family|partial|none, "matched": [...], "missing": [...], "evidence": line}.
    exact: every token found as is or through an x-placeholder; family: every token found, at
    least one through a family name with trailing zeros; partial: at least one token with digits
    found; none: nothing with digits found."""
    if not tokens:
        return {"kind": "unknown", "matched": [], "missing": [], "evidence": ""}
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text or "") if ln.strip()]
    words = {w.lower() for ln in lines for w in TOKEN_RE.findall(ln)}
    matched, missing, kinds, evidence = [], [], set(), ""
    digit_tokens = [t for t in tokens if re.search(r"\d", t)]
    for q in tokens:
        best = None
        for d in words:
            k = token_match(q, d)
            if k and (best is None or MATCH_RANK.get(k, 9) < MATCH_RANK.get(best[0], 9)):
                best = (k, d)
        if best:
            matched.append(q)
            kinds.add(best[0])
            if not evidence or q == (digit_tokens or [None])[0]:   # the line with the model number, else the first match
                evidence = next((ln for ln in lines if best[1] in {w.lower() for w in TOKEN_RE.findall(ln)}), "")
        else:
            missing.append(q)
    base = tokens.index(digit_tokens[0]) if digit_tokens else None
    if not missing:
        kind = "family" if "family" in kinds else "exact"
    elif base is not None and digit_tokens[0] in matched and all(tokens.index(t) > base for t in missing):
        kind = "family"   # the base model number matched; only a variant suffix (HCS01.1E-W0054) is unnamed
    elif any(t in matched for t in digit_tokens) or (not digit_tokens and len(matched) >= max(1, len(tokens) // 2)):
        kind = "partial"
    else:
        kind = "none"
    return {"kind": kind, "matched": matched, "missing": missing, "evidence": shorten(evidence)}


# ---- reading the front of a document -------------------------------------------
def shorten(text, n=EVIDENCE_CHARS):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def is_pdf_file(path):
    try:
        with open(path, "rb") as f:
            return b"%PDF" in f.read(1024)
    except OSError:
        return False


def pdf_front(pdf_path, n=FRONT_PAGES):
    """([(page, text)], metadata dict, page count) of the first n pages, read with pypdfium2."""
    if pdfium is None:
        raise Handled("no_pdf_lib", "pypdfium2 is not installed",
                      f"run: {storage.pip_hint()} — then retry; see reference/fallbacks.md")
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
    except Exception as e:  # pdfium.PdfiumError, or an unreadable file
        raise Handled("bad_pdf", f"{Path(pdf_path).name}: {e}",
                      "the file is not a readable PDF (encrypted or damaged): download it again or ask the user for another copy")
    pages = []
    try:
        count = len(doc)
        for i in range(min(n, count)):
            try:
                text = doc[i].get_textpage().get_text_range()
            except Exception:
                text = ""
            pages.append((i + 1, text))
        meta = doc.get_metadata_dict()
    finally:
        doc.close()
    return pages, meta, count


def index_front(index_path, n=FRONT_PAGES):
    """The same from an index, for when the working copy is gone."""
    db = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT page, z FROM pages WHERE page<=? ORDER BY page", (n,)).fetchall()
        meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
    finally:
        db.close()
    pages = [(p, zlib.decompress(z).decode()) for p, z in rows]
    return pages, {"Title": meta.get("title_meta", "")}, int(meta.get("pages") or 0)


def title_page(pages):
    """(page number, text) of the first page with real text: covers are often an image."""
    for p, text in pages:
        if len(TOKEN_RE.findall(text)) >= 4:
            return p, text
    return (pages[0] if pages else (1, ""))


def detect_doc_type(text):
    for name, rx in DOC_TYPE_RES:
        if rx.search(text or ""):
            return name
    return "other"


def doc_type_fits(requested, detected):
    if not requested or requested == "other" or detected in (None, "other"):
        return True
    return requested == detected or (requested, detected) in COMPATIBLE


def find_issue(pages):
    """'Issue 2', 'Version 2.3.1', 'Edition 07' from the title page, else from the next pages."""
    for p, text in pages:
        m = VERSION_RE.search(text)
        if m:
            return f"{m.group(1).capitalize()} {m.group(2)}"
    return ""


def all_dates(text):
    """Every date in the text as YYYY-MM or YYYY-MM-DD, in order of appearance."""
    found = []
    for i, rx in enumerate(DATE_RES):
        for m in rx.finditer(text or ""):
            g = m.groups()
            if i == 0:
                found.append((m.start(), "-".join(x for x in g if x)))
            elif i == 1:
                found.append((m.start(), f"{g[1]}-{MONTHS[g[0].lower()[:3]]:02d}"))
            elif i == 2:
                found.append((m.start(), f"{g[1]}-{int(g[0]):02d}"))
            else:
                found.append((m.start(), f"{g[2]}-{int(g[1]):02d}-{int(g[0]):02d}"))
    return [d for _, d in sorted(found)]


def find_date(text):
    """The first date on a page (the title page states one date)."""
    dates = all_dates(text)
    return dates[0] if dates else ""


def newest_date(pages):
    """The newest date in the front matter: the last row of a revision history."""
    dates = [d for _, text in pages for d in all_dates(text)]
    return max(dates) if dates else ""


def detect_language(text):
    words = [w.lower() for w in re.findall(r"[^\W\d_]+", text or "")]
    if len(words) < 20:
        return "unknown"
    best, score = "unknown", 0
    for lang, vocab in LANG_WORDS.items():
        n = sum(1 for w in words if w in vocab)
        if n > score:
            best, score = lang, n
    return best if score >= max(3, len(words) * 0.01) else "unknown"


def title_from(text, tokens, doc_type_hit=None):
    """A short title from the title page: the lines down to the model and doc type, plus a
    continuation line, stopping at part numbers, issue and date lines."""
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text or "") if ln.strip()][:8]
    if not lines:
        return ""
    needed = 0
    for i, ln in enumerate(lines[:6]):
        low = {w.lower() for w in TOKEN_RE.findall(ln)}
        if any(token_match(t, w) for t in tokens for w in low) or detect_doc_type(ln) != "other":
            needed = i
    chosen = []
    for i, ln in enumerate(lines):
        if TITLE_STOP_RE.match(ln):
            break
        chosen.append(ln)
        if i >= needed:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt and not TITLE_STOP_RE.match(nxt) and len(nxt) <= 60 and (
                    ln.endswith(("/", ",", "-", "and", "&")) or re.search(r"[a-z]+\s+[a-z]+", nxt)):
                chosen.append(nxt)
            break
    title = re.sub(r"/\s+", "/", " ".join(chosen))
    return shorten(title, 120)


# ---- labels -----------------------------------------------------------------
def label_for(model_match, firmware_range, firmware, doc_type_fits_=True, language_fits=True):
    """(label, reasons, warning). Verified: model and firmware range stated and covering the
    device. Likely: model matches, no range stated or device firmware unknown. Unsure: a
    partial model match, a doc type or language that does not fit, or a firmware mismatch."""
    reasons, warning = [], None
    if model_match in ("none", "unknown"):
        return "Unsure", ["model not found on the title page" if model_match == "none" else "no model given"], None
    if model_match == "partial":
        reasons.append("only part of the model name is on the title page")
    elif model_match == "family":
        reasons.append("matched through the product family or base model name")
    elif model_match == "title":
        reasons.append("model found in the PDF metadata title only, not on the title page")
    if not doc_type_fits_:
        reasons.append("document type does not fit the question")
    if not language_fits:
        reasons.append("language is not one of manual_languages")
    unsure = bool(reasons) and model_match not in ("family", "title")   # those cap at Likely instead
    if firmware_range:
        check = firmware_check(firmware_range, firmware)
        if check == "covered":
            reasons.append(f"firmware range {range_text(firmware_range)} covers {firmware}")
            label = "Unsure" if unsure else ("Likely" if model_match in ("family", "title") else "Verified")
        elif check == "unknown":
            reasons.append(f"firmware range {range_text(firmware_range)} stated; device firmware unknown")
            label = "Unsure" if unsure else "Likely"
        elif check == "newer_manual":
            reasons.append(f"manual is for firmware {range_text(firmware_range)}, newer than the device's {firmware}")
            warning = ("this manual is newer than the device's firmware: look for an older issue in the vendor "
                       "archive; if there is none, some functions it describes may not exist on this device")
            label = "Unsure"
        else:
            reasons.append(f"manual is for firmware {range_text(firmware_range)}, older than the device's {firmware}")
            label = "Unsure"
    else:
        reasons.append("no firmware range stated")
        label = "Unsure" if unsure else "Likely"
    return label, reasons, warning


def range_text(rng):
    if not rng:
        return ""
    if rng.get("max"):
        return f"{rng['min']} to {rng['max']}"
    return f"{rng['min']} and later" if rng.get("kind") == "from" else f"{rng['min']}"


# ---- verification -----------------------------------------------------------
def verify(pages, meta_title, model, doc_type=None, firmware=None, languages=("en",)):
    """Verify a document from its own front pages: [(page, text)] plus the PDF metadata title.

    Returns title, version, date, model_match, doc_type, doc_type_fits, firmware_range,
    language, label, reasons, evidence, warning. Evidence holds the page and the short
    wording each check rested on."""
    tokens = model_tokens(model)
    tp, ttext = title_page(pages)
    mm = match_model(tokens, ttext)
    evidence = []
    if mm["kind"] in ("none", "partial") and meta_title:
        mt = match_model(tokens, meta_title)
        if mt["kind"] in ("exact", "family"):
            mm = dict(mt, kind="title", evidence=shorten("PDF title: " + meta_title))
    if mm["evidence"]:
        evidence.append({"page": tp if mm["kind"] != "title" else None, "text": mm["evidence"]})
    detected = detect_doc_type(ttext)
    if detected == "other" and meta_title:
        detected = detect_doc_type(meta_title)
    fits = doc_type_fits(doc_type, detected)
    version = find_issue([(tp, ttext)] + [(p, t) for p, t in pages if p != tp])
    if version:
        m = VERSION_RE.search(ttext)
        src = (tp, ttext) if m else next(((p, t) for p, t in pages if VERSION_RE.search(t)), (tp, ttext))
        m = m or VERSION_RE.search(src[1])
        line = src[1][src[1].rfind("\n", 0, m.start()) + 1:]
        evidence.append({"page": src[0], "text": shorten(line.split("\n", 1)[0])})
    date = find_date(ttext) or newest_date([(p, t) for p, t in pages if p != tp])
    rng = None
    for p, text in [(tp, ttext)] + [(p, t) for p, t in pages if p != tp]:
        rng = parse_firmware_range(text, page=p)
        if rng:
            evidence.append({"page": p, "text": rng["text"]})
            break
    lang = detect_language("\n".join(t for _, t in pages))
    lang_fits = lang == "unknown" or lang in (languages or ("en",))
    label, reasons, warning = label_for(mm["kind"], rng, firmware, fits, lang_fits)
    return {"title": title_from(ttext, tokens), "version": version, "date": date,
            "model_match": mm["kind"], "model_missing": mm["missing"], "doc_type": detected,
            "doc_type_fits": fits, "firmware_range": rng, "language": lang, "label": label,
            "reasons": reasons, "evidence": evidence, "warning": warning, "title_page": tp}


def firmware_hint(index_path):
    """Where the manual says to read the device's software version: a parameter whose name is
    the software/firmware version, else a sentence that says where it is displayed."""
    if not index_path or not Path(index_path).exists():
        return None
    db = None
    try:
        db = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT b.page, b.kind, b.off, b.len FROM anchors a JOIN blocks b ON b.id=a.block "
            "WHERE a.kind='firmware' ORDER BY (b.kind='record') DESC, b.page").fetchall()
        labels = dict(db.execute("SELECT page, label FROM pages").fetchall())
        texts = {}
        fallback = None
        for page, kind, off, ln in rows:
            if page not in texts:
                z, = db.execute("SELECT z FROM pages WHERE page=?", (page,)).fetchone()
                texts[page] = zlib.decompress(z).decode()
            text = texts[page][off:off + ln]
            first = text.split("\n", 1)[0]
            if kind == "record" and HINT_NAME_RE.search(first):
                return {"page": page, "label": labels.get(page, str(page)), "text": shorten(first, 100),
                        "note": "the manual's parameter that shows the device's software version"}
            if fallback is None and HINT_READ_RE.search(text):
                m = HINT_READ_RE.search(text)
                start = max(text.rfind(".", 0, m.start()) + 1, text.rfind("\n", 0, m.start()) + 1)
                fallback = {"page": page, "label": labels.get(page, str(page)),
                            "text": shorten(text[start:start + 160], 100),
                            "note": "where the manual says the software version is shown"}
        return fallback
    except sqlite3.Error:
        return None
    finally:
        if db is not None:
            db.close()   # explicitly: a connection left to garbage collection keeps the index open on Windows


# ---- catalog ----------------------------------------------------------------
def catalog_read():
    cat = storage.read_json(storage.catalog_path())
    if not isinstance(cat, dict) or not isinstance(cat.get("manuals"), dict):
        cat = {"manuals": {}}
    return cat


def catalog_write(cat):
    """Caller holds the lock."""
    storage.write_json(storage.catalog_path(), cat)


def catalog_update(doc_id, fields, lock=True):
    """Merge `fields` into the entry (None leaves the stored value), bump last_used, write."""
    def _do():
        cat = catalog_read()
        entry = cat["manuals"].get(doc_id)
        if not isinstance(entry, dict):
            entry = {"added": now_iso()}
        for k, v in fields.items():
            if k == "models":
                entry["models"] = sorted(set(entry.get("models") or []) | set(v or []))
            elif v is not None or k not in entry:
                entry[k] = v
        entry["last_used"] = now_iso()
        cat["manuals"][doc_id] = entry
        catalog_write(cat)
        return entry
    if lock:
        with storage.lock():
            return _do()
    return _do()


def catalog_touch(doc_ids):
    """Bump last_used of the entries that were consulted; skips when another tool holds the lock."""
    if not doc_ids:
        return
    try:
        with storage.lock(timeout=2, busy="raise"):
            cat = catalog_read()
            for d in doc_ids:
                if isinstance(cat["manuals"].get(d), dict):
                    cat["manuals"][d]["last_used"] = now_iso()
            catalog_write(cat)
    except storage.Locked:
        pass


def catalog_by_url(url):
    """(doc_id, entry) of the newest entry for this clean URL, else (None, None)."""
    best = (None, None)
    for doc_id, e in catalog_read()["manuals"].items():
        if isinstance(e, dict) and e.get("url") == url:
            if best[1] is None or (e.get("added") or "") > (best[1].get("added") or ""):
                best = (doc_id, e)
    return best


def needs_check(entry, now=None):
    """True when the URL's last HEAD check is older than a day (or there was none)."""
    t = cleanup.parse_iso((entry or {}).get("checked_at"))
    return t is None or (now or time.time()) - t >= FRESH_S


def unchanged(entry, head_info):
    """True when the validators say the file is the same; None when there is nothing to compare."""
    if not head_info:
        return None
    if entry.get("etag") and head_info.get("etag"):
        return entry["etag"] == head_info["etag"]
    if entry.get("last_modified") and head_info.get("last_modified"):
        return entry["last_modified"] == head_info["last_modified"]
    if entry.get("size") and head_info.get("length"):
        return str(entry["size"]) == str(head_info["length"])
    return None


def index_current(index_path, doc_id):
    if not index_path.exists():
        return False
    meta = build_index.read_meta(index_path)
    return meta.get("schema") == str(storage.SCHEMA_VERSION) and meta.get("doc_id") == doc_id


def entry_fields(v, doc, model=None):
    """Catalog fields from a verification result and an acquired document. Links and short
    evidence only, never page text."""
    matched = v.get("model_match") in ("exact", "family", "title")
    f = {"title": v.get("title"), "url": doc.get("url"), "path": doc.get("path"), "version": v.get("version"),
         "date": v.get("date"), "models": [model] if model and matched else [], "doc_type": v.get("doc_type"),
         "language": v.get("language"), "firmware_range": v.get("firmware_range"),
         "label": v.get("label") if matched else None,
         "evidence": v.get("evidence") if matched else None, "pages": doc.get("pages"), "etag": doc.get("etag"),
         "last_modified": doc.get("last_modified"), "size": doc.get("size"), "checked_at": doc.get("checked_at")}
    if doc.get("path"):
        f["url"] = None
    return f


# ---- network ----------------------------------------------------------------
def _http_error(e, url):
    if e.code in (401, 403, 407, 429, 451):
        return Handled("download_blocked", f"{url}: HTTP {e.code} {e.reason} (bot check, login or rate limit)",
                       BLOCKED_FALLBACK)
    if e.code in (404, 410):
        return Handled("not_found", f"{url}: HTTP {e.code} {e.reason}",
                       "the link is dead: search again (scripts/fetch.py resolve) or ask the user for another link")
    return Handled("download_failed", f"{url}: HTTP {e.code} {e.reason}", BLOCKED_FALLBACK)


def _net_error(e, url):
    reason = getattr(e, "reason", e)
    if not env_check.network_available():
        return Handled("no_network", f"no network: {reason}", NO_NETWORK_FALLBACK)
    return Handled("download_failed", f"{url}: {reason}", BLOCKED_FALLBACK)


def head(url):
    """ETag, Last-Modified, length and type of a URL; None when the server refuses HEAD."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            h = r.headers
            return {"status": r.status, "etag": h.get("ETag"), "last_modified": h.get("Last-Modified"),
                    "length": h.get("Content-Length"), "type": h.get("Content-Type")}
    except urllib.error.HTTPError as e:
        if e.code in (405, 501):
            return None
        raise _http_error(e, url)
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise _net_error(e, url)


def download(url, dest):
    """GET the URL to `dest` with a plain User-Agent. A web page instead of a PDF (bot check,
    landing page, login) is a handled error, as are 403-style refusals and no network."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            first = r.read(CHUNK)
            if b"%PDF" not in first[:1024]:
                what = "a web page" if ctype.startswith("text/html") or first.lstrip()[:1] == b"<" else (ctype or "something")
                raise Handled("not_a_pdf", f"{url} returned {what}, not a PDF (a bot check, a landing page or a login)",
                              BLOCKED_FALLBACK)
            size = 0
            with open(dest, "wb") as f:
                f.write(first)
                size += len(first)
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)
            h = r.headers
            return {"etag": h.get("ETag"), "last_modified": h.get("Last-Modified"), "size": size,
                    "final_url": r.geturl(), "type": ctype}
    except urllib.error.HTTPError as e:
        raise _http_error(e, url)
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise _net_error(e, url)


# ---- acquiring a document ----------------------------------------------------
def ensure_index(pdf, doc_id, url="", locked=False):
    """(index path, built). Reuses a current index; stores the clean URL in it when it was
    missing, so page.py link opens the vendor's own copy."""
    out = storage.index_path(doc_id)
    if index_current(out, doc_id):
        meta = build_index.read_meta(out)
        if url and meta.get("source_url") != url or (meta.get("source_path") != str(Path(pdf).resolve()) and Path(pdf).exists()):
            def _fix():
                db = sqlite3.connect(str(out))
                if url:
                    db.execute("INSERT OR REPLACE INTO meta VALUES('source_url', ?)", (url,))
                db.execute("INSERT OR REPLACE INTO meta VALUES('source_path', ?)", (str(Path(pdf).resolve()),))
                db.commit()
                db.close()
            if locked:
                _fix()
            else:
                with storage.lock():
                    _fix()
        storage.mark_used(out)
        return out, False
    if build_index.pdfium is None:
        raise Handled("no_pdf_lib", "pypdfium2 is not installed",
                      f"run: {storage.pip_hint()} — then retry; see reference/fallbacks.md")

    def _build():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
        if tmp.exists():
            tmp.unlink()
        try:
            build_index.build(pdf, tmp, doc_id, url=url)
        except build_index.pdfium.PdfiumError as e:
            if tmp.exists():
                tmp.unlink()
            raise Handled("bad_pdf", f"{Path(pdf).name}: {e}",
                          "the file is not a readable PDF (encrypted or damaged): download it again or ask the user for another copy")
        try:
            storage.replace_file(tmp, out)   # retried: on Windows an index another process is reading cannot be replaced
        except storage.InUse as e:
            if tmp.exists():
                tmp.unlink()
            raise Handled("index_in_use", str(e),
                          "the index is in use by another program; wait a moment and run the command again")
    if locked:
        _build()
    else:
        with storage.lock():
            _build()
    return out, True


def acquire(url=None, path=None, force=False, now=None):
    """Get hold of a document: the user's own file in place, or a private working copy of a
    URL (downloaded, or the cached one after the daily freshness check). Builds or reuses
    the index. Returns doc_id, pdf, index, cached, url, path, pages, etag, last_modified,
    size, checked_at, freshness, replaced (the previous doc_id when the file changed)."""
    now = now or time.time()
    if path:
        pdf = Path(path).expanduser()
        if not pdf.is_file():
            raise Handled("no_file", f"{pdf} does not exist", "check the path, or give the vendor link with --url")
        pdf = pdf.resolve()
        if not is_pdf_file(pdf):
            raise Handled("not_a_pdf", f"{pdf} is not a PDF", "give a PDF file, or the vendor link with --url")
        doc_id = storage.doc_id_for(pdf)
        index, built = ensure_index(pdf, doc_id, url="")
        meta = build_index.read_meta(index)
        return {"doc_id": doc_id, "pdf": str(pdf), "index": str(index), "cached": not built, "url": None,
                "path": str(pdf), "pages": int(meta.get("pages") or 0), "size": pdf.stat().st_size,
                "freshness": "local file"}

    url = clean_url(url)
    if not url.startswith(("http://", "https://")):
        raise Handled("bad_url", f"{url!r} is not an http(s) link", "give the vendor's link to the PDF, or a file path with --path")
    old_id, entry = catalog_by_url(url)
    freshness = None
    if entry and not force:
        pdf, index = storage.pdf_path(old_id), storage.index_path(old_id)
        if pdf.exists() or index_current(index, old_id):
            checked_at = entry.get("checked_at")
            if not needs_check(entry, now):
                freshness = "checked today"
            else:
                try:
                    info = head(url)
                except Handled as e:
                    if e.error != "no_network":
                        raise
                    info, freshness = None, "offline, not checked"
                if freshness is None:
                    same = unchanged(entry, info)
                    freshness = "unchanged" if same else "changed" if same is False else "unknown, kept"
                    if same is not False:
                        checked_at = now_iso(now)
                        catalog_update(old_id, {"checked_at": checked_at, "etag": (info or {}).get("etag"),
                                                "last_modified": (info or {}).get("last_modified")})
            if freshness != "changed":
                index, built = (ensure_index(pdf, old_id, url) if pdf.exists() else (index, False))
                meta = build_index.read_meta(index)
                out = {"doc_id": old_id, "pdf": str(pdf) if pdf.exists() else None, "index": str(index),
                       "cached": True, "url": url, "path": None, "pages": int(meta.get("pages") or 0),
                       "etag": entry.get("etag"), "last_modified": entry.get("last_modified"),
                       "size": entry.get("size"), "checked_at": checked_at, "freshness": freshness}
                if not pdf.exists():
                    out["note"] = "the PDF working copy was cleaned up; the index is complete. Run get --force to fetch the file again (page images need it)"
                return out
    with storage.lock():
        tmp = storage.pdf_dir() / f".download.{os.getpid()}.tmp"
        try:
            info = download(url, tmp)
            doc_id = storage.doc_id_for(tmp)
            dest = storage.pdf_path(doc_id)
            try:
                storage.replace_file(tmp, dest)   # retried: on Windows a PDF open in a viewer cannot be replaced
            except storage.InUse as e:
                raise Handled("file_in_use", str(e),
                              "the PDF working copy is open in another program (a viewer?); close it and run the command again")
        finally:
            if tmp.exists():
                tmp.unlink()
        index, built = ensure_index(dest, doc_id, url, locked=True)
    meta = build_index.read_meta(index)
    out = {"doc_id": doc_id, "pdf": str(dest), "index": str(index), "cached": False, "index_cached": not built,
           "url": url, "path": None, "pages": int(meta.get("pages") or 0), "etag": info.get("etag"),
           "last_modified": info.get("last_modified"), "size": info.get("size"), "checked_at": now_iso(now),
           "freshness": "downloaded" if not entry else ("changed, downloaded again" if freshness == "changed" else "downloaded again")}
    if entry and old_id != doc_id:
        out["replaced"] = old_id
    return out


def front_of(doc):
    """[(page, text)], metadata title, pages of an acquired document."""
    if doc.get("pdf") and Path(doc["pdf"]).exists():
        pages, meta, n = pdf_front(doc["pdf"])
    else:
        pages, meta, n = index_front(doc["index"])
    return pages, (meta.get("Title") or ""), n


# ---- candidates -------------------------------------------------------------
def candidate(source, v, doc=None, url=None, path=None, extra=None):
    c = {"source": source, "label": v.get("label"), "title": v.get("title"), "url": url, "path": path,
         "version": v.get("version"), "date": v.get("date"), "model_match": v.get("model_match"),
         "doc_type": v.get("doc_type"), "firmware_range": v.get("firmware_range"), "language": v.get("language"),
         "reasons": v.get("reasons"), "evidence": v.get("evidence"), "pages": v.get("pages")}
    if doc:
        c.update(doc_id=doc["doc_id"], pages=doc.get("pages"), index=bool(doc.get("index")),
                 pdf=bool(doc.get("pdf")), url=doc.get("url") or url, path=doc.get("path") or path)
    if v.get("warning"):
        c["warning"] = v["warning"]
    if extra:
        c.update(extra)
    return c


def sort_candidates(cands, doc_type=None):
    def type_rank(c):
        if doc_type:
            return 0 if doc_type_fits(doc_type, c.get("doc_type")) else 1
        return 0 if c.get("doc_type") == "user-guide" else 1
    cands.sort(key=lambda c: (version_tuple(c.get("version")), c.get("date") or ""), reverse=True)
    cands.sort(key=lambda c: (LABEL_RANK.get(c.get("label"), 3), MATCH_RANK.get(c.get("model_match"), 9), type_rank(c)))
    return cands


def dup_key(c):
    """Copies of one manual: the same title, issue and page count, wherever they were found."""
    title = re.sub(r"\W+", " ", (c.get("title") or "").lower()).strip()
    version = (c.get("version") or "").lower().strip()
    if not title or not version:
        return None
    return (title, version, c.get("pages"))


def collapse_duplicates(cands, vendor=None):
    """Two copies of the same manual (same title, issue and page count; another host, mirror
    or file) are one candidate, so they never count as close candidates. The copy kept is
    the best label, then the vendor's own domain (--vendor), then the newest date, then the
    most recently used; the others are listed under `duplicates`."""
    groups, order = {}, []
    for c in cands:
        k = dup_key(c)
        if k is None:
            k = ("", id(c))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(c)
    out = []
    for k in order:
        members = groups[k]
        if len(members) > 1:
            members.sort(key=lambda c: (c.get("date") or "", c.get("last_used") or ""), reverse=True)
            members.sort(key=lambda c: (LABEL_RANK.get(c.get("label"), 3),
                                        0 if vendor and vendor.lower() in host_of(c.get("url") or "") else 1))
            best = members[0]
            best["duplicates"] = [{"source": d.get("source"), "url": d.get("url"), "path": d.get("path"),
                                   "date": d.get("date"), "doc_id": d.get("doc_id"), "label": d.get("label")}
                                  for d in members[1:]]
            out.append(best)
        else:
            out.append(members[0])
    return out


def catalog_candidates(model, doc_type, firmware, languages):
    tokens = model_tokens(model)
    out = []
    for doc_id, e in catalog_read()["manuals"].items():
        if not isinstance(e, dict):
            continue
        mm = match_model(tokens, " ".join((e.get("models") or []) + [e.get("title") or ""]))
        if mm["kind"] in ("none", "unknown"):
            continue
        lang = e.get("language") or "unknown"
        label, reasons, warning = label_for(mm["kind"], e.get("firmware_range"), firmware,
                                            doc_type_fits(doc_type, e.get("doc_type")),
                                            lang == "unknown" or lang in languages)
        v = dict(e, label=label, reasons=reasons, warning=warning, model_match=mm["kind"])
        doc = {"doc_id": doc_id, "url": e.get("url"), "path": e.get("path"), "pages": e.get("pages"),
               "index": storage.index_path(doc_id).exists() or None,
               "pdf": (storage.pdf_path(doc_id).exists() or (e.get("path") and Path(e["path"]).exists())) or None}
        out.append(candidate("catalog", v, doc, extra={"last_used": e.get("last_used")}))
    return out


def vendor_map_rows():
    rows = []
    for p in sorted(storage.vendors_dir().glob("*.json")):
        data = storage.read_json(p, default={})
        docs = data.get("documents") if isinstance(data, dict) else data
        if not isinstance(docs, list):
            continue
        for d in docs:
            if isinstance(d, dict) and d.get("url"):
                rows.append(dict(d, vendor_map=p.stem))
    return rows


def vendor_map_candidates(model, doc_type, languages, vendor=None):
    """Rows of the vendor maps that name the model: unverified until downloaded."""
    tokens = model_tokens(model)
    out = []
    for r in vendor_map_rows():
        models = r.get("models") or []
        models = models if isinstance(models, list) else [str(models)]
        mm = match_model(tokens, " ".join(models + [r.get("title") or ""]))
        if mm["kind"] in ("none", "unknown"):
            continue
        lang = r.get("language") or "unknown"
        if not (lang == "unknown" or lang in languages):
            continue
        v = {"title": r.get("title"), "version": r.get("issue") or r.get("version"), "date": r.get("date"),
             "model_match": mm["kind"], "doc_type": r.get("doc_type"), "firmware_range": r.get("firmware_range"),
             "language": lang, "label": None, "reasons": ["from the vendor document map, not verified yet"]}
        out.append(candidate("vendor_map", v, url=clean_url(r["url"]),
                             extra={"vendor_map": r.get("vendor_map"), "verified": False}))
    if vendor:
        out.sort(key=lambda c: 0 if vendor.lower() in host_of(c["url"] or "") else 1)
    return sort_candidates(out, doc_type)


def local_candidates(model, dirs, doc_type, firmware, languages):
    """PDFs in the manuals folders that name the model: by file name first, then by title page."""
    tokens = model_tokens(model)
    files = []
    for d in dirs or []:
        p = Path(d).expanduser()
        if not p.is_dir():
            continue
        for f in sorted(p.rglob("*.pdf")):
            if f.is_file() and not f.name.startswith("."):
                files.append(f)
            if len(files) >= MAX_LOCAL_FILES:
                break
    by_name = [f for f in files if match_model(tokens, re.sub(r"[_\-.]+", " ", f.stem))["kind"] not in ("none", "unknown")]
    to_read = by_name or files[:LOCAL_TITLE_PAGES]
    out = []
    for f in to_read:
        try:
            if f not in by_name:
                pages, meta, n = pdf_front(f, n=1)
                if match_model(tokens, pages[0][1] if pages else "")["kind"] in ("none", "unknown"):
                    continue
            pages, meta, n = pdf_front(f)
        except Handled:
            continue
        v = verify(pages, meta.get("Title") or "", model, doc_type, firmware, languages)
        if v["model_match"] in ("none",):
            continue
        v["pages"] = n
        out.append(candidate("local", v, path=str(f), extra={"name_match": f in by_name}))
    return sort_candidates(out, doc_type)


def web_queries(model, doc_type=None, vendor=None):
    words = DOC_WORDS.get(doc_type) or "user guide"
    qs = []
    if vendor:
        qs.append(f"site:{vendor} {model} {words} pdf")
    qs += [f"{model} {words} pdf", f"{model} manual pdf"]
    return qs


def verify_doc(doc, model, doc_type, firmware, languages):
    pages, meta_title, n = front_of(doc)
    v = verify(pages, meta_title, model, doc_type, firmware, languages)
    v["pages"] = n
    doc["pages"] = doc.get("pages") or n
    return v


# ---- commands ---------------------------------------------------------------
def resolve(model, doc_type=None, lang=None, url=None, path=None, firmware=None, vendor=None):
    s = storage.settings()
    languages = [lang] if lang else list(s.get("manual_languages") or ["en"])
    out = {"model": model, "doc_type": doc_type, "firmware": firmware, "candidates": [], "problems": []}
    cands, touched = [], []

    def finish(stop=False):
        sort_candidates(cands, doc_type)
        merged = collapse_duplicates(cands, vendor)
        out["candidates"] = merged
        best = merged[0] if merged else None
        out["label"] = best["label"] if best else None
        if best and len(merged) > 1 and best["label"] != "Verified" and merged[1]["label"] == best["label"] \
                and merged[1]["model_match"] == best["model_match"] and merged[1].get("source") == best.get("source"):
            out["label"] = "Unsure"
            out["close"] = True
            out["note"] = "close candidates: ask the user which one (title, issue, link)"
        if best:
            out["ask_firmware"] = firmware is None
            if firmware is None and best.get("doc_id"):
                hint = firmware_hint(storage.index_path(best["doc_id"]))
                if hint:
                    out["firmware_hint"] = hint
            if best.get("warning"):
                out["warning"] = best["warning"]
        catalog_touch(touched)
        if not out["problems"]:
            del out["problems"]
        return out

    # 1. link or file from the user
    if url or path:
        doc = acquire(url=url, path=path)
        v = verify_doc(doc, model, doc_type, firmware, languages)
        cands.append(candidate("user", v, doc))
        entry = catalog_update(doc["doc_id"], entry_fields(v, doc, model))
        touched.append(doc["doc_id"])
        if v["model_match"] != "none":
            return finish()

    # 2. catalog
    for c in catalog_candidates(model, doc_type, firmware, languages):
        cands.append(c)
        touched.append(c["doc_id"])
    if any(c["label"] == "Verified" for c in cands):
        return finish()

    # 3. vendor document maps: verify the best rows by reading the document itself
    rows = vendor_map_candidates(model, doc_type, languages, vendor)
    for row in rows[:VERIFY_FROM_MAPS]:
        try:
            doc = acquire(url=row["url"])
        except Handled as e:
            out["problems"].append(dict(e.as_dict(), url=row["url"], source="vendor_map"))
            continue
        v = verify_doc(doc, model, doc_type, firmware, languages)
        cands.append(candidate("vendor_map", v, doc, extra={"vendor_map": row.get("vendor_map")}))
        catalog_update(doc["doc_id"], entry_fields(v, doc, model))
        touched.append(doc["doc_id"])
        if v["label"] == "Verified":
            return finish()
    for row in rows[VERIFY_FROM_MAPS:]:
        cands.append(row)

    # 4. local manuals folders
    cands.extend(local_candidates(model, s.get("manuals_dirs") or [], doc_type, firmware, languages))
    if any(c["label"] == "Verified" for c in cands):
        return finish()

    # 5. hand over to the agent's web search
    if not any(c.get("model_match") in ("exact", "family", "title") for c in cands):
        out["next"] = "web_search"
        out["queries"] = web_queries(model, doc_type, vendor)
        out["then"] = ("search with your web tool, the vendor's own domain first, and run "
                       f"scripts/fetch.py resolve --model {model!r} --url <link> for each PDF link, best first: "
                       "it downloads, verifies from the title page and labels it")
    return finish()


def get(url=None, path=None, force=False, model=None, doc_type=None, firmware=None, lang=None):
    cl = cleanup.run()
    doc = acquire(url=url, path=path, force=force)
    s = storage.settings()
    languages = [lang] if lang else list(s.get("manual_languages") or ["en"])
    entry = catalog_read()["manuals"].get(doc["doc_id"]) or {}
    model = model or next(iter(entry.get("models") or []), None)
    pages, meta_title, n = front_of(doc)
    v = verify(pages, meta_title, model, doc_type or entry.get("doc_type"), firmware, languages)
    if not model:
        v["label"] = entry.get("label")
        v["reasons"] = ["no model given: pass --model to verify and label"]
        v["evidence"] = entry.get("evidence") or v["evidence"]
    entry = catalog_update(doc["doc_id"], entry_fields(v, doc, model))
    out = {"doc_id": doc["doc_id"], "pdf": doc.get("pdf"), "index": doc["index"], "cached": doc["cached"],
           "version": v["version"], "date": v["date"], "title": v["title"], "pages": doc.get("pages") or n,
           "label": v["label"], "url": doc.get("url"), "path": doc.get("path"), "doc_type": v["doc_type"],
           "language": v["language"], "firmware_range": v["firmware_range"], "model_match": v["model_match"],
           "reasons": v["reasons"], "evidence": v["evidence"], "freshness": doc.get("freshness"),
           "catalog": str(storage.catalog_path()),
           "cleanup": {"removed": len(cl.get("removed", [])), "freed_mb": cl.get("freed_mb", 0)}}
    for k in ("note", "replaced"):
        if doc.get(k):
            out[k] = doc[k]
    if v.get("warning"):
        out["warning"] = v["warning"]
    if firmware is None and model:
        out["ask_firmware"] = True
        hint = firmware_hint(doc["index"])
        if hint:
            out["firmware_hint"] = hint
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="find the manual for a model and label the candidates")
    r.add_argument("--model", required=True, help='product or model, e.g. "Commander C200"')
    r.add_argument("--doc-type", choices=DOC_TYPES)
    r.add_argument("--lang", help="manual language to accept (default: manual_languages setting)")
    r.add_argument("--url", help="a link to the PDF: verify this one first")
    r.add_argument("--path", help="a PDF on disk: verify this one first")
    r.add_argument("--firmware", help="the device's firmware/software version, if known")
    r.add_argument("--vendor", help="the vendor's domain, for the web search queries")
    g = sub.add_parser("get", help="download (or open) the manual, build the index, write the catalog")
    g.add_argument("--url")
    g.add_argument("--path")
    g.add_argument("--force", action="store_true", help="download again even if a copy is cached")
    g.add_argument("--model")
    g.add_argument("--doc-type", choices=DOC_TYPES)
    g.add_argument("--firmware")
    g.add_argument("--lang")
    a = ap.parse_args()
    if bool(a.url) == bool(a.path) and (a.cmd == "get" or a.url):
        ap.error("give exactly one of --url or --path" if a.cmd == "get" else "give --url or --path, not both")
    try:
        if a.cmd == "resolve":
            storage.emit(resolve(a.model, a.doc_type, a.lang, a.url, a.path, a.firmware, a.vendor))
        else:
            storage.emit(get(a.url, a.path, a.force, a.model, a.doc_type, a.firmware, a.lang))
    except Handled as e:
        storage.fail(e.error, e.message, e.fallback)


if __name__ == "__main__":
    main()
