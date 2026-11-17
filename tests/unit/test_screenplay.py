"""Screenplays (ComfyUI-Studio/screenplay.py): Fountain parsing and writing, and reading a screenplay back from a PDF.

The two real files in screenplays/ (The Glass Harbour as .fountain and as a PDF) must give the same screenplay. The other
PDFs are written here by a tiny PDF writer, so every layout case (page breaks inside a speech, scene numbers, dual
dialogue, encodings, compressed object streams, encryption) is checked without any PDF library."""
import importlib.util
import os
import textwrap
import zlib

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FOUNTAIN = os.path.join(REPO, "screenplays", "the_glass_harbour.fountain")
PDF = os.path.join(REPO, "screenplays", "The Glass Harbour.pdf")

_spec = importlib.util.spec_from_file_location("studio_screenplay", os.path.join(REPO, "ComfyUI-Studio", "screenplay.py"))
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)


def kinds(text_or_parsed):
    p = sp.parse(text_or_parsed) if isinstance(text_or_parsed, str) else text_or_parsed
    return [(e["type"], e["text"]) for e in p["elements"]]


def same_screenplay(els):
    """Elements compared the way a PDF can keep them: whitespace evened out, and action paragraphs that follow each other
    joined (a PDF can't tell a line break inside a paragraph from a new paragraph when the app prints both alike)."""
    out = []
    for kind, text in els:
        text = " ".join(text.split())
        if kind == "action" and out and out[-1][0] == "action":
            out[-1] = ("action", out[-1][1] + " " + text)
        else:
            out.append((kind, text))
    return out


# ------------------------------------------------------------------ the two real files
def test_the_fountain_file_parses():
    p = sp.parse(open(FOUNTAIN, encoding="utf-8").read())
    assert p["title_page"] == [("Title", "The Glass Harbour"), ("Credit", "Written by"), ("Author", "Alex Rivera"), ("Draft date", "08/28/2026")]
    assert p["title"] == "The Glass Harbour"
    assert p["scenes"] == [{"heading": "INT. THE GLASS HARBOUR DINER - LATE AFTERNOON", "number": "1", "line": 5}]
    assert [(c["name"], c["lines"]) for c in p["characters"]] == [("RAY", 28), ("JUNE", 35)]
    counts = {}
    for e in p["elements"]:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    assert counts == {"scene": 1, "action": 20, "character": 63, "dialogue": 64, "parenthetical": 2}
    assert p["pages"] == 7                               # the PDF has 7 pages after its title page
    els = p["elements"]
    assert els[1]["text"].startswith("ONE SHOT. NO CUTS. FIVE MINUTES, ONE BREATH.\nA 1941 diner")   # a capitals line with a comma is action
    ray = next(i for i, e in enumerate(els) if e["text"] == "(too fast, too loud)")
    assert els[ray - 1] == {"type": "character", "text": "RAY", "line": els[ray - 1]["line"]}
    assert els[ray + 1]["text"].startswith("I was forty-eight and I was drowning")
    assert els[-1]["text"].endswith("Two people in a corner booth. Not talking. Not leaving.\nFADE OUT.")


def test_the_pdf_reads_as_the_same_screenplay():
    text, warnings = sp.fountain_from_pdf(open(PDF, "rb").read())
    assert warnings == []
    pdf, src = sp.parse(text), sp.parse(open(FOUNTAIN, encoding="utf-8").read())
    assert pdf["title_page"] == src["title_page"]        # title page: the title from the PDF's info, credit, author, date
    assert pdf["scenes"][0]["heading"] == src["scenes"][0]["heading"] and pdf["scenes"][0]["number"] == "1"
    assert [c["name"] for c in pdf["characters"]] == ["RAY", "JUNE"]
    assert [c["lines"] for c in pdf["characters"]] == [28, 35]
    assert same_screenplay(kinds(pdf)) == same_screenplay(kinds(src))
    # wrapped lines are joined back into sentences, and nothing of the page furniture (page numbers, scene numbers) is left
    assert "Mom used to get the chowder. You'd order the coffee anyway and tell the waitress it was terrible, and she'd laugh like she hadn't heard it before." in text
    assert "2." not in text.split("\n") and "\n5\n" not in text
    assert sp.parse(text)["pages"] == 7


def test_fountain_round_trip_of_the_real_file():
    src = sp.parse(open(FOUNTAIN, encoding="utf-8").read())
    again = sp.parse(sp.to_fountain(src["title_page"], src["elements"]))
    assert kinds(again) == kinds(src) and again["title_page"] == src["title_page"]


# ------------------------------------------------------------------ Fountain syntax
SYNTAX = """Title:
    _**BRICK & STEEL**_
    _**FULL RETIRED**_
Credit: Written by
Author: Stu Maschwitz
Draft date: 1/20/2012
Contact:
    Next Level Productions
    1588 Mission Dr.

/* a boneyard
   across lines */
# ACT ONE

= Brick comes home.

EXT. BRICK'S PATIO - DAY #1A#

A gorgeous day. The sun is shining. But BRICK BRADDOCK, retired police detective, is sitting quietly, contemplating -- something.

The SCREEN DOOR slides open and DICK STEEL, his former partner and fellow retiree, emerges with two cold beers.

STEEL
Beer's ready!

BRICK
Are they cold?

STEEL (O.S.)
(calling)
Does a bear crap in the woods?
  
Of course they're cold.

@McCLANE
Yippee ki-yay.

BRICK
Screw retirement.

STEEL ^
Screw retirement.

CUT TO:

.FLASHBACK

!SCANNING THE AISLES...
Steel walks on.

> THE END <

>FADE TO BLACK.

~Willy Wonka! Willy Wonka!

===

EXT. HOUSE - NIGHT

Brick walks in. [[a note]]
"""


def test_fountain_syntax():
    p = sp.parse(SYNTAX)
    tp = dict(p["title_page"])
    assert tp["Title"] == "_**BRICK & STEEL**_\n_**FULL RETIRED**_" and p["title"] == "BRICK & STEEL FULL RETIRED"
    assert tp["Contact"] == "Next Level Productions\n1588 Mission Dr." and tp["Author"] == "Stu Maschwitz"
    got = [(e["type"], e["text"]) + ((e["number"],) if "number" in e else ()) + (("dual",) if e.get("dual") else ()) for e in p["elements"]]
    assert got == [
        ("section", "ACT ONE"),
        ("synopsis", "Brick comes home."),
        ("scene", "EXT. BRICK'S PATIO - DAY", "1A"),
        ("action", "A gorgeous day. The sun is shining. But BRICK BRADDOCK, retired police detective, is sitting quietly, contemplating -- something."),
        ("action", "The SCREEN DOOR slides open and DICK STEEL, his former partner and fellow retiree, emerges with two cold beers."),
        ("character", "STEEL"), ("dialogue", "Beer's ready!"),
        ("character", "BRICK"), ("dialogue", "Are they cold?"),
        ("character", "STEEL (O.S.)"), ("parenthetical", "(calling)"), ("dialogue", "Does a bear crap in the woods?\n\nOf course they're cold."),
        ("character", "McCLANE"), ("dialogue", "Yippee ki-yay."),
        ("character", "BRICK"), ("dialogue", "Screw retirement."),
        ("character", "STEEL", "dual"), ("dialogue", "Screw retirement."),
        ("transition", "CUT TO:"),
        ("scene", "FLASHBACK"),
        ("action", "SCANNING THE AISLES...\nSteel walks on."),
        ("centered", "THE END"),
        ("transition", "FADE TO BLACK."),
        ("lyrics", "Willy Wonka! Willy Wonka!"),
        ("page_break", ""),
        ("scene", "EXT. HOUSE - NIGHT"),
        ("action", "Brick walks in. [[a note]]"),
    ]
    assert [c["name"] for c in p["characters"]] == ["STEEL", "BRICK", "MCCLANE"]
    assert [s["number"] for s in p["scenes"]] == ["1A", "", ""]
    lines = SYNTAX.split("\n")
    assert all(lines[s["line"]].lstrip(".").startswith(s["heading"]) for s in p["scenes"])   # where to jump to in the editor
    again = sp.parse(sp.to_fountain(p["title_page"], p["elements"]))
    assert kinds(again) == kinds(p) and again["title_page"] == p["title_page"]


@pytest.mark.parametrize("line,cue", [("RAY", True), ("JUNE (CONT'D)", True), ("DR. HALE (V.O.)", True), ("COP #2", True),
                                      ("MRS. O'NEIL", True), ("@McCLANE", True), ("ONE SHOT. NO CUTS. FIVE MINUTES, ONE BREATH.", False),
                                      ("BANG!", False), ("Ray", False), ("FADE OUT.", False), ("CUT TO:", False), ("INT. HOUSE", False),
                                      ("1941", False), ("A VERY LONG LINE OF CAPITALS THAT IS NOT ANYBODY'S NAME AT ALL", False)])
def test_what_counts_as_a_character_cue(line, cue):
    assert sp.is_cue(line) is cue


def test_writer_forces_what_would_read_as_something_else():
    els = [{"type": "action", "text": "INT. IS WHAT THE SIGN SAYS"}, {"type": "action", "text": "BOOM\nThe door gives."},
           {"type": "action", "text": "SMASH TO:"}, {"type": "character", "text": "McCLANE"}, {"type": "dialogue", "text": "Hi."},
           {"type": "scene", "text": "THE ROOF"}, {"type": "transition", "text": "Fade out."}, {"type": "action", "text": "# not a section"}]
    text = sp.to_fountain([], els)
    assert [(e["type"], e["text"]) for e in sp.parse(text)["elements"]] == [(e["type"], e["text"]) for e in els]


def test_text_files_in_any_encoding():
    s = "Title: Café\n\nINT. CAFÉ - DAY\n\nZOË\n“Hello” — there.\n"
    assert sp.fountain_from_text(("\ufeff" + s).encode("utf-8")) == s
    assert sp.fountain_from_text(s.replace("\n", "\r\n").encode("cp1252")) == s
    assert sp.fountain_from_text(s.encode("utf-16")) == s
    with pytest.raises(sp.ScreenplayError):
        sp.fountain_from_text(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")


def test_glyph_names():
    assert [sp.glyph_text(n) for n in ("a", "eacute", "Aacute", "quoteright", "emdash", "uni2014", "f_i", "A.sc", "zero", "udieresis", "bogus123")] == \
        ["a", "é", "Á", "\u2019", "\u2014", "\u2014", "fi", "A", "0", "ü", ""]


# ------------------------------------------------------------------ PDFs written here
def _pdf_string(s, encoding):
    b = s.encode(encoding)
    return b"(" + b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def make_pdf(pages, info=None, compress=False, objstm=False, encrypt=False, differences=False, size=(612, 792)):
    """A small PDF: pages of (x, y from the top, text[, font size]) drawn in Courier. Courier is one of the 14 standard fonts,
    so nothing is embedded. differences=True uses a font whose codes are remapped by glyph name (/Differences)."""
    w, h = size
    objs = {}
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = []
    font = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding << /Type /Encoding /BaseEncoding /WinAnsiEncoding "
            b"/Differences [ 1 /Eacute /quotedblleft /quotedblright /emdash /f_i ] >> >>" if differences else
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    objs[3] = font
    n = 10
    for page in pages:
        ops = []
        for item in page:
            x, y, t = item[:3]
            fs = item[3] if len(item) > 3 else 12
            if isinstance(t, bytes):                     # raw operators (TJ arrays, rotated text...)
                ops.append(b"BT /F1 %d Tf 1 0 0 1 %.2f %.2f Tm " % (fs, x, h - y) + t + b" ET")
                continue
            if differences:
                raw = t.translate(str.maketrans({"É": "\x01", "“": "\x02", "”": "\x03", "—": "\x04", "ﬁ": "\x05"}))
                s = _pdf_string(raw, "latin-1")
            else:
                s = _pdf_string(t, "cp1252")
            ops.append(b"BT /F1 %d Tf %.2f %.2f Td " % (fs, x, h - y) + s + b" Tj ET")
        content = b"\n".join(ops)
        if compress:
            content = zlib.compress(content)
            objs[n] = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content) + content + b"\nendstream"
        else:
            objs[n] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
        objs[n + 1] = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (w, h, n)
        kids.append(n + 1)
        n += 2
    objs[2] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)
    info_obj = None
    if info:
        info_obj = n
        objs[n] = b"<< " + b" ".join(b"/" + k.encode() + b" " + _pdf_string(v, "latin-1") for k, v in info.items()) + b" >>"
        n += 1
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    if objstm:                                           # the catalog, page tree and font go into a compressed object stream
        packed = [1, 2, 3]
        body, header = b"", b""
        for k in packed:
            header += b"%d %d " % (k, len(body))
            body += objs.pop(k) + b"\n"
        data = zlib.compress(header + body)
        objs[n] = b"<< /Type /ObjStm /N %d /First %d /Filter /FlateDecode /Length %d >>\nstream\n" % (len(packed), len(header), len(data)) + data + b"\nendstream"
        n += 1
    for k in sorted(objs):
        out += b"%d 0 obj\n" % k + objs[k] + b"\nendobj\n"
    trailer = b"<< /Size %d /Root 1 0 R" % (n + 1) + (b" /Info %d 0 R" % info_obj if info_obj else b"")
    if encrypt:
        trailer += b" /Encrypt << /Filter /Standard /V 2 /R 3 /Length 128 /O <00> /U <00> /P -4 >>"
    out += b"trailer\n" + trailer + b" >>\nstartxref\n0\n%%EOF\n"
    return bytes(out)


L, CW, LINE = 108.0, 7.2, 12.0                           # the action margin (1.5"), Courier 12's character width, 6 lines an inch


def lay_out(elements, title=None, lines_per_page=50, scene_numbers=True, revision_marks=False):
    """A screenplay laid out the standard way: action at 1.5", dialogue +1", parentheticals +1.5", cues +2", transitions
    ending at 7.5"; a blank line between elements; page numbers; (MORE) and CONT'D when a speech breaks over a page."""
    pages = []
    if title:
        pages.append([(306 - len(title) * CW / 2, 250, title), (306 - 5 * CW, 290, "Written by"), (306 - 4 * CW, 310, "Ann Lee"),
                      (L, 680, "ann@example.com"), (504 - 10 * CW, 680, "Draft 3, 5/1/2026")])
    page, y = [], 72.0
    cue = None

    def new_page():
        nonlocal page, y
        pages.append(page)
        page, y = [(504 - 3 * CW, 36, f"{len(pages) + (0 if title else 1)}."), (L, 60, "CONTINUED:")], 84.0

    def put(x, t):
        nonlocal y
        page.append((x, y, t))
        if revision_marks:
            page.append((L + 65 * CW, y, "*"))
        y += LINE

    bottom = 72 + lines_per_page * LINE
    for k, (kind, text, *extra) in enumerate(elements):
        if kind in ("scene", "action", "character", "transition", "centered", "dual") and k:
            y += LINE
        if y > bottom - 3 * LINE and kind in ("scene", "character", "dual"):
            new_page()
        if kind == "scene":
            if scene_numbers and extra:
                page.append((L - 6 * CW, y, extra[0]))
                page.append((L + 62 * CW, y, extra[0]))
            put(L, text)
        elif kind == "action":
            for para in text.split("\n"):
                for ln in textwrap.wrap(para, 60):
                    if y > bottom:
                        new_page()
                    put(L, ln)
        elif kind == "character":
            cue = text
            put(L + 20 * CW, text)
        elif kind == "parenthetical":
            for ln in textwrap.wrap(text, 25):
                put(L + 15 * CW, ln)
        elif kind == "dialogue":
            for ln in textwrap.wrap(text, 35):
                if y > bottom:
                    put(L + 20 * CW, "(MORE)")
                    new_page()
                    put(L + 20 * CW, sp.cue_name(cue) + " (CONT'D)")
                put(L + 10 * CW, ln)
        elif kind == "transition":
            put(L + 60 * CW - len(text) * CW, text)
        elif kind == "centered":
            put(306 - len(text) * CW / 2, text)
        elif kind == "dual":                             # two speeches side by side
            (c1, d1), (c2, d2) = extra[0], extra[1]
            page.append((L + 8 * CW, y, c1))
            put(L + 38 * CW, c2)
            left, right = textwrap.wrap(d1, 26), textwrap.wrap(d2, 26)
            for i in range(max(len(left), len(right))):
                if i < len(left):
                    page.append((L, y, left[i]))
                if i < len(right):
                    page.append((L + 31 * CW, y, right[i]))
                y += LINE
    pages.append(page)
    return pages


LONG = ("I have been on this rock for eleven winters and I have never once seen a boat come in on a night like this one, "
        "not with the lamp out and the bell gone quiet and every sensible captain tied up in the harbour three miles south.")
SCRIPT = [
    ("scene", "INT. LIGHTHOUSE - LAMP ROOM - NIGHT", "12A"),
    ("action", "Rain hammers the glass. MARA (60s), oilskin coat, climbs the last steps with a lantern and stops dead: the great lens is dark."),
    ("action", "A beat.\nThen the bell, once, far below."),
    ("character", "MARA (V.O.)"),
    ("parenthetical", "(under her breath, the way she talks to the sea when nobody is listening)"),
    ("dialogue", "Not tonight. Not you."),
    ("character", "TOMÁS"),
    ("dialogue", "They said “no boats” — and here's one anyway."),
    ("transition", "CUT TO:"),
    ("scene", "EXT. CLIFF PATH - CONTINUOUS", "13"),
    ("action", "A self-contained, well-known path; the lantern swings."),
    ("character", "MARA"),
    ("dialogue", LONG + " " + LONG),
    ("dual", "", ("MARA", "Go back inside."), ("TOMÁS", "Not without you.")),
    ("action", "They stand there."),
    ("centered", "THE END"),
]


def expected(script):
    out = []
    for kind, text, *extra in script:
        if kind == "dual":
            (c1, d1), (c2, d2) = extra[0], extra[1]
            out += [("character", c1), ("dialogue", d1), ("character", c2), ("dialogue", d2)]
        else:
            out.append((kind, text))
    return out


def test_a_laid_out_screenplay_reads_back_exactly():
    pdf = make_pdf(lay_out(SCRIPT, title="THE LAMP", lines_per_page=30), info={"Title": "The Lamp", "Author": "Ann Lee"})
    text, warnings = sp.fountain_from_pdf(pdf)
    p = sp.parse(text)
    assert warnings == []
    assert p["title_page"] == [("Title", "The Lamp"), ("Credit", "Written by"), ("Author", "Ann Lee"), ("Contact", "ann@example.com"),
                               ("Draft date", "Draft 3, 5/1/2026")]
    assert kinds(p) == expected(SCRIPT)                  # exact: line breaks kept, wrapped lines joined, (MORE)/CONT'D undone
    assert [(s["heading"], s["number"]) for s in p["scenes"]] == [("INT. LIGHTHOUSE - LAMP ROOM - NIGHT", "12A"), ("EXT. CLIFF PATH - CONTINUOUS", "13")]
    assert next(e for e in p["elements"] if e["text"] == "TOMÁS" and e.get("dual"))
    assert "(MORE)" not in text and "CONTINUED" not in text and "\n2.\n" not in text


@pytest.mark.parametrize("variant", ["compressed", "object streams", "revision marks", "no scene numbers", "glyph-name encoding", "A4"])
def test_pdf_variants(variant):
    script = [s for s in SCRIPT if not (variant == "glyph-name encoding" and s[0] == "dual")]
    if variant == "glyph-name encoding":
        script = script + [("action", "ÉLAN — “ﬁne”, she says.")]
    pages = lay_out(script, lines_per_page=30, scene_numbers=variant != "no scene numbers", revision_marks=variant == "revision marks")
    pdf = make_pdf(pages, compress=variant in ("compressed", "object streams"), objstm=variant == "object streams",
                   differences=variant == "glyph-name encoding", size=(595, 842) if variant == "A4" else (612, 792))
    text, _ = sp.fountain_from_pdf(pdf)
    want = expected(script)
    if variant == "glyph-name encoding":
        want[-1] = ("action", "ÉLAN — “fine”, she says.")
    assert kinds(text) == want
    if variant == "revision marks":
        assert "*" not in text


def test_kerning_and_spaces_drawn_by_position():
    page = [(L, 72, b"[(INT.) -600 (HOUSE) -600 (- DAY)] TJ"),
            (L, 96, b"[(She ) 120 (wa) 30 (its.)] TJ"),                      # kerning inside a word isn't a space
            (L + 20 * CW, 120, "RAY"), (L + 10 * CW, 132, "Hello"), (L + 16 * CW, 132, b"(there.) Tj"),
            (300, 400, b"(DRAFT) Tj")]
    page[-1] = (300, 400, b"(CONFIDENTIAL) Tj")
    pdf = make_pdf([page])
    pdf = pdf.replace(b"1 0 0 1 300.00 392.00 Tm", b"0.7 0.7 -0.7 0.7 300.00 392.00 Tm")   # a diagonal watermark is ignored
    assert kinds(sp.fountain_from_pdf(pdf)[0]) == [("scene", "INT. HOUSE - DAY"), ("action", "She waits."), ("character", "RAY"),
                                                   ("dialogue", "Hello there.")]


def test_pdfs_that_cant_be_read_say_why():
    with pytest.raises(sp.ScreenplayError, match="password-protected or encrypted"):
        sp.fountain_from_pdf(make_pdf(lay_out(SCRIPT[:3]), encrypt=True))
    with pytest.raises(sp.ScreenplayError, match="no text"):
        sp.fountain_from_pdf(make_pdf([[], []]))
    with pytest.raises(sp.ScreenplayError, match="isn't a PDF"):
        sp.fountain_from_pdf(b"GIF89a" + b"\x00" * 100)
    with pytest.raises(sp.ScreenplayError):
        sp.fountain_from_pdf(b"%PDF-1.4\n" + os.urandom(3000))


def test_plain_text_in_a_pdf_comes_in_as_action_with_a_warning():
    pages = [[(72, 72 + 14 * i, f"This is line {i} of a letter, not a screenplay at all.") for i in range(8)]]
    text, warnings = sp.fountain_from_pdf(make_pdf(pages))
    assert {e["type"] for e in sp.parse(text)["elements"]} == {"action"} and len(warnings) == 1


# ------------------------------------------------------------------ breakdown: scenes, shots, characters
def _breakdown(text, *a, **k):
    return sp.breakdown(sp.parse(text), *a, **k)


def test_the_glass_harbour_breaks_down_into_its_camera_setups():
    """A shot is a camera setup. The Glass Harbour's one scene is written as one continuous take whose camera directions
    move it eight times; the camera holding ("holds on him", "stays close") is the same setup."""
    bd = _breakdown(open(FOUNTAIN, encoding="utf-8").read())
    [sc] = bd["scenes"]
    assert (sc["number"], sc["intext"], sc["location"], sc["time"], sc["characters"]) == ("1", "INT", "THE GLASS HARBOUR DINER", "LATE AFTERNOON", ["RAY", "JUNE"])
    assert sc["summary"].startswith("A 1941 diner, re-topped in '52") and sc["continuous"]
    shots = bd["shots"]
    codes = sp.shot_codes(bd["scenes"], shots)
    got = [(codes[s["id"]], s["camera"], s["title"]) for s in shots]
    assert [(c, cam) for c, cam, _t in got] == [(c, cam) for c, cam, _t in WANT] and all(t.startswith(w[:60]) for (_c, _m, t), (_x, _y, w) in zip(got, WANT))


WANT = [
        ("1A", "push in", "We begin eight inches off the floor"),          # the dolly that glides in and finds them: one shot
        ("1B", "push in", "The camera eases toward Ray"),
        ("1C", "tracking", "The camera drifts with his hands and finds her over his shoulder"),
        ("1D", "tracking", "The camera crosses the table low"),
        ("1E", "push in", "The camera pushes in"),
        ("1F", "crane down", "The camera drops to the tabletop"),
        ("1G", "crane down", "The camera comes down to the formica and holds on that open hand"),
        ("1H", "pull back", "The camera draws back off the table into a level two-shot across the booth and holds there")]


def test_the_glass_harbour_setups_go_on():
    bd = _breakdown(open(FOUNTAIN, encoding="utf-8").read())
    [sc] = bd["scenes"]
    shots = bd["shots"]
    assert [s.get("continues", False) for s in shots] == [False] + [True] * 7
    # the opening shot holds the set's description before the dolly; a camera that holds stays in its setup
    assert shots[0]["action"].startswith("ONE SHOT. NO CUTS. FIVE MINUTES, ONE BREATH. A 1941 diner") and "JUNE MERCER, twenty-three" in shots[0]["action"]
    assert "The camera holds on him, unkind about it." in shots[3]["action"] and "The camera stays close" in shots[4]["action"]
    # every line of dialogue is in exactly one shot, in order, and the beats keep the order of action and lines
    lines = [ln["text"] for s in shots for ln in s["lines"]]
    src = [e["text"] for e in sp.parse(open(FOUNTAIN, encoding="utf-8").read())["elements"] if e["type"] == "dialogue"]
    assert lines == src
    assert [ln["text"] for s in shots for b in s["beats"] if "line" in b for ln in [b["line"]]] == src
    said = next(ln for s in shots for ln in s["lines"] if ln["text"].startswith("I was forty-eight"))
    assert said["who"] == "RAY" and said["paren"] == "(too fast, too loud)"
    # screen time: a page a minute, on each setup's share of the standard page
    assert all(s["seconds"] == sp.screen_seconds([("action", b["action"]) if "action" in b else ("line", b["line"]) for b in s["beats"]]) for s in shots)
    assert abs(sum(s["seconds"] for s in shots) - sc["eighths"] / 8 * 60) <= 30
    assert [s["characters"] for s in shots][:2] == [["RAY", "JUNE"], ["RAY", "JUNE"]]
    # the characters, with the description from where they're introduced
    assert [(c["name"], c["full"], c["lines"]) for c in bd["characters"]] == [("RAY", "Ray Mercer", 28), ("JUNE", "June Mercer", 35)]
    assert bd["characters"][0]["intro"].startswith("fifty-seven, canvas work jacket he has owned too long")
    assert bd["characters"][1]["intro"].startswith("twenty-three. Coat on. Bag still on her shoulder.")


@pytest.mark.parametrize("text,cameras", [
    ("INT. ROOM - DAY\n\nA room.\n\nMARA\nHi.\n\nTOM\nHi.\n\nMara sits. Tom sits.\n", ["static"]),            # no directions: one setup
    ("INT. ROOM - DAY\n\nA room. The camera pushes in on the door.\n\nIt opens. The camera holds.\n", ["push in"]),  # a hold isn't a setup
    ("INT. ROOM - DAY\n\nA room.\n\nCLOSE ON THE DOOR\n\nIt opens.\n\nWIDE\n\nEveryone turns.\n", ["static", "close-up", "wide"]),
    ("INT. ROOM - DAY\n\nA room.\n\nCUT TO:\n\nA man at the window.\n", ["static", "static"]),                   # a cut
    ("INT. ROOM - DAY\n\nMara runs. Tom follows her. She pulls back the curtain.\n", ["static"]),               # people move, not the camera
    ("INT. ROOM - DAY\n\nA room.\n\nWe track with Mara down the hall.\n\nThe camera drifts up to the ceiling.\n", ["tracking", "crane up"]),
])
def test_what_makes_a_new_setup(text, cameras):
    assert [s["camera"] for s in _breakdown(text)["shots"]] == cameras


def test_shot_numbers():
    assert [sp.shot_letters(n) for n in (0, 7, 8, 12, 23, 24, 47)] == ["A", "H", "J", "N", "Z", "AA", "AZ"]   # no I, no O
    scenes = [{"id": "a", "number": "12"}, {"id": "b", "number": "12A"}]
    shots = [{"id": "1", "scene": "a"}, {"id": "2", "scene": "a"}, {"id": "3", "scene": "b"}, {"id": "4", "scene": "b"}]
    assert sp.shot_codes(scenes, shots) == {"1": "12A", "2": "12B", "3": "12A-A", "4": "12A-B"}


def test_the_pdf_and_the_fountain_file_break_down_the_same():
    a = _breakdown(open(FOUNTAIN, encoding="utf-8").read())
    b = _breakdown(sp.fountain_from_pdf(open(PDF, "rb").read())[0])
    strip = lambda bd: [(s["action"], s["lines"], s["camera"], s["characters"]) for s in bd["shots"]]  # noqa: E731
    assert strip(a) == strip(b)
    assert [(c["full"], c["intro"]) for c in a["characters"]] == [(c["full"], c["intro"]) for c in b["characters"]]


TWO_SCENES = """FADE IN:

INT. KITCHEN - NIGHT

Mara fills the kettle.

MARA
Tea?

TOM
Please.

EXT. GARDEN - CONTINUOUS

Tom walks out into the rain. The camera follows him to the gate.
"""


def test_the_shots_follow_the_screenplay_and_keep_what_was_decided():
    """The breakdown is made again from the screenplay at every save; what the user decided stays."""
    first = _breakdown(TWO_SCENES)
    assert [s["heading"] for s in first["scenes"]] == ["INT. KITCHEN - NIGHT", "EXT. GARDEN - CONTINUOUS"]   # FADE IN: alone is no scene
    assert [s["camera"] for s in first["shots"]] == ["static", "tracking"]
    kitchen, garden = first["scenes"]
    first["shots"][0]["keyframe"] = "studio/kitchen.png"
    first["shots"][1].update(camera="drone", camera_set=True, seconds=12, seconds_set=True)      # set by hand
    # a line changes in the kitchen and a new setup is written into the garden: the shots follow at once
    edited = TWO_SCENES.replace("Please.", "Please. Two sugars.").replace("to the gate.", "to the gate.\n\nCLOSE ON THE LATCH\n\nIt's rusted shut.")
    second = _breakdown(edited, first["scenes"], first["shots"])
    assert [s["id"] for s in second["scenes"]] == [kitchen["id"], garden["id"]]
    assert second["shots"][0]["lines"][1]["text"] == "Please. Two sugars." and second["shots"][0]["keyframe"] == "studio/kitchen.png"
    assert second["shots"][0]["id"] == first["shots"][0]["id"]
    assert [(s["camera"], s.get("camera_set", False)) for s in second["shots"][1:]] == [("drone", True), ("close-up", False)]
    assert second["shots"][1]["seconds"] == 12 and second["shots"][2]["action"].startswith("CLOSE ON THE LATCH")
    # the user splits the kitchen before Tom's line and merges the close-up back: kept through the next edits
    second["scenes"][0]["splits"] = ["TOM: Please. Two sugars."]
    second["scenes"][1]["joins"] = [second["shots"][2]["anchor"]]
    added = {"id": "m1", "manual": True, "after": second["shots"][0]["id"], "title": "Insert: the kettle", "action": "The kettle boils.", "camera": "close-up", "seconds": 3}
    third = _breakdown(edited.replace("Mara fills the kettle.", "Mara fills the old kettle."), second["scenes"], second["shots"] + [dict(added, scene=kitchen["id"])])
    assert [len(s.get("beats", [])) for s in third["shots"]] == [2, 0, 1, 3]                  # kitchen split, added shot, garden merged
    assert third["shots"][1]["id"] == "m1" and third["shots"][0]["keyframe"] == "studio/kitchen.png"
    assert third["shots"][0]["action"] == "Mara fills the old kettle."                        # the opening beat rewritten: still the same shot
    assert third["scenes"][0]["splits"] == ["TOM: Please. Two sugars."] and third["scenes"][1]["joins"]
    # back to the screenplay's own setups: choices go, keyframes stay
    fourth = _breakdown(edited, third["scenes"], third["shots"], redo=[kitchen["id"], garden["id"]])
    assert [len(s["beats"]) for s in fourth["shots"]] == [3, 1, 2] and not any(s.get("manual") for s in fourth["shots"])
    assert fourth["shots"][0]["keyframe"] == "studio/kitchen.png" and not fourth["shots"][1].get("camera_set")
    # a new scene in between gets its own shots; a renamed heading is still the same scene
    more = TWO_SCENES.replace("EXT. GARDEN", "INT. HALL - NIGHT\n\nThe lights flicker.\n\nEXT. GARDEN").replace("INT. KITCHEN - NIGHT", "INT. KITCHEN - LATER")
    fifth = _breakdown(more, first["scenes"], first["shots"])
    assert [s["heading"] for s in fifth["scenes"]] == ["INT. KITCHEN - LATER", "INT. HALL - NIGHT", "EXT. GARDEN - CONTINUOUS"]
    assert fifth["scenes"][0]["id"] == kitchen["id"] and fifth["scenes"][2]["id"] == garden["id"]
    # a deleted scene takes its shots with it; shots made before there was a screenplay stay, at the end
    loose = {"id": "old1", "title": "From an idea", "action": "A lighthouse at dawn.", "camera": "drone", "seconds": 5}
    sixth = _breakdown(TWO_SCENES.split("EXT. GARDEN")[0], first["scenes"], first["shots"] + [loose])
    assert [s["heading"] for s in sixth["scenes"]] == ["INT. KITCHEN - NIGHT"] and sixth["shots"][-1] == loose and len(sixth["shots"]) == 2


def test_every_beat_knows_its_line_in_the_screenplay():
    text = sp.fountain_from_text(open(FOUNTAIN, "rb").read())
    lines = text.split("\n")
    for s in _breakdown(text)["shots"]:
        for b in s["beats"]:
            said = b["action"] if "action" in b else b["line"]["text"].split("\n")[0]
            assert lines[b["at"]].strip().lstrip("!") == said.split("\n")[0], (b, lines[b["at"]])


def test_a_fountain_file_imports_as_the_pdf_does():
    """Each action line of the Fountain file is a paragraph, as its own PDF prints it: both imports give the same screenplay."""
    from_fountain = sp.parse(sp.fountain_from_text(open(FOUNTAIN, "rb").read()))
    from_pdf = sp.parse(sp.fountain_from_pdf(open(PDF, "rb").read())[0])
    assert [(e["type"], e["text"]) for e in from_fountain["elements"]] == [(e["type"], e["text"]) for e in from_pdf["elements"]]
    assert from_fountain["title_page"] == from_pdf["title_page"] and from_fountain["pages"] == from_pdf["pages"] == 7
    assert len(from_fountain["elements"]) == 169


def test_action_lines_become_paragraphs():
    t = "Title: X\n\nINT. X - DAY\n\nHe walks in. The room is dark\nand smells of rain.\nBOOM\nThe door gives.\nINT. IS WHAT THE SIGN SAYS\n[[a note]]\n/* kept */\n"
    assert sp.fountain_from_text(t.encode()) == ("Title: X\n\nINT. X - DAY\n\nHe walks in. The room is dark and smells of rain.\n\nBOOM\n\nThe door gives."
                                                 "\n\n!INT. IS WHAT THE SIGN SAYS\n\n[[a note]]\n/* kept */\n")
    assert sp.fountain_from_text(t.encode(), paragraphs=False) == t
    assert [e["type"] for e in sp.parse(sp.fountain_from_text(t.encode()))["elements"]] == ["scene", "action", "action", "action", "action", "action"]
    assert sp.fountain_from_text(SYNTAX.encode()) == SYNTAX.replace("SCANNING THE AISLES...\nSteel", "SCANNING THE AISLES...\n\nSteel")


def test_a_screenplay_without_headings_is_one_opening_scene():
    bd = _breakdown("A man waits at a bus stop.\n\nMAN\nLate again.\n")
    assert [(s["heading"], s["location"]) for s in bd["scenes"]] == [("", "Opening")] and len(bd["shots"]) == 1
    assert _breakdown("") == {"scenes": [], "shots": [], "characters": [], "locations": []}


@pytest.mark.parametrize("heading,parts", [
    ("INT. THE GLASS HARBOUR DINER - LATE AFTERNOON", ("INT", "THE GLASS HARBOUR DINER", "LATE AFTERNOON")),
    ("EXT. LIGHTHOUSE - LAMP ROOM - NIGHT", ("EXT", "LIGHTHOUSE - LAMP ROOM", "NIGHT")),
    ("INT./EXT. CAR - MOVING - DAY", ("INT/EXT", "CAR - MOVING", "DAY")),
    ("I/E TRUCK", ("INT/EXT", "TRUCK", "")), ("EST. CITY SKYLINE", ("EST", "CITY SKYLINE", "")), ("FLASHBACK", ("", "FLASHBACK", ""))])
def test_scene_headings(heading, parts):
    assert sp.split_heading(heading) == parts


def test_names_and_settings_for_prompts():
    assert [sp.nice_name(x) for x in ("RAY MERCER", "O'NEIL", "MARY-JANE", "McCLANE")] == ["Ray Mercer", "O'Neil", "Mary-Jane", "McCLANE"]
    assert sp.setting_text({"intext": "INT", "location": "THE GLASS HARBOUR DINER", "time": "LATE AFTERNOON"}) == "Inside The Glass Harbour Diner, late afternoon"
    assert sp.setting_text({"intext": "EXT", "location": "PIER", "time": ""}) == "Outside Pier"
    assert sp.setting_text(None) == ""


# ------------------------------------------------------------------ the Hollywood standard page
def test_the_layout_reproduces_the_real_pdf_line_for_line():
    """Paginated on the standard page, the screenplay read from The Glass Harbour.pdf falls on the same pages, line for line,
    as the PDF itself (its wrapping and its page breaks)."""
    pdf = open(PDF, "rb").read()
    pages, _info = sp.pdf_lines(pdf)
    real = [[" ".join(t for _a, _b, t in ln.segs if not (sp._MARGIN_MARK.match(t) and len(t) <= 5)) for ln in lines] for _w, _h, lines in pages[1:]]
    real = [[t for t in pg if t] for pg in real]
    mine = [[(" ".join(x.text for x in ln) if isinstance(ln, list) else ln.text) for ln in pg if ln is not None]
            for pg in sp.paginate(sp.parse(sp.fountain_from_pdf(pdf)[0])["elements"])]
    assert len(mine) == len(real) == 7
    assert mine == real


def test_the_measures_of_the_page():
    assert sp.LINES_A_PAGE == 55
    assert {k: sp.LAYOUT[k] for k in ("scene", "action", "dialogue", "parenthetical", "character", "transition")} == {
        "scene": (1.5, 59), "action": (1.5, 59), "dialogue": (2.5, 34), "parenthetical": (3.1, 24), "character": (3.7, 37), "transition": (6.0, 14)}
    assert sp.wrap("a" * 59 + " b", 59) == [(0, 59), (60, 61)]
    assert sp.wrap("kick-plates of the empty", 8) == [(0, 5), (5, 11), (12, 18), (19, 24)]   # after the hyphen, like a browser
    assert sp.wrap("one\n\ntwo", 10) == [(0, 3), (4, 4), (5, 8)]
    assert sp.styled("A *soft* **loud** _under_ \\*star\\* [[note]]") == ("A soft loud under *star* ", [0, 0, 1, 1, 1, 1, 0, 2, 2, 2, 2, 0, 4, 4, 4, 4, 4, 0, 0, 0, 0, 0, 0, 0, 0])


def _where(pdf):
    """Each printed line of an exported PDF: (page, top in lines, x in inches, text)."""
    pages, _ = sp.pdf_lines(pdf)
    return [(n, round((ln.y - 81) / 12), round(a / 72, 2), t) for n, (_w, _h, lines) in enumerate(pages) for ln in lines for a, _b, t in ln.segs]


def test_the_export_is_on_the_standard_page():
    p = sp.parse(open(FOUNTAIN, encoding="utf-8").read())
    pdf = sp.render_pdf(p["title_page"], p["elements"])
    rows = _where(pdf)
    at = lambda text: next((x, pg) for pg, _r, x, t in rows if t == text)   # noqa: E731
    assert at("INT. THE GLASS HARBOUR DINER - LATE AFTERNOON")[0] == 1.5      # scene heading
    assert at("Ray smiles. Not knowing how else to respond.")[0] == 1.5        # action
    assert at("You look like her.")[0] == 2.5                                 # dialogue
    assert at("(too fast, too loud)")[0] == 3.1                               # parenthetical
    assert at("RAY")[0] == 3.7                                                # character
    body = [r for r in rows if r[0] >= 1]
    assert all(0 <= r[1] <= 54 for r in body if r[1] >= 0)                    # 55 lines a page, 6 lines an inch
    numbers = [(pg, x, t) for pg, r, x, t in rows if r < 0]                   # above the first line: the page numbers
    assert numbers == [(pg, round(7.5 - len(f"{pg}.") * 0.1, 2), f"{pg}.") for pg in range(2, len(sp.paginate(p["elements"])) + 1)]
    assert (1, 0, 1.1, "1") in rows and (1, 0, 7.8, "1") in rows              # the scene number in both margins
    title = [t for pg, _r, _x, t in rows if pg == 0]
    assert title == ["THE GLASS HARBOUR", "Written by", "Alex Rivera", "08/28/2026"]            # the title in capitals
    assert all(x <= 7.5 - len(t) * 0.1 + 0.01 for pg, _r, x, t in body if x < 7.8)   # nothing crosses the right margin


def same(a, b):
    return same_screenplay([(e["type"], e["text"]) for e in a]) == same_screenplay([(e["type"], e["text"]) for e in b])


@pytest.mark.parametrize("source", ["fountain", "syntax"])
def test_the_export_reads_back_as_the_same_screenplay(source):
    text = open(FOUNTAIN, encoding="utf-8").read() if source == "fountain" else SYNTAX
    p = sp.parse(text)
    printed = [e for e in p["elements"] if e["type"] not in ("section", "synopsis", "page_break")]
    q = sp.parse(sp.fountain_from_pdf(sp.render_pdf(p["title_page"], p["elements"]))[0])
    back = [e for e in q["elements"] if e["type"] != "page_break"]
    # what a printed page can't tell apart: a Fountain-only heading with no INT./EXT. and no number (".FLASHBACK") and
    # lyrics are printed like action, so they come back as action
    printed = [dict(e, type="action") if e["type"] == "lyrics" or (e["type"] == "scene" and not sp.HEADING.match(e["text"]) and not e.get("number")) else e
               for e in printed]
    strip = lambda els: [dict(e, text=sp.styled(e["text"])[0].strip()) for e in els]   # noqa: E731  (emphasis is printed, not written)
    assert same(strip(back), strip(printed))
    assert [e.get("dual", False) for e in back if e["type"] == "character"] == [e.get("dual", False) for e in printed if e["type"] == "character"]
    assert [e.get("number") for e in back if e["type"] == "scene"] == [e.get("number") for e in printed if e["type"] == "scene"]


def test_page_breaks_follow_the_rules():
    long_speech = " ".join(f"Sentence number {k} of the speech goes on a while." for k in range(40))
    script = "\n\n".join(["INT. HALL - DAY", *[f"Action paragraph {k} is only one line." for k in range(23)],
                          "MARA\n" + long_speech, "INT. KITCHEN - NIGHT", "Mara sits.", "RAY\nHello.",
                          "\n".join(f"Line {k} of a long action paragraph, one sentence each line here." for k in range(30))])
    els = sp.parse(script)["elements"]
    pages = sp.paginate(els)
    for n, page in enumerate(pages):
        lines = [ln for ln in page if ln is not None]
        assert len(page) <= 55 and page[0] is not None and page[-1] is not None      # no blank line at a top or a foot
        last = lines[-1]
        assert last.kind not in ("scene", "character"), (n, last.text)                 # no heading or cue at the foot
        if last.kind == "more":                                                         # a speech breaks between sentences
            assert lines[-2].kind == "dialogue" and sp._SENTENCE_END.search(lines[-2].text)
            nxt = [ln for ln in pages[n + 1] if ln is not None]
            assert nxt[0].kind == "character" and nxt[0].text == "MARA (CONT'D)" and nxt[1].kind == "dialogue"
    assert sum(1 for pg in pages for ln in pg if ln is not None and ln.kind == "more") >= 1
    split = [pg for pg in pages if any(ln is not None and ln.text.startswith("Line ") for ln in pg)]
    if len(split) > 1:                                                                  # an action paragraph: two lines each side
        assert sum(1 for ln in split[0] if ln is not None and ln.text.startswith("Line ")) >= 2
        assert sum(1 for ln in split[1] if ln is not None and ln.text.startswith("Line ")) >= 2
    # the importer puts a speech broken over pages back together
    back = sp.parse(sp.fountain_from_pdf(sp.render_pdf([], els))[0])["elements"]
    said = next(e["text"] for e in back if e["type"] == "dialogue")
    assert same(back, els) and said == long_speech, (len(said), len(long_speech), said[:80], said[-80:])
    heading = next(n for n, pg in enumerate(pages) if any(ln is not None and ln.text == "INT. KITCHEN - NIGHT" for ln in pg))
    pg = [ln for ln in pages[heading] if ln is not None]
    k = next(i for i, ln in enumerate(pg) if ln.text == "INT. KITCHEN - NIGHT")
    assert k + 2 < len(pg)                                                              # the heading has its scene under it


def test_breakdown_sheet_fields():
    assert [sp.day_night(t, "NIGHT") for t in ("LATE AFTERNOON", "EVENING", "DAWN", "MAGIC HOUR", "CONTINUOUS", "MOMENTS LATER", "")] == \
        ["DAY", "NIGHT", "DAWN", "DUSK", "NIGHT", "NIGHT", ""]
    assert [sp.shot_letters(n) for n in (0, 7, 8, 12, 23, 24, 47)] == ["A", "H", "J", "N", "Z", "AA", "AZ"]   # no I, no O
    script = """INT. OFFICE - NIGHT #4#

Papers everywhere.

CLOSE ON THE LETTER

The signature is smudged.

SAM (V.O.)
I never sent it.

ANGLE ON THE WINDOW

Rain.

EXT. STREET - CONTINUOUS #5#

Sam runs. NELL follows.

NELL
Sam!

SAM
Go home.

.OMITTED #6#

INT. CAR - DAWN #7#

Sam drives. The car passes under a bridge.

SAM
Almost there.
"""
    bd = sp.breakdown(sp.parse(script))
    assert [(s["number"], s["dn"], s.get("omitted", False)) for s in bd["scenes"]] == [("4", "NIGHT", False), ("5", "NIGHT", False), ("6", "", True), ("7", "DAWN", False)]
    codes = sp.shot_codes(bd["scenes"], bd["shots"])
    got = [(codes[s["id"]], s["camera"], s["action"].split(".")[0]) for s in bd["shots"]]
    assert got == [("4A", "static", "Papers everywhere"), ("4B", "close-up", "CLOSE ON THE LETTER The signature is smudged"),
                   ("4C", "static", "ANGLE ON THE WINDOW Rain"), ("5A", "static", "Sam runs"), ("7A", "static", "Sam drives")]
    assert [(c["name"], c["cast"]) for c in bd["characters"]] == [("SAM", 1), ("NELL", 2)]
    assert [s["cast"] for s in bd["scenes"]] == [[1], [1, 2], [], [1]]
    whole = sp.parse(open(FOUNTAIN, encoding="utf-8").read())
    assert sp.breakdown(whole)["scenes"][0]["eighths"] == sum(max(1, round(len(pg) / 55 * 8)) for pg in sp.paginate(whole["elements"]))
    from_pdf = sp.parse(sp.fountain_from_pdf(open(PDF, "rb").read())[0])
    assert sp.breakdown(from_pdf)["scenes"][0]["eighths"] == 6 * 8 + 5          # six full pages and 33 lines of the seventh


# ------------------------------------------------------------------ found in the bug hunt
def test_a_script_that_opens_on_fade_in_has_no_title_page():
    p = sp.parse("FADE IN:\n\nINT. A - DAY\n\nA room.\n")
    assert p["title_page"] == [] and [(e["type"], e["text"]) for e in p["elements"]][:2] == [("action", "FADE IN:"), ("scene", "INT. A - DAY")]
    assert sp.parse("Title: X\nAuthor: Y\n\nFADE IN:\n")["title_page"] == [("Title", "X"), ("Author", "Y")]
    pages, _ = sp.pdf_lines(sp.render_pdf(p["title_page"], p["elements"]))
    assert len(pages) == 1                                 # no empty title page


@pytest.mark.parametrize("text", ["INT. A - DAY\n", "FADE IN:\n\nCUT TO:\n\nFADE OUT.\n", ".OMITTED #4#\n", "# ACT ONE\n\n= synopsis\n\nINT. A - DAY\n\nx\n"])
def test_a_short_script_exports_and_imports(text):
    p = sp.parse(text)
    printed = [(e["type"], e["text"]) for e in p["elements"] if e["type"] not in ("section", "synopsis", "page_break")]
    back = sp.parse(sp.fountain_from_pdf(sp.render_pdf(p["title_page"], p["elements"]))[0])
    assert [(e["type"], e["text"]) for e in back["elements"]] == printed


@pytest.mark.parametrize("name,text", [("huge paragraph", "INT. A - DAY\n\n" + "word " * 1500), ("huge speech", "INT. A - DAY\n\nMARA\n" + "word " * 1500),
                                       ("huge dual", "INT. A - DAY\n\nMARA\n" + "Go. " * 800 + "\n\nTOM ^\n" + "No. " * 800),
                                       ("beyond cp1252", "Title: 海 🌊\n\nINT. 海 - DAY\n\nMARA\n¡Hola! Ça va? 😀 — “quotes” 中文\n")])
def test_the_page_holds_up(name, text):
    p = sp.parse(text)
    pages = sp.paginate(p["elements"])
    assert pages and all(len(pg) <= sp.LINES_A_PAGE for pg in pages)
    assert sp.render_pdf(p["title_page"], p["elements"]).startswith(b"%PDF-")
    bd = sp.breakdown(p)
    messy = sp.breakdown(p, [dict(s, splits=["nope"], joins=["nah"]) for s in bd["scenes"]],
                         [dict(s, anchor=None) for s in bd["shots"]] + [{"id": "m", "manual": True, "after": "missing", "scene": bd["scenes"][0]["id"], "title": "x"}])
    assert [s["id"] for s in messy["shots"]][-1] == "m"
