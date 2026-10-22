"""The generic ID patterns: the index side and the query side must produce the same key."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from patterns import (CAPTION_QUERY_RE, CAPTION_RE, PARAM_DEF_RE, PARAM_QUERY_RE, PARAM_REF_RE,  # noqa: E402
                      TRIP_SENTENCE_RE, TRIP_TOKEN_RE, VERSION_RE, caption_key, param_key)


class Params(unittest.TestCase):
    def test_every_spelling_maps_to_one_key(self):
        self.assertEqual(param_key("5", "14"), "05.014")
        self.assertEqual(param_key("05", "014"), "05.014")
        for text in ("Pr 5.14 default", "default of Pr 05.014", "see 05.014 and 2.11"):
            m = PARAM_QUERY_RE.search(text)
            self.assertEqual(param_key(m.group(1), m.group(2)), "05.014", text)

    def test_definition_is_a_line_starting_with_the_id_and_a_name(self):
        self.assertTrue(PARAM_DEF_RE.match("05.014 Motor rated current"))
        self.assertTrue(PARAM_DEF_RE.match("Pr 5.14 Motor rated current"))
        self.assertFalse(PARAM_DEF_RE.match("5.1 Introduction"))       # one digit after the dot: a section
        self.assertFalse(PARAM_DEF_RE.match("4.2.1 Wiring the drive"))

    def test_references_inside_text(self):
        found = set()
        for m in PARAM_REF_RE.finditer("Set {05.014} as in Pr 2.11; 03.010 applies"):
            g = [x for x in m.groups() if x]
            found.add(param_key(g[0], g[1]))
        self.assertEqual(found, {"05.014", "02.011", "03.010"})


class Captions(unittest.TestCase):
    def test_index_side(self):
        for text, want in (("Figure 5-3: Wiring", "figure 5-3"), ("Fig. 5.3 Wiring", "figure 5-3"),
                           ("Table 12", "table 12"), ("Tab. 4-1:", "table 4-1"), ("Fig 7", "figure 7")):
            m = CAPTION_RE.match(text)
            self.assertIsNotNone(m, text)
            self.assertEqual(caption_key(m.group(1), m.group(2)), want, text)

    def test_query_side_meets_index_side(self):
        for q, want in (("see figure 5.3", "figure 5-3"), ("Table 12 values", "table 12"),
                        ("fig 5-3", "figure 5-3"), ("tab. 4.1", "table 4-1")):
            m = CAPTION_QUERY_RE.search(q)
            self.assertIsNotNone(m, q)
            self.assertEqual(caption_key(m.group(1), m.group(2)), want, q)

    def test_running_text_is_not_a_caption(self):
        self.assertIsNone(CAPTION_RE.match("The figure shows the wiring"))
        self.assertIsNone(CAPTION_QUERY_RE.search("figure out the wiring"))


class Trips(unittest.TestCase):
    def test_standalone_codes(self):
        for code in ("OI.AC", "PH.Lo", "It.br", "E12", "F0012", "AL23", "OV", "A1"):
            self.assertTrue(TRIP_TOKEN_RE.match(code), code)
        for word in ("the", "Motor", "WARNING!", "12.5", "overload"):
            self.assertFalse(TRIP_TOKEN_RE.match(word), word)

    def test_sentence_form(self):
        m = TRIP_SENTENCE_RE.search("The OI.AC trip indicates that the output current is too high.")
        self.assertEqual(m.group(1), "OI.AC")
        m = TRIP_SENTENCE_RE.search("The E12 fault occurs when")
        self.assertEqual(m.group(1), "E12")


class Versions(unittest.TestCase):
    def test_title_page_forms(self):
        for text, want in (("Issue Number: 2", ("Issue Number", "2")), ("Edition 07", ("Edition", "07")),
                           ("Version 2.3.1", ("Version", "2.3.1")), ("Rev. 3", ("Rev.", "3"))):
            m = VERSION_RE.search(text)
            self.assertEqual((m.group(1), m.group(2)), want, text)


if __name__ == "__main__":
    unittest.main()
