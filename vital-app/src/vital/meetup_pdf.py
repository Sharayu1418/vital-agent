"""Renders the meeting-point document.

Kept apart from meetup.py so the geometry and the ranking stay pure and
testable without a PDF library in the way. This file knows about layout and
nothing about decisions.

fpdf2 rather than reportlab or a headless browser: pure Python, no system
libraries, a few hundred kilobytes, and it starts instantly on Cloud Run.
A browser-based renderer would mean shipping Chromium to render one page of
text.

WHAT GOES IN AND WHAT STAYS OUT
-------------------------------
In: venue names, addresses, ratings, straight-line distance for each
person, why each was chosen, the tradeoff, and what was rejected.

Out: coordinates, user ids, and any address belonging to either person.
Both people receive the SAME document, so anything in it is something each
has agreed the other may see. Distance is the part that affects the
decision; position is not, and is withheld.
"""
from __future__ import annotations

from vital.meetup import Person, Suggestion

MARGIN = 18
TEXT = (28, 28, 32)
MUTED = (110, 110, 122)
RULE = (222, 222, 230)


def build(activity: str, a: Person, b: Person, suggestions: list[Suggestion],
          footnote: str = "", generated: str = "") -> bytes:
    """The document, as PDF bytes. Raises if fpdf2 is missing."""
    from fpdf import FPDF

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=MARGIN)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.add_page()
    width = pdf.w - 2 * MARGIN

    # --- header ---
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*TEXT)
    pdf.multi_cell(width, 9, f"{activity.strip().title()} — where to meet")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(width, 5.5,
                   f"{a.label} and {b.label}"
                   + (f"  ·  {generated}" if generated else ""))
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.multi_cell(width, 5,
                   "Ranked so neither of you does all the travelling. "
                   "Distances are straight-line from each of your general "
                   "areas, not door-to-door travel time.")
    pdf.ln(3)
    _rule(pdf, width)

    if not suggestions:
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*TEXT)
        pdf.multi_cell(width, 6,
                       "No venue came out as a fair meeting point this time. "
                       "You are probably far enough apart that it is worth "
                       "agreeing a neighbourhood between you first, then "
                       "searching there.")
        return _out(pdf)

    for index, suggestion in enumerate(suggestions, start=1):
        _entry(pdf, width, index, suggestion, a, b)

    if footnote:
        pdf.ln(1)
        _rule(pdf, width)
        pdf.ln(3)
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(width, 5, footnote)

    pdf.ln(4)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(width, 4.5,
                   "Neither of you can see the other's location — only how "
                   "far each venue is from your general area. Check opening "
                   "hours before you go.")
    return _out(pdf)


def _entry(pdf, width, index: int, s: Suggestion, a: Person, b: Person) -> None:
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*TEXT)
    line = f"{index}.  {s.venue.name}"
    if s.venue.rating is not None:
        line += f"   {s.venue.rating:.1f}*"
    pdf.multi_cell(width, 6.5, _ascii(line))

    if s.venue.address:
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(width, 5, _ascii(s.venue.address))

    # The two numbers that decide it, given equal visual weight.
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*TEXT)
    pdf.multi_cell(width, 5.5,
                   _ascii(f"{a.label}: {s.km_a:.1f} km      "
                          f"{b.label}: {s.km_b:.1f} km"))

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*TEXT)
    for reason in s.reasons:
        pdf.multi_cell(width, 5.2, _ascii(f"  -  {reason}"))

    pdf.ln(1)
    pdf.set_font("Helvetica", "I", 9.5)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(width, 5, _ascii(f"Tradeoff: {s.tradeoff}"))
    pdf.ln(2)
    _rule(pdf, width)


def _rule(pdf, width) -> None:
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.2)
    y = pdf.get_y()
    pdf.line(MARGIN, y, MARGIN + width, y)


def _ascii(text: str) -> str:
    """fpdf2's built-in fonts are latin-1.

    Venue names carry em dashes, curly quotes and accents constantly, and an
    unencodable character raises mid-render — turning one Portuguese cafe
    into a 500 on the whole document. Substitute the common ones, drop the
    rest, and keep the page.
    """
    swaps = {"—": "-", "–": "-", "‘": "'", "’": "'",
             "“": '"', "”": '"', "…": "...", " ": " ",
             "•": "-", "★": "*", "×": "x"}
    for bad, good in swaps.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "ignore").decode("latin-1")


def _out(pdf) -> bytes:
    data = pdf.output()
    return bytes(data) if not isinstance(data, bytes) else data
