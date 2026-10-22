"""Generic, vendor-neutral ID patterns shared by build_index.py (indexing) and search.py
(query parsing). Documented in reference/id-patterns.md: change the two together, and
never add a rule for one manual or one test question.
"""
import re

# Parameters written as menu.parameter: "05.014", "5.14", "Pr 05.014", "{05.014}".
PARAM_DEF_RE = re.compile(r"^(?:Pr\s*)?(\d{1,2})\.(\d{2,3})\s+(\S.*)$")   # "05.014 Name ..." at line start = definition
PARAM_REF_RE = re.compile(r"\bPr\s*(\d{1,2})\.(\d{2,3})\b|\{(\d{1,2})\.(\d{3})\}|\b(\d{2})\.(\d{3})\b")
PARAM_QUERY_RE = re.compile(r"\b(?:Pr\s*)?(\d{1,2})\.(\d{1,3})\b")

# Figure and table captions: "Figure 5-3:", "Fig. 5.3", "Table 12", "Tab. 4-1".
CAPTION_RE = re.compile(r"^(Figure|Fig\.?|Table|Tab\.?)\s*(\d+(?:[-.]\d+)*)\s*:?")
CAPTION_QUERY_RE = re.compile(r"(?i)\b(figure|fig\.?|table|tab\.?)\s*(\d+(?:[-.]\d+)*)")

# Fault, trip and error codes: a short standalone token in a diagnostics chapter
# ("OI.AC", "PH.Lo", "E12", "F0012", "AL23"), or the sentence "The X trip indicates".
TRIP_TOKEN_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9]{0,5}\.[A-Za-z0-9]{1,5}|[A-Z]{2,6}[0-9]{0,3}|[A-Z]{1,3}[0-9]{1,5})$")
TRIP_SENTENCE_RE = re.compile(r"\bThe ([A-Za-z0-9.]{2,8}) (?:trip|fault|alarm) (?:indicates|occurs|is)")
DIAG_CHAPTER_RE = re.compile(r"(?i)diagnos|fault|trip|alarm|error code|troubleshoot")

# Document version on the title page, and firmware notes in the text.
VERSION_RE = re.compile(r"(?i)\b(issue(?: number)?|edition|version|revision|rev\.?)\s*:?\s*([0-9][\w.\-]*)")
FIRMWARE_RE = re.compile(r"(?i)\b(firmware|software version|from v\d|version \d+\.\d+)")

# Layout helpers.
SECTION_NUMBER_RE = re.compile(r"^\d+(\.\d+)*\.?$")          # a line that is only "4.2.1"
CONTENTS_TITLE_RE = re.compile(r"(?i)^(table of )?contents$|^index$")

_CAPTION_WORD = {"figure": "figure", "fig": "figure", "table": "table", "tab": "table"}


def param_key(menu, number):
    """'5', '14' -> '05.014': the one form every spelling of a parameter maps to."""
    return f"{int(menu):02d}.{int(number):03d}"


def caption_key(word, number):
    """'Fig.', '5.3' -> 'figure 5-3': word spelled out, separator '-'. Same on both sides."""
    return f"{_CAPTION_WORD[word.lower().rstrip('.')]} {number.replace('.', '-')}"
