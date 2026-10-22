# ID patterns

Generic, vendor-neutral patterns the index and the search use. They live in
`scripts/patterns.py`, shared by `scripts/build_index.py` (indexing) and `scripts/search.py`
(query parsing), with unit tests in `tests/unit/test_patterns.py`. Extend them here and
there together; never with a rule for one manual.

## Parameters

| Written in manuals as | Normalised key | Notes |
| --- | --- | --- |
| `05.014`, `5.14`, `Pr 05.014`, `Pr 5.14`, `{05.014}` | `05.014` | menu.parameter, zero-padded to 2.3 |

Other families (`P-14` / `P0014` single-number parameters, `F1.07` / `b1.02`
letter-group parameters) are not anchored yet; they still work through text search.
Add them here and in the code together, with a test manual that uses them.

A block that *starts* with a parameter ID followed by a name is a **definition**
(`is_def = 1`, kind `record`). A mention inside running text is a **reference**
(`is_def = 0`). Exact search returns definitions first.

A parameter table row is one block, `ID  name  range  default  unit` as printed, and the
answer is read from that row. Splitting the row into columns is not done yet.

## Fault, trip and error codes

Two detections, both generic:

- A standalone short token on its own line inside a chapter whose title contains
  *diagnos*, *fault*, *trip*, *alarm*, *error code* or *troubleshoot*: `OI.AC`, `PH.Lo`,
  `E12`, `F0012`, `AL23`. Pattern: `^[A-Za-z][A-Za-z0-9]{0,5}\.[A-Za-z0-9]{1,5}$`,
  `^[A-Z]{2,6}[0-9]{0,3}$` or `^[A-Z]{1,3}[0-9]{1,5}$`.
- A sentence `The <code> trip|fault|alarm indicates|occurs|is`.

A few harmless false positives are accepted. Keys are lower-cased.

## Captions

`Figure 5-3`, `Fig. 5.3`, `Table 12`, `Tab. 4-1` → key `figure 5-3`, `table 12`
(word spelled out, separator `-`). Query forms `figure 5.3` and `figure 5-3` match the
same key.

## Section numbers

A line that is only a section number (`4`, `4.2`, `4.2.1`, `4.2.`) is joined to the
heading text that follows on the same line, even across a wide gap.

## Version, issue and edition

`(Issue|Issue Number|Edition|Version|Revision|Rev.)[:]? <token>` on the title page,
case-insensitive. The title page wins over PDF metadata, which is often stale.

## Firmware notes

Blocks containing `firmware`, `software version`, `from V1.10`, `version 1.10` and
similar are anchored as `firmware` notes and shown next to a hit that carries one.

## Model numbers with placeholders

A model number in the query (`AX8108`) also matches the manual's placeholder form of the
same length (`AX81x8`, `AX8xxx`): the vocabulary of the index is scanned for terms with
digits and `x` in the same positions.

## Page labels

Printed page labels (`17/25`, `A-3`, `iv`) are read from the PDF's page label tree and
stored next to the physical page number. Answers cite the printed label; links use the
physical number.

## Boilerplate

Running headers and footers are detected by words that recur on more than 30 % of pages
within the top and bottom 12 % of the page height. Lines there whose words are at least
80 % recurring are dropped before indexing, as are bare page numbers.

## Contents and index pages

A page is treated as a contents page when most of its lines end in a number after dot
leaders or a gap, when it has many dot leaders, or when the outline puts it under a
*Contents* or *Index* entry. Its blocks are stored but not searched.
