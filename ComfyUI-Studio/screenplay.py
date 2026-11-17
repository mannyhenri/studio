"""Screenplays: read a PDF or a Fountain file into Fountain text, and parse Fountain into elements.

Pure Python with no dependencies (Studio runs inside whatever Python ComfyUI has), so it is importable on its own:

    fountain_from_pdf(data)   PDF bytes -> (fountain text, warnings)
    fountain_from_text(data)  .fountain / .txt bytes -> fountain text
    parse(text)               fountain text -> {"title_page", "elements", "scenes", "characters", "pages", ...}
    to_fountain(title, els)   elements -> fountain text

A PDF keeps no structure, only text drawn at positions, so the screenplay is read back from its layout: the columns
(action at the left margin, dialogue about an inch in, character cues about two inches in, transitions at the right),
the capitals and the parentheses. The Fountain rules are those of https://fountain.io/syntax."""
import base64
import re
import unicodedata
import zlib

__all__ = ["ScreenplayError", "fountain_from_pdf", "fountain_from_text", "parse", "to_fountain", "pdf_lines"]


class ScreenplayError(ValueError):
    """A file that can't be read as a screenplay; the message is for the user."""


# ================================================================== PDF objects
class Name(str):
    pass


class Kw(str):
    pass


class Ref(tuple):
    pass


class Stream:
    __slots__ = ("dict", "raw", "_data")

    def __init__(self, d, raw):
        self.dict, self.raw, self._data = d, raw, None


_TOKEN = re.compile(rb"""
    (?P<ws>[\x00\t\n\f\r ]+|%[^\r\n]*)
  | (?P<num>[+-]?(?:\d+\.?\d*|\.\d+))(?![^\x00\t\n\f\r ()<>\[\]{}/%])
  | (?P<name>/[^\x00\t\n\f\r ()<>\[\]{}/%]*)
  | (?P<dopen><<) | (?P<dclose>>>)
  | (?P<hstr><[0-9A-Fa-f\x00\t\n\f\r ]*>)
  | (?P<lstr>\()
  | (?P<aopen>\[) | (?P<aclose>\])
  | (?P<brace>[{}])
  | (?P<kw>[^\x00\t\n\f\r ()<>\[\]{}/%]+)
""", re.X)
_ESC = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
_NAME_HEX = re.compile(rb"#([0-9A-Fa-f]{2})")


def _literal(data, i):
    """A (literal string) starting after its '('; returns (bytes, position after ')')."""
    out, depth, n = bytearray(), 1, len(data)
    while i < n:
        c = data[i]
        if c == 0x5C:                                    # backslash
            i += 1
            if i >= n:
                break
            c = data[i]
            if c in _ESC:
                out.append(_ESC[c])
            elif 0x30 <= c <= 0x37:
                j = i
                while j < n and j < i + 3 and 0x30 <= data[j] <= 0x37:
                    j += 1
                out.append(int(data[i:j], 8) & 0xFF)
                i = j - 1
            elif c == 0x0D:
                if i + 1 < n and data[i + 1] == 0x0A:
                    i += 1
            elif c != 0x0A:
                out.append(c)
        elif c == 0x28:
            depth += 1
            out.append(c)
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
            out.append(c)
        else:
            out.append(c)
        i += 1
    return bytes(out), n


def _hex(tok):
    h = re.sub(rb"[^0-9A-Fa-f]", b"", tok[1:-1])
    if len(h) % 2:
        h += b"0"
    return bytes.fromhex(h.decode())


def _tokens(data, pos=0):
    """PDF tokens: ('n', number) ('/', Name) ('s', bytes) ('k', keyword) or the punctuation itself."""
    n = len(data)
    match = _TOKEN.match
    while pos < n:
        m = match(data, pos)
        if not m:                                        # a stray byte: skip it
            pos += 1
            continue
        kind = m.lastgroup
        end = m.end()
        if kind == "ws":
            pos = end
            continue
        if kind == "num":
            t = m.group()
            yield "n", (float(t) if b"." in t else int(t)), end
        elif kind == "name":
            yield "/", Name(_NAME_HEX.sub(lambda x: bytes.fromhex(x.group(1).decode()), m.group()[1:]).decode("latin-1")), end
        elif kind == "hstr":
            yield "s", _hex(m.group()), end
        elif kind == "lstr":
            s, end = _literal(data, end)
            yield "s", s, end
        elif kind == "kw":
            yield "k", Kw(m.group().decode("latin-1")), end
        else:
            yield m.group().decode(), None, end
        pos = end


class _Parser:
    """Parses PDF values from a token stream (numbers, names, strings, arrays, dictionaries, references, keywords)."""

    def __init__(self, data, pos=0):
        self.data = data
        self.it = _tokens(data, pos)
        self.buf = []
        self.pos = pos

    def _next(self):
        if self.buf:
            t = self.buf.pop()
        else:
            t = next(self.it, None)
        if t is not None:
            self.pos = t[2]
        return t

    def _push(self, t):
        self.buf.append(t)

    def value(self):
        t = self._next()
        if t is None:
            raise EOFError
        kind, v, _ = t
        if kind == "n":
            if isinstance(v, int) and v >= 0:          # maybe "num gen R"
                t2 = self._next()
                if t2 and t2[0] == "n" and isinstance(t2[1], int):
                    t3 = self._next()
                    if t3 and t3[0] == "k" and t3[1] == "R":
                        return Ref((v, t2[1]))
                    if t3:
                        self._push(t3)
                    self._push(t2)
                elif t2:
                    self._push(t2)
            return v
        if kind in ("/", "s"):
            return v
        if kind == "k":
            return {"true": True, "false": False, "null": None}.get(v, v)
        if kind == "[":
            out = []
            while True:
                t = self._next()
                if t is None or t[0] == "]":
                    return out
                self._push(t)
                out.append(self.value())
        if kind == "<<":
            d = {}
            while True:
                t = self._next()
                if t is None or t[0] == ">>":
                    return d
                if t[0] != "/":
                    continue
                d[t[1]] = self.value()
        return Kw(kind)                                   # a stray ], >>, { or }


_OBJ = re.compile(rb"(?<![0-9])(\d+)[\x00\t\n\f\r ]+(\d+)[\x00\t\n\f\r ]+obj\b")


def _unpredict(data, parms):
    pred = int(parms.get("Predictor") or 1)
    if pred < 10:
        return data
    cols, colors, bpc = int(parms.get("Columns") or 1), int(parms.get("Colors") or 1), int(parms.get("BitsPerComponent") or 8)
    bpp, row = max(1, colors * bpc // 8), (cols * colors * bpc + 7) // 8
    out, prev = bytearray(), bytearray(row)
    for i in range(0, len(data) - row, row + 1):
        ft, line = data[i], bytearray(data[i + 1:i + 1 + row])
        for k in range(len(line)):
            a = line[k - bpp] if k >= bpp else 0
            b, c = prev[k], (prev[k - bpp] if k >= bpp else 0)
            if ft == 1:
                line[k] = (line[k] + a) & 0xFF
            elif ft == 2:
                line[k] = (line[k] + b) & 0xFF
            elif ft == 3:
                line[k] = (line[k] + (a + b) // 2) & 0xFF
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[k] = (line[k] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        out += line
        prev = line
    return bytes(out)


def _lzw(data):
    out, table, bits, buf, nbits, prev = bytearray(), [bytes([i]) for i in range(256)] + [b"", b""], 9, 0, 0, None
    for byte in data:
        buf, nbits = (buf << 8) | byte, nbits + 8
        while nbits >= bits:
            nbits -= bits
            code = (buf >> nbits) & ((1 << bits) - 1)
            if code == 256:
                table, bits, prev = table[:258], 9, None
                continue
            if code == 257:
                return bytes(out)
            if prev is None:
                entry = table[code]
            else:
                entry = table[code] if code < len(table) else prev + prev[:1]
                table.append(prev + entry[:1])
            out += entry
            prev = entry
            if len(table) + 1 >= (1 << bits) and bits < 12:
                bits += 1
    return bytes(out)


def _decode(raw, filters, parms):
    if not isinstance(filters, list):
        filters = [filters] if filters else []
    if not isinstance(parms, list):
        parms = [parms] * len(filters)
    data = raw
    for f, p in zip(filters, parms + [None] * len(filters)):
        p = p if isinstance(p, dict) else {}
        if f in ("FlateDecode", "Fl"):
            try:
                data = zlib.decompress(data)
            except zlib.error:
                try:
                    data = zlib.decompressobj().decompress(data)
                except zlib.error:
                    data = b""
            data = _unpredict(data, p)
        elif f in ("ASCIIHexDecode", "AHx"):
            data = _hex(b"<" + data.split(b">")[0] + b">")
        elif f in ("ASCII85Decode", "A85"):
            s = re.sub(rb"\s", b"", data)
            s = s[2:] if s.startswith(b"<~") else s
            s = s.split(b"~>")[0]
            try:
                data = base64.a85decode(s)
            except ValueError:
                data = b""
        elif f in ("LZWDecode", "LZW"):
            data = _unpredict(_lzw(data), p)
        elif f in ("RunLengthDecode", "RL"):
            out, i = bytearray(), 0
            while i < len(data) and data[i] != 128:
                n = data[i]
                if n < 128:
                    out += data[i + 1:i + 2 + n]
                    i += n + 2
                else:
                    out += data[i + 1:i + 2] * (257 - n)
                    i += 2
            data = bytes(out)
        else:                                            # images (DCT, JPX, CCITT...): not text
            return b""
    return data


class _Doc:
    """The objects of a PDF file, found by scanning (so damaged cross-reference tables don't matter)."""

    def __init__(self, data):
        if not data.lstrip()[:5] == b"%PDF-" and b"%PDF-" not in data[:1024]:
            raise ScreenplayError("This isn't a PDF file.")
        self.data = data
        self.objs = {}
        self.trailers = []
        pos = 0
        while True:
            m = _OBJ.search(data, pos)
            if not m:
                break
            num = int(m.group(1))
            p = _Parser(data, m.end())
            try:
                val = p.value()
            except Exception:
                pos = m.end()
                continue
            end = p.pos
            s = re.compile(rb"[\x00\t\n\f\r ]*stream(\r\n|\n|\r)").match(data, end)
            if isinstance(val, dict) and s:
                start = s.end()
                length = val.get("Length")
                stop = None
                if isinstance(length, int) and re.match(rb"[\x00\t\n\f\r ]*endstream", data[start + length:start + length + 30]):
                    stop = start + length
                if stop is None:
                    e = data.find(b"endstream", start)
                    stop = len(data) if e < 0 else e
                    while stop > start and data[stop - 1] in b"\r\n":
                        stop -= 1
                val = Stream(val, data[start:stop])
                end = stop
                if val.dict.get("Type") == "XRef":
                    self.trailers.append(val.dict)
            self.objs[num] = val
            pos = max(end, m.end())
        for m in re.finditer(rb"trailer[\x00\t\n\f\r ]*<<", data):
            try:
                t = _Parser(data, m.end() - 2).value()
                if isinstance(t, dict):
                    self.trailers.append(t)
            except Exception:
                pass
        for o in list(self.objs.values()):             # objects packed in object streams (PDF 1.5+)
            if isinstance(o, Stream) and o.dict.get("Type") == "ObjStm":
                try:
                    self._objstm(o)
                except Exception:
                    pass

    def _objstm(self, st):
        d = self.stream_data(st)
        n, first = int(st.dict.get("N") or 0), int(st.dict.get("First") or 0)
        nums = []
        it = _tokens(d[:first])
        for _ in range(2 * n):
            t = next(it, None)
            if t is None:
                break
            nums.append(t[1])
        for k in range(0, len(nums) - 1, 2):
            num, off = nums[k], nums[k + 1]
            if num in self.objs:
                continue
            try:
                self.objs[num] = _Parser(d, first + off).value()
            except Exception:
                pass

    def get(self, v, depth=0):
        while isinstance(v, Ref) and depth < 32:
            v = self.objs.get(v[0])
            depth += 1
        return v

    def stream_data(self, st):
        if st._data is None:
            d = st.dict
            st._data = _decode(st.raw, self.get(d.get("Filter")), self.get(d.get("DecodeParms") or d.get("DP")))
        return st._data

    def trailer(self, key):
        for t in reversed(self.trailers):
            if key in t:
                return self.get(t[key])
        return None

    def info(self):
        i = self.trailer("Info")
        return {k: _text_string(v) for k, v in i.items() if isinstance(v, bytes)} if isinstance(i, dict) else {}

    def pages(self):
        out = []

        def walk(node, inherited, seen):
            node = self.get(node)
            if not isinstance(node, dict) or id(node) in seen:
                return
            seen.add(id(node))
            inh = dict(inherited)
            for k in ("Resources", "MediaBox", "CropBox"):
                if k in node:
                    inh[k] = self.get(node[k])
            kids = self.get(node.get("Kids"))
            if isinstance(kids, list) and node.get("Type") != "Page":
                for k in kids:
                    walk(k, inh, seen)
            else:
                page = dict(node)
                page.update({k: v for k, v in inh.items() if k not in node or k in ("Resources", "MediaBox", "CropBox")})
                out.append(page)
        root = self.trailer("Root")
        if isinstance(root, dict):
            walk(root.get("Pages"), {}, set())
        if not out:                                      # no usable page tree: every page object, in file order
            out = [o for _n, o in sorted(self.objs.items()) if isinstance(o, dict) and o.get("Type") == "Page"]
        return out


def _text_string(b):
    if b[:2] == b"\xfe\xff":
        return b[2:].decode("utf-16-be", "replace")
    if b[:3] == b"\xef\xbb\xbf":
        return b[3:].decode("utf-8", "replace")
    return b.decode("latin-1")


# ================================================================== fonts and encodings
_ACCENTS = {"acute": "\u0301", "grave": "\u0300", "circumflex": "\u0302", "dieresis": "\u0308", "tilde": "\u0303", "ring": "\u030a",
            "cedilla": "\u0327", "caron": "\u030c", "macron": "\u0304", "breve": "\u0306", "ogonek": "\u0328", "dotaccent": "\u0307",
            "hungarumlaut": "\u030b", "commaaccent": "\u0326"}
_GLYPHS = dict(zip("space exclam quotedbl numbersign dollar percent ampersand quotesingle parenleft parenright asterisk plus comma "
                   "hyphen period slash zero one two three four five six seven eight nine colon semicolon less equal greater question "
                   "at bracketleft backslash bracketright asciicircum underscore grave braceleft bar braceright asciitilde".split(),
                   " !\"#$%&'()*+,-./0123456789:;<=>?@[\\]^_`{|}~"))
_GLYPHS.update({
    "quoteright": "\u2019", "quoteleft": "\u2018", "quotedblleft": "\u201c", "quotedblright": "\u201d", "quotesinglbase": "\u201a",
    "quotedblbase": "\u201e", "guillemotleft": "\u00ab", "guillemotright": "\u00bb", "guilsinglleft": "\u2039", "guilsinglright": "\u203a",
    "endash": "\u2013", "emdash": "\u2014", "bullet": "\u2022", "ellipsis": "\u2026", "dagger": "\u2020", "daggerdbl": "\u2021",
    "periodcentered": "\u00b7", "paragraph": "\u00b6", "section": "\u00a7", "perthousand": "\u2030", "exclamdown": "\u00a1",
    "questiondown": "\u00bf", "cent": "\u00a2", "sterling": "\u00a3", "yen": "\u00a5", "Euro": "\u20ac", "euro": "\u20ac", "florin": "\u0192",
    "currency": "\u00a4", "fraction": "\u2044", "fi": "fi", "fl": "fl", "ff": "ff", "ffi": "ffi", "ffl": "ffl", "AE": "\u00c6", "ae": "\u00e6",
    "OE": "\u0152", "oe": "\u0153", "Oslash": "\u00d8", "oslash": "\u00f8", "Lslash": "\u0141", "lslash": "\u0142", "germandbls": "\u00df",
    "dotlessi": "\u0131", "Eth": "\u00d0", "eth": "\u00f0", "Thorn": "\u00de", "thorn": "\u00fe", "ordfeminine": "\u00aa",
    "ordmasculine": "\u00ba", "copyright": "\u00a9", "registered": "\u00ae", "trademark": "\u2122", "degree": "\u00b0", "minus": "\u2212",
    "multiply": "\u00d7", "divide": "\u00f7", "plusminus": "\u00b1", "mu": "\u00b5", "logicalnot": "\u00ac", "brokenbar": "\u00a6",
    "onehalf": "\u00bd", "onequarter": "\u00bc", "threequarters": "\u00be", "onesuperior": "\u00b9", "twosuperior": "\u00b2",
    "threesuperior": "\u00b3", "nbspace": " ", "nonbreakingspace": " ", "sfthyphen": "-", "softhyphen": "-", "acute": "\u00b4",
    "dieresis": "\u00a8", "macron": "\u00af", "cedilla": "\u00b8", "circumflex": "\u02c6", "tilde": "\u02dc", "ring": "\u02da",
    "caron": "\u02c7", "breve": "\u02d8", "dotaccent": "\u02d9", "hungarumlaut": "\u02dd", "ogonek": "\u02db", "quotesingle.": "'",
})
_STANDARD_HIGH = dict(zip(
    [0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF, 0xB1, 0xB2, 0xB3, 0xB4, 0xB6, 0xB7,
     0xB8, 0xB9, 0xBA, 0xBB, 0xBC, 0xBD, 0xBF, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF, 0xD0,
     0xE1, 0xE3, 0xE8, 0xE9, 0xEA, 0xEB, 0xF1, 0xF5, 0xF8, 0xF9, 0xFA, 0xFB],
    "exclamdown cent sterling fraction yen florin section currency quotesingle quotedblleft guillemotleft guilsinglleft "
    "guilsinglright fi fl endash dagger daggerdbl periodcentered paragraph bullet quotesinglbase quotedblbase quotedblright "
    "guillemotright ellipsis perthousand questiondown grave acute circumflex tilde macron breve dotaccent dieresis ring cedilla "
    "hungarumlaut ogonek caron emdash AE ordfeminine Lslash Oslash OE ordmasculine ae dotlessi lslash oslash oe germandbls".split()))


def glyph_text(name):
    """The text of a glyph name (Adobe Glyph List conventions: 'a', 'eacute', 'quoteright', 'uni2014', 'f_i', 'A.sc')."""
    name = str(name)
    if name in _GLYPHS:
        return _GLYPHS[name]
    base = name.split(".")[0]
    if not base:
        return ""
    if "_" in base:
        return "".join(glyph_text(p) for p in base.split("_"))
    if base in _GLYPHS:
        return _GLYPHS[base]
    if len(base) == 1:
        return base
    m = re.fullmatch(r"uni((?:[0-9A-F]{4})+)", base)
    if m:
        return "".join(chr(int(m.group(1)[i:i + 4], 16)) for i in range(0, len(m.group(1)), 4))
    m = re.fullmatch(r"u([0-9A-F]{4,6})", base)
    if m:
        return chr(int(m.group(1), 16))
    for acc, comb in _ACCENTS.items():
        if base.endswith(acc) and len(base) > len(acc) and (len(base) - len(acc) == 1 or base[:-len(acc)] in ("dotless", "AE", "ae")):
            return unicodedata.normalize("NFC", base[:-len(acc)] + comb)
    return ""


def _base_encoding(name):
    """code -> text for a simple font's base encoding."""
    if name == "MacRomanEncoding":
        return {c: bytes([c]).decode("mac_roman") for c in range(32, 256)}
    if name == "StandardEncoding":
        enc = {c: chr(c) for c in range(32, 127)}
        enc[0x27], enc[0x60] = "\u2019", "\u2018"
        enc.update({c: glyph_text(n) for c, n in _STANDARD_HIGH.items()})
        return enc
    enc = {}
    for c in range(32, 256):                             # WinAnsiEncoding (and the default)
        try:
            enc[c] = bytes([c]).decode("cp1252")
        except UnicodeDecodeError:
            pass
    enc[0xA0], enc[0xAD] = " ", "-"
    return enc


def _parse_cmap(data):
    """A ToUnicode CMap: (code -> text, code lengths in bytes)."""
    out, sizes = {}, set()

    def hexs(b):
        return bytes.fromhex(re.sub(rb"[^0-9A-Fa-f]", b"", b).decode())

    def u16(b):
        return b.decode("utf-16-be", "ignore") if len(b) % 2 == 0 else b.decode("latin-1")

    for blk in re.findall(rb"begincodespacerange(.*?)endcodespacerange", data, re.S):
        for lo in re.findall(rb"<([0-9A-Fa-f\s]+)>\s*<[0-9A-Fa-f\s]+>", blk):
            sizes.add(len(hexs(lo)))
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]*)>", blk):
            s = hexs(src)
            sizes.add(len(s))
            out[int.from_bytes(s, "big")] = u16(hexs(dst))
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>\s*(<[0-9A-Fa-f\s]*>|\[[^\]]*\])", blk):
            a, b = int.from_bytes(hexs(lo), "big"), int.from_bytes(hexs(hi), "big")
            sizes.add(len(hexs(lo)))
            if b - a > 65535:
                continue
            if dst.startswith(b"["):
                for k, d in enumerate(re.findall(rb"<([0-9A-Fa-f\s]*)>", dst)):
                    if a + k <= b:
                        out[a + k] = u16(hexs(d))
            else:
                d = hexs(dst[1:-1])
                if not d:
                    continue
                base, width = int.from_bytes(d, "big"), len(d)
                for k in range(b - a + 1):
                    try:
                        out[a + k] = u16((base + k).to_bytes(width, "big"))
                    except OverflowError:
                        break
    return out, sizes


class _Font:
    def __init__(self, doc, d):
        self.doc = doc
        d = d if isinstance(d, dict) else {}
        sub = d.get("Subtype")
        self.composite = sub == "Type0"
        self.widths, self.map, self.code_bytes = {}, {}, 1
        self.scale = 0.001
        base = str(d.get("BaseFont") or "")
        tu = doc.get(d.get("ToUnicode"))
        tu_map, tu_sizes = _parse_cmap(doc.stream_data(tu)) if isinstance(tu, Stream) else ({}, set())
        if self.composite:
            self.code_bytes = 2
            enc = doc.get(d.get("Encoding"))
            desc = doc.get((doc.get(d.get("DescendantFonts")) or [None])[0])
            desc = desc if isinstance(desc, dict) else {}
            self.default_width = float(desc.get("DW") or 1000)
            w = doc.get(desc.get("W")) or []
            i = 0
            while i < len(w):
                first, nxt = doc.get(w[i]), doc.get(w[i + 1]) if i + 1 < len(w) else None
                if isinstance(nxt, list):
                    for k, x in enumerate(nxt):
                        self.widths[int(first) + k] = float(doc.get(x) or 0)
                    i += 2
                elif i + 2 < len(w):
                    for c in range(int(first), int(nxt) + 1):
                        self.widths[c] = float(doc.get(w[i + 2]) or 0)
                    i += 3
                else:
                    break
            if isinstance(enc, str) and enc not in ("Identity-H", "Identity-V") and not tu_map:
                # a predefined Unicode CMap (UniGB-UCS2-H, UniJIS-UTF16-H...): the codes are UTF-16 already
                self.map = None if "UCS2" in enc or "UTF16" in enc else {}
            if tu_sizes == {1}:
                self.code_bytes = 1
        else:
            self.default_width = 0.0
            fd = doc.get(d.get("FontDescriptor"))
            fd = fd if isinstance(fd, dict) else {}
            if sub == "Type3":
                fm = doc.get(d.get("FontMatrix")) or [0.001]
                self.scale = float(doc.get(fm[0]) or 0.001)
            first = int(doc.get(d.get("FirstChar")) or 0)
            for k, x in enumerate(doc.get(d.get("Widths")) or []):
                self.widths[first + k] = float(doc.get(x) or 0)
            self.default_width = float(doc.get(fd.get("MissingWidth")) or 0) or (600.0 if "courier" in base.lower() else 500.0)
            if not self.widths and "courier" in base.lower():
                self.default_width = 600.0
            enc = doc.get(d.get("Encoding"))
            base_name, diffs = None, []
            if isinstance(enc, str):
                base_name = enc
            elif isinstance(enc, dict):
                base_name, diffs = doc.get(enc.get("BaseEncoding")), doc.get(enc.get("Differences")) or []
            if base_name is None and sub == "Type1" and not tu_map:
                builtin = self._type1_encoding(fd)
                if builtin:
                    self.map = builtin
            if not self.map:
                self.map = _base_encoding(base_name or ("StandardEncoding" if sub == "Type1" and "courier" not in base.lower() else "WinAnsiEncoding"))
            code = 0
            for x in diffs:
                x = doc.get(x)
                if isinstance(x, (int, float)):
                    code = int(x)
                elif isinstance(x, str):
                    g = glyph_text(x)
                    m = None if g else re.fullmatch(r"[A-Za-z]{1,3}(\d{2,3})", x)
                    if g:
                        self.map[code] = g
                    elif m and 32 <= int(m.group(1)) < 127:  # generated fonts name glyphs by code: /a65 is "A"
                        self.map[code] = chr(int(m.group(1)))
                    code += 1
        if tu_map:
            if self.map is None:
                self.map = {}
            self.map.update(tu_map)

    def _type1_encoding(self, fd):
        ff = self.doc.get(fd.get("FontFile"))
        if not isinstance(ff, Stream):
            return None
        data = self.doc.stream_data(ff)
        n = ff.dict.get("Length1")
        clear = data[:n] if isinstance(n, int) and n > 0 else data[:20000]
        if b"StandardEncoding def" in clear:
            return None
        enc = {int(c): glyph_text(g.decode("latin-1")) for c, g in re.findall(rb"dup\s+(\d+)\s*/(\S+)\s+put", clear)}
        return enc or None

    def decode(self, s):
        """[(text, width in text-space units per 1 unit of font size, is a single-byte space)]"""
        out = []
        if self.code_bytes == 2:
            if len(s) % 2:
                s = s + b"\x00"
            for i in range(0, len(s), 2):
                c = (s[i] << 8) | s[i + 1]
                t = chr(c) if self.map is None else self.map.get(c, "")
                out.append((t, self.widths.get(c, self.default_width) * self.scale, False))
        else:
            m = self.map
            for c in s:
                out.append((m.get(c, ""), self.widths.get(c, self.default_width) * self.scale, c == 32))
        return out


# ================================================================== content streams -> text runs
def _mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


_ID = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_INLINE_END = re.compile(rb"[\x00\t\n\f\r ]EI(?=[\x00\t\n\f\r ]|$)")


class _Run:
    __slots__ = ("x0", "x1", "y", "size", "text")

    def __init__(self, x0, x1, y, size, text):
        self.x0, self.x1, self.y, self.size, self.text = x0, x1, y, size, text

    def __repr__(self):
        return f"Run({self.x0:.1f}-{self.x1:.1f} @{self.y:.1f} {self.text!r})"


class _Page:
    def __init__(self, doc, page):
        self.doc = doc
        self.fonts = {}
        self.runs = []
        box = doc.get(page.get("CropBox")) or doc.get(page.get("MediaBox")) or [0, 0, 612, 792]
        try:
            box = [float(doc.get(v)) for v in box]
        except (TypeError, ValueError):
            box = [0, 0, 612, 792]
        self.x0, self.y0, self.width, self.height = min(box[0], box[2]), min(box[1], box[3]), abs(box[2] - box[0]), abs(box[3] - box[1])
        contents = doc.get(page.get("Contents"))
        parts = contents if isinstance(contents, list) else [contents]
        data = b"\n".join(doc.stream_data(s) for s in (doc.get(p) for p in parts) if isinstance(s, Stream))
        self._run(data, doc.get(page.get("Resources")) or {}, _ID, 0)

    def _font(self, res, name):
        fonts = self.doc.get(res.get("Font")) or {}
        ref = fonts.get(name) if isinstance(fonts, dict) else None
        key = ref if isinstance(ref, Ref) else (id(res), name)
        if key not in self.fonts:
            self.fonts[key] = _Font(self.doc, self.doc.get(ref)) if ref is not None else None
        return self.fonts[key]

    def _run(self, data, res, ctm, depth):
        res = res if isinstance(res, dict) else {}
        stack = []
        tm = tlm = _ID
        tc = tw = rise = 0.0
        th, tl, fs = 1.0, 0.0, 1.0
        font = None
        ops = []
        p = _Parser(data)
        runs = self.runs

        def show(s):
            nonlocal tm
            if font is None:
                return
            m = _mul(tm, ctm)
            upright = m[0] > 0 and m[3] > 0 and abs(m[1]) < 0.05 * m[0] and abs(m[2]) < 0.05 * m[3]
            x, text, start, last_w = 0.0, [], None, 0.0
            for t, w, sp in font.decode(s):
                if start is None:
                    start = x
                text.append(t)
                last_w = w * fs * th
                x += (w * fs + tc + (tw if sp else 0.0)) * th
            if upright and text:
                size = fs * m[3]
                x0 = start * m[0] + rise * m[2] + m[4]
                x1 = (x - (tc + (tw if s[-1:] == b" " else 0.0)) * th if s else x) * m[0] + rise * m[2] + m[4]
                if x1 < x0 + last_w * m[0] * 0.5:
                    x1 = x0 + max(0.0, x - start) * m[0]
                runs.append(_Run(x0, x1, start * m[1] + rise * m[3] + m[5], size, "".join(text)))
            tm = _mul((1.0, 0.0, 0.0, 1.0, x, 0.0), tm)

        while True:
            try:
                v = p.value()
            except EOFError:
                break
            except Exception:
                break
            if not isinstance(v, Kw):
                ops.append(v)
                if len(ops) > 64:
                    del ops[:-64]
                continue
            op = str(v)
            try:
                if op == "Tj" and ops:
                    show(ops[-1])
                elif op == "TJ" and ops and isinstance(ops[-1], list):
                    for item in ops[-1]:
                        if isinstance(item, bytes):
                            show(item)
                        elif isinstance(item, (int, float)):
                            tm = _mul((1.0, 0.0, 0.0, 1.0, -item / 1000.0 * fs * th, 0.0), tm)
                elif op in ("'", '"'):
                    if op == '"' and len(ops) >= 3:
                        tw, tc = float(ops[-3]), float(ops[-2])
                    tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -tl), tlm)
                    tm = tlm
                    if ops:
                        show(ops[-1])
                elif op == "Td" and len(ops) >= 2:
                    tlm = _mul((1.0, 0.0, 0.0, 1.0, float(ops[-2]), float(ops[-1])), tlm)
                    tm = tlm
                elif op == "TD" and len(ops) >= 2:
                    tl = -float(ops[-1])
                    tlm = _mul((1.0, 0.0, 0.0, 1.0, float(ops[-2]), float(ops[-1])), tlm)
                    tm = tlm
                elif op == "T*":
                    tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -tl), tlm)
                    tm = tlm
                elif op == "Tm" and len(ops) >= 6:
                    tlm = tm = tuple(float(x) for x in ops[-6:])
                elif op == "Tf" and len(ops) >= 2:
                    font, fs = self._font(res, ops[-2]), float(ops[-1])
                elif op == "Tc" and ops:
                    tc = float(ops[-1])
                elif op == "Tw" and ops:
                    tw = float(ops[-1])
                elif op == "Tz" and ops:
                    th = float(ops[-1]) / 100.0
                elif op == "TL" and ops:
                    tl = float(ops[-1])
                elif op == "Ts" and ops:
                    rise = float(ops[-1])
                elif op == "BT":
                    tm = tlm = _ID
                elif op == "q":
                    stack.append((ctm, font, fs, tc, tw, th, tl, rise))
                elif op == "Q" and stack:
                    ctm, font, fs, tc, tw, th, tl, rise = stack.pop()
                elif op == "cm" and len(ops) >= 6:
                    ctm = _mul(tuple(float(x) for x in ops[-6:]), ctm)
                elif op == "Do" and ops and depth < 8:
                    xo = self.doc.get((self.doc.get(res.get("XObject")) or {}).get(ops[-1]))
                    if isinstance(xo, Stream) and xo.dict.get("Subtype") == "Form":
                        mtx = tuple(float(self.doc.get(x)) for x in (self.doc.get(xo.dict.get("Matrix")) or _ID))
                        self._run(self.doc.stream_data(xo), self.doc.get(xo.dict.get("Resources")) or res, _mul(mtx, ctm), depth + 1)
                elif op == "BI":                       # inline image: skip its data
                    m = _INLINE_END.search(data, p.pos)
                    p = _Parser(data, m.end() if m else len(data))
            except (TypeError, ValueError, IndexError, ZeroDivisionError):
                pass
            ops = []


def pdf_pages(data):
    """Each page's text runs: [(page width, page height, [_Run])], y measured from the top of the page."""
    doc = _Doc(data)
    if any("Encrypt" in t for t in doc.trailers):       # even if the dictionary it names is missing: the streams are encrypted
        raise ScreenplayError("This PDF is password-protected or encrypted. Export it again without protection "
                              "(or export a .fountain file from your screenplay app) and import that.")
    out = []
    for pg in doc.pages():
        try:
            p = _Page(doc, pg)
        except RecursionError:
            continue
        for r in p.runs:
            r.x0, r.x1, r.y = r.x0 - p.x0, r.x1 - p.x0, p.height - (r.y - p.y0)
        out.append((p.width, p.height, p.runs))
    return out, doc.info()


# ================================================================== runs -> lines
class Line:
    """One printed line: its baseline (from the top of the page), font size and segments [(x0, x1, text)] split where
    the gap is wider than a few characters (scene numbers in the margins, revision marks, the two sides of dual dialogue)."""
    __slots__ = ("page", "y", "size", "segs", "cw")

    def __init__(self, page, y, size, segs, cw):
        self.page, self.y, self.size, self.segs, self.cw = page, y, size, segs, cw

    @property
    def x0(self):
        return self.segs[0][0]

    @property
    def x1(self):
        return self.segs[-1][1]

    @property
    def text(self):
        return " ".join(s[2] for s in self.segs)

    def __repr__(self):
        return f"Line(p{self.page} y={self.y:.1f} " + " | ".join(f"{a:.1f}-{b:.1f} {t!r}" for a, b, t in self.segs) + ")"


def _char_width(runs):
    """The typical width of one character (a screenplay is Courier: 0.6 of the font size)."""
    ws = sorted((r.x1 - r.x0) / len(r.text) for r in runs if len(r.text.strip()) >= 4 and r.x1 > r.x0)
    return ws[len(ws) // 2] if ws else 7.2


def _page_lines(pageno, runs):
    runs = [r for r in runs if r.text.strip()]
    if not runs:
        return []
    runs.sort(key=lambda r: (round(r.y, 1), r.x0))
    rows = []
    for r in runs:                                       # same baseline (within a third of the font size) = same line
        for row in reversed(rows[-3:]):
            if abs(row[0].y - r.y) < 0.35 * max(r.size, row[0].size, 1):
                row.append(r)
                break
        else:
            rows.append([r])
    out = []
    for row in rows:
        row.sort(key=lambda r: r.x0)
        dedup = []
        for r in row:                                    # bold drawn twice, slightly offset
            if dedup and r.text == dedup[-1].text and abs(r.x0 - dedup[-1].x0) < 0.3 * r.size:
                continue
            dedup.append(r)
        cw = _char_width(dedup) if any(len(r.text) >= 4 for r in dedup) else 0.6 * max(r.size for r in dedup)
        segs = []
        for r in dedup:
            t = r.text
            if segs:
                a, b, prev = segs[-1]
                gap = r.x0 - b
                marks = set(t.strip()) == {"*"} or set(prev.strip()) == {"*"}     # revision marks stand apart
                if gap < 2.5 * cw and not (marks and gap > 0.5 * cw):
                    sp = gap > 0.25 * cw and not prev.endswith(" ") and not t.startswith(" ")
                    segs[-1] = (a, max(b, r.x1), prev + (" " if sp else "") + t)
                    continue
            segs.append((r.x0, r.x1, t))
        segs = [(a, b, re.sub(r"\s+", " ", t).strip()) for a, b, t in segs]
        segs = [s for s in segs if s[2]]
        if segs:
            ys = sorted(r.y for r in row)
            out.append(Line(pageno, ys[len(ys) // 2], max(r.size for r in row), segs, cw))
    out.sort(key=lambda ln: (ln.y, ln.x0))
    return out


def pdf_lines(data):
    """The printed lines of every page, top to bottom, and the document info (Title, Author...)."""
    pages, info = pdf_pages(data)
    return [(w, h, _page_lines(k, runs)) for k, (w, h, runs) in enumerate(pages)], info


# ================================================================== Fountain
HEADING = re.compile(r"^(?:INT\.?/EXT|EXT\.?/INT|INT|EXT|EST|I/E|E/I)[. ]", re.I)
SCENE_NUMBER = re.compile(r"\s*#([\w.\-]+)#\s*$")
_ABBREV_END = re.compile(r"\b(?:MR|MRS|MS|DR|JR|SR|ST|LT|SGT|CPL|PVT|CAPT|COL|GEN|PROF|REV|NO)\.$")
_EXTENSION = re.compile(r"\s*\([^)]*\)\s*")


def is_cue(s):
    """Could this line be a character cue? All capitals (extensions in parentheses may be lower case), a name-like length,
    no sentence punctuation. "RAY", "JUNE (CONT'D)", "DR. HALE (V.O.)", "COP #2" yes; "ONE SHOT. NO CUTS, GO." no."""
    s = s.strip()
    if s.startswith("@"):
        return len(s) > 1
    name = _EXTENSION.sub(" ", s.rstrip("^").strip()).strip()
    if not name or not any(c.isalpha() for c in name) or name != name.upper() or len(name) > 38:
        return False
    if re.search(r"[,!?:;\"“”]", name) or HEADING.match(name):
        return False
    if name.endswith(".") and not _ABBREV_END.search(name):
        return False
    return True


def cue_name(s):
    """The character a cue names: 'JUNE (CONT'D)' -> 'JUNE', '@McCLANE ^' -> 'McCLANE'."""
    return re.sub(r"\s+", " ", _EXTENSION.sub(" ", s.strip().lstrip("@").rstrip("^").strip())).strip()


def _strip_boneyard(text):
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), text, flags=re.S)


TITLE_KEYS = {"title", "credit", "author", "authors", "source", "draft date", "date", "contact", "copyright", "notes", "revision",
              "format", "font", "watermark", "tl", "tc", "tr", "cc", "bl", "br"}


def _title_page(lines):
    """Fountain's title page (Key: value lines up to the first blank line, starting with a title page key, so a script
    that opens on FADE IN: has none) -> ([(key, value)], index of the first body line)."""
    first = re.match(r"^([A-Za-z][A-Za-z ]*):", lines[0]) if lines else None
    if not first or first.group(1).strip().lower() not in TITLE_KEYS:
        return [], 0
    pairs, i = [], 0
    while i < len(lines) and lines[i].strip():
        ln = lines[i]
        m = re.match(r"^([A-Za-z][A-Za-z ]*):\s*(.*)$", ln)
        if m and not ln[:1].isspace():
            pairs.append([m.group(1).strip(), m.group(2).strip()])
        elif pairs and (ln[:1].isspace()):
            pairs[-1][1] = (pairs[-1][1] + "\n" + ln.strip()).strip("\n")
        else:
            return [], 0                                 # not a title page after all
        i += 1
    return [tuple(p) for p in pairs], i


def parse(text):
    """Fountain text -> {"title_page": [(key, value)], "title", "elements": [{"type", "text", "line", ...}], "scenes",
    "characters": [{"name", "lines"}], "pages"}. Types: scene, action, character, parenthetical, dialogue, transition,
    centered, lyrics, section, synopsis, page_break. "line" is the 0-based line of the source where the element starts."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").replace("\t", "    ")
    lines = _strip_boneyard(text).split("\n")
    tp, i = _title_page(lines)
    n = len(lines)
    els = []

    def blank(k):
        return k < 0 or k >= n or not lines[k].strip()

    def add(kind, txt, line, **kw):
        e = {"type": kind, "text": txt, "line": line}
        e.update(kw)
        els.append(e)
        return e

    start = i
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue
        prev_blank = i == start or blank(i - 1)
        if re.fullmatch(r"={3,}", s):
            add("page_break", "", i)
        elif s.startswith("#"):
            depth = len(s) - len(s.lstrip("#"))
            add("section", s[depth:].strip(), i, depth=depth)
        elif s.startswith("=") and not s.startswith("=="):
            add("synopsis", s[1:].strip(), i)
        elif s.startswith(">") and s.endswith("<") and len(s) > 1:
            add("centered", s[1:-1].strip(), i)
        elif s.startswith(">"):
            add("transition", s[1:].strip(), i)
        elif s.startswith("~"):
            add("lyrics", s[1:].strip(), i)
        elif prev_blank and ((s.startswith(".") and not s.startswith("..") and len(s) > 1) or HEADING.match(s)):
            h = s[1:].strip() if s.startswith(".") and not HEADING.match(s) else s
            num = SCENE_NUMBER.search(h)
            add("scene", SCENE_NUMBER.sub("", h).strip(), i, **({"number": num.group(1)} if num else {}))
        elif prev_blank and blank(i + 1) and s.upper() == s and s.endswith("TO:") and not s.startswith("!"):
            add("transition", s, i)
        elif prev_blank and not blank(i + 1) and not s.startswith("!") and is_cue(s):
            dual = s.endswith("^")
            name = s.rstrip("^").strip()
            add("character", name[1:].strip() if name.startswith("@") else name, i, **({"dual": True} if dual else {}))
            i += 1
            in_paren = False
            while i < n and (lines[i].strip() or lines[i] == "  "):
                d = lines[i].strip()
                if in_paren or d.startswith("("):
                    if in_paren and els[-1]["type"] == "parenthetical":
                        els[-1]["text"] += "\n" + d
                    else:
                        add("parenthetical", d, i)
                    in_paren = not d.endswith(")")
                elif els[-1]["type"] == "dialogue":
                    els[-1]["text"] += "\n" + d
                else:
                    add("dialogue", d, i)
                i += 1
            continue
        else:                                            # action: the whole block up to the next blank line
            first = i
            block = []
            while i < n and lines[i].strip():
                ln = lines[i].rstrip()
                block.append(ln[ln.index("!") + 1:] if i == first and s.startswith("!") else ln)
                i += 1
            body = "\n".join(block)
            lead = min((len(b) - len(b.lstrip()) for b in block if b.strip()), default=0)
            add("action", "\n".join(b[lead:] for b in block) if lead else body.strip("\n"), first)
            continue
        i += 1
    return _summary(tp, els)


def _summary(tp, els):
    scenes = [{"heading": e["text"], "number": e.get("number", ""), "line": e["line"]} for e in els if e["type"] == "scene"]
    counts = {}
    for e in els:
        if e["type"] == "character":
            nm = cue_name(e["text"]).upper()
            if nm:
                counts[nm] = counts.get(nm, 0) + 1
    title = next((v for k, v in tp if k.lower() == "title"), "")
    title = re.sub(r"[*_]+|\[\[.*?\]\]", "", title).replace("\n", " ").strip()
    return {"title_page": tp, "title": title, "elements": els, "scenes": scenes,
            "characters": [{"name": k, "lines": v} for k, v in counts.items()], **_pages(els)}


def _pages(els):
    pages = paginate(els) if els else []
    return {"pages": page_count(els, pages), "page_starts": page_starts(els, pages)}


def to_fountain(title_page, els, starts=None):
    """Elements -> Fountain text that parse() reads back as the same elements (forcing marks where the text would be
    read as something else). starts (a list) gets the line each element starts on, as parse() numbers lines."""
    out = []
    for k, v in title_page or []:
        vs = str(v).split("\n")
        out.append(f"{k}: {vs[0]}" if len(vs) == 1 else f"{k}:\n" + "\n".join("    " + x for x in vs))
    if out:
        out.append("")
    prev = None
    for e in els:
        t, kind = (e.get("text") or "").strip("\n"), e["type"]
        in_dialogue = kind in ("dialogue", "parenthetical") and prev in ("character", "dialogue", "parenthetical")
        if out and out[-1] != "" and not in_dialogue:
            out.append("")
        if starts is not None:
            starts.append(len(out))
        if kind == "scene":
            h = t.replace("\n", " ").strip()
            h = h if HEADING.match(h) else "." + h
            out.append(h + (f" #{e['number']}#" if e.get("number") else ""))
        elif kind == "character":
            name = t.strip()
            out.append((name if is_cue(name) and not name.startswith("@") else "@" + name) + (" ^" if e.get("dual") else ""))
        elif kind == "parenthetical":
            p = " ".join(t.split("\n")).strip()
            out.append(p if p.startswith("(") else f"({p})")
        elif kind == "dialogue":
            out.extend(x if x.strip() else "  " for x in t.split("\n"))
        elif kind == "transition":
            s = t.replace("\n", " ").strip()
            out.append(s if s.upper() == s and s.endswith("TO:") else "> " + s)
        elif kind == "centered":
            out.extend(f"> {x.strip()} <" for x in t.split("\n") if x.strip())
        elif kind == "lyrics":
            out.extend("~" + x for x in t.split("\n"))
        elif kind == "section":
            out.append("#" * int(e.get("depth") or 1) + " " + t)
        elif kind == "synopsis":
            out.append("= " + t)
        elif kind == "page_break":
            out.append("===")
        else:
            lines = t.split("\n")
            first = lines[0].strip()
            forced = (HEADING.match(first) or first[:1] in "#=~>@!." or (len(lines) > 1 and is_cue(first))
                      or (len(lines) == 1 and first.upper() == first and first.endswith("TO:")) or re.fullmatch(r"={3,}", first))
            out.append(("!" if forced else "") + lines[0])
            out.extend(x if x.strip() else "" for x in lines[1:])
        prev = kind
    return "\n".join(out).rstrip("\n") + "\n"


_LINE_END = re.compile(r"[.!?:;\"”’')\]…—-]\s*$")


def paragraphs_of_action(text):
    """Writing apps (the one that made The Glass Harbour among them) put each action paragraph on a line of its own with no
    blank line between, and print them as paragraphs; strict Fountain would read those lines as one block with line
    breaks. This makes each such line a paragraph, as the page prints it: a blank line goes between them. A line that is
    hard-wrapped mid-sentence (no closing punctuation, the next line going on in lower case) joins its paragraph instead.
    Works on the text itself, so notes, sections and the boneyard stay as they are."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for e in reversed(parse(text)["elements"]):
        if e["type"] != "action" or "\n" not in e["text"]:
            continue
        first = e["line"]
        n = e["text"].count("\n") + 1
        for k in range(first + n - 1, first, -1):          # from the bottom, so the line numbers above stay right
            a, b = lines[k - 1].rstrip(), lines[k].strip()
            if not a.strip() or not b:
                continue
            if not _LINE_END.search(a) and b[:1].islower():  # hard-wrapped: the same paragraph
                lines[k - 1] = a + " " + b
                del lines[k]
                continue
            forced = HEADING.match(b) or b[:1] in "#=~>@!." or re.fullmatch(r"={3,}", b) or (b.upper() == b and b.endswith("TO:")) \
                or (is_cue(b) and k + 1 < len(lines) and lines[k + 1].strip() and k + 1 < first + n)
            lines[k] = ("!" if forced and not b.startswith("!") else "") + lines[k].lstrip()
            lines.insert(k, "")
    return "\n".join(lines)


def fountain_from_text(data, paragraphs=True):
    """A .fountain / .txt upload -> its text (UTF-8, or Windows-1252 for old files), with Unix line ends, each action
    line a paragraph (see paragraphs_of_action)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", "replace")
    if "\x00" in text:
        raise ScreenplayError("That file isn't text. Import a .fountain, .txt or .pdf screenplay.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return paragraphs_of_action(text) if paragraphs else text


# ================================================================== PDF layout -> screenplay elements
_PAGE_NO = re.compile(r"^(?:page\s*)?\(?\d{1,4}[A-Z]{0,2}\.?\)?(?:\s*(?:of|/)\s*\d+)?\.?$", re.I)
_CONTINUED = re.compile(r"^\(?\s*CONT(?:INUED|'D|’D)\s*[:)]?\s*(?:\(\d+\))?\s*\)?$", re.I)
_MORE = re.compile(r"^\(\s*MORE\s*\)$", re.I)
_MARGIN_MARK = re.compile(r"^(?:\*?[A-Z]{0,2}\d{1,4}[A-Z]{0,3}\.?\*?|\*+|[A-Z]\d{0,3})$")
_TRANSITIONS = re.compile(r"^(?:[A-Z0-9 .'’-]+ TO:|FADE OUT\.?|FADE TO BLACK\.?|CUT TO BLACK\.?|SMASH TO BLACK\.?|THE END\.?|FADE IN:?|"
                          r"BACK TO:?|INTERCUT(?: WITH)?:?|END OF (?:ACT|EPISODE|TEASER).*|CONTINUED:?)$")
_DATE = re.compile(r"\b(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d|"
                   r"\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|draft|revision|revised)", re.I)
_CREDIT = re.compile(r"^(?:written|screenplay|teleplay|story|adapted|directed|created)?(?:\s*(?:and|&)\s*(?:written|directed))?\s*by:?$|"
                     r"^(?:an original screenplay|a screenplay)(?: by)?$", re.I)
_LIGATURES = str.maketrans({"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
                            "\u00a0": " ", "\u2009": " ", "\u200b": ""})


def _mode(values, default):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=lambda k: (counts[k], -k)) if counts else default


def _pct(values, q, default):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))] if v else default


def _caps(s):
    letters = [c for c in _EXTENSION.sub(" ", s) if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _title_page_pairs(lines, width, info):
    """Title page lines -> Fountain title-page pairs. The page is cut into blocks (text on the same side, left, centre
    or right, with no blank line between): the biggest (or first) centred block is the title, "Written by" and the names
    under it are the credit and authors, a block with a date or draft is the draft date, the rest is contact."""
    items = [(a, b, ln.y, ln.size, t) for ln in lines for a, b, t in ln.segs]
    if not items:
        return []
    mid = width / 2

    def side(a, b):
        if abs((a + b) / 2 - mid) < 0.1 * width:
            return "c"
        return "l" if (a + b) / 2 < mid else "r"

    blocks = []
    for it in sorted(items, key=lambda i: (i[2], i[0])):
        sd = side(it[0], it[1])
        for blk in reversed(blocks):
            last = blk["items"][-1]
            if blk["side"] == sd and 0 <= it[2] - last[2] <= 1.9 * max(it[3], last[3]):
                blk["items"].append(it)
                break
        else:
            blocks.append({"side": sd, "items": [it]})
    blocks.sort(key=lambda b: (b["items"][0][2], b["items"][0][0]))
    pairs, order = {}, []

    def put(k, v):
        if v:
            if k not in pairs:
                order.append(k)
                pairs[k] = v
            else:
                pairs[k] += "\n" + v

    biggest = max(i[3] for i in items)
    centred = [b for b in blocks if b["side"] == "c"] or blocks
    title_block = next((b for b in centred if any(i[3] >= biggest - 0.5 for i in b["items"])), centred[0])
    title = "\n".join(i[4] for i in title_block["items"])
    it = (info.get("Title") or "").strip()
    if it and re.sub(r"\W+", "", it).lower() == re.sub(r"\W+", "", title).lower():
        title = it
    put("Title", title)
    low = 0.55 * max(i[2] for i in items)
    author_next = False
    for b in blocks:
        if b is title_block:
            continue
        texts = [i[4] for i in b["items"]]
        for k, t in enumerate(texts):
            if _CREDIT.match(t):
                put("Credit", t)
                author_next = k + 1 >= len(texts)
                if k + 1 < len(texts):
                    put("Author", "\n".join(texts[k + 1:]))
                break
        else:
            if author_next and b["side"] == "c":
                put("Author", "\n".join(texts))
                author_next = False
            elif _DATE.search(" ".join(texts)) and len(texts) <= 3:
                put("Draft date", "\n".join(texts))
            elif b["side"] == "c" and b["items"][0][2] < low:
                put("Source" if "Author" in pairs else "Credit", "\n".join(texts))
            else:
                put("Contact", "\n".join(texts))
    if "Author" not in pairs and info.get("Author"):
        put("Author", info["Author"].strip())
    return [(k, pairs[k]) for k in order]


def _cue_like(t):
    """A character cue as printed: capitals, with Mc/Mac names as writers keep them (McCLANE)."""
    t = re.sub(r"\bMa?c(?=[A-Z])", lambda m: m.group().upper(), t)
    return is_cue(t) and _caps(t)


def _is_heading(s):
    return bool(HEADING.match(s)) or bool(re.match(r"^(?:INT|EXT)\b", s))


def _looks_like_title_page(lines, width, npages):
    """A first page with a few short centred lines (title, credit, author) and no scene heading or speech."""
    if npages < 2 or not lines or len(lines) > 30:
        return False
    if any(_is_heading(t) for ln in lines for _a, _b, t in ln.segs):
        return False
    short_centred = sum(1 for ln in lines if abs((ln.x0 + ln.x1) / 2 - width / 2) < 0.08 * width and ln.x1 - ln.x0 < 0.6 * width)
    return short_centred >= 1 and short_centred >= len(lines) / 3


def fountain_from_pdf(data):
    """A screenplay PDF -> (Fountain text, [warnings]). Anything this reader can't follow is a ScreenplayError for the user."""
    try:
        return _fountain_from_pdf(data)
    except ScreenplayError:
        raise
    except (Exception, RecursionError) as e:
        raise ScreenplayError(f"This PDF couldn't be read ({type(e).__name__}). Export it again, or import a .fountain file.") from e


def _fountain_from_pdf(data):
    try:
        pages, info = pdf_lines(data)
    except ScreenplayError:
        raise
    except Exception as e:                               # a file this reader can't make sense of
        raise ScreenplayError(f"This PDF couldn't be read ({type(e).__name__}). Export it again, or import a .fountain file.") from e
    if not pages:
        raise ScreenplayError("This PDF has no pages.")
    for _w, _h, lines in pages:
        for ln in lines:
            ln.segs = [(a, b, t.translate(_LIGATURES).strip()) for a, b, t in ln.segs]
            ln.segs = [s for s in ln.segs if s[2]]
    pages = [(w, h, [ln for ln in lines if ln.segs]) for w, h, lines in pages]
    chars = sum(len(ln.text) for _w, _h, lines in pages for ln in lines)
    if chars < 10 * len(pages):                          # a scan: nothing, or at most a stamped page number on each page
        raise ScreenplayError("This PDF has no text to read: it may be scanned pictures of pages. Export the screenplay from "
                              "your writing app as PDF or Fountain and import that.")
    title_page = []
    w0, _h0, first = pages[0]
    if _looks_like_title_page(first, w0, len(pages)):
        title_page = _title_page_pairs(first, w0, info)
        pages = pages[1:]
    els, warnings = _classify(pages)
    if not title_page and info.get("Title"):
        title_page = [("Title", info["Title"].strip())] + ([("Author", info["Author"].strip())] if info.get("Author") else [])
    if not any(e["type"] == "scene" for e in els) and not any(e["type"] == "character" for e in els):
        warnings.append("No scene headings or character names were found, so the text came in as action. Check the formatting.")
    return to_fountain(title_page, els), warnings


def _classify(pages):
    body = []                                            # (line, page height) for every line that's part of the script
    for w, h, lines in pages:
        keep = list(lines)
        # the page's furniture: page numbers, CONTINUED, revision headers in the top and bottom margins
        while keep and keep[0].y < 0.85 * 72 and (_PAGE_NO.match(keep[0].text) or _CONTINUED.match(keep[0].text)
                                                   or keep[0].x0 > 0.55 * w or len(keep[0].text) < 4 or _DATE.search(keep[0].text)):
            keep.pop(0)
        while keep and keep[-1].y > h - 0.75 * 72 and (_PAGE_NO.match(keep[-1].text) or _CONTINUED.match(keep[-1].text)):
            keep.pop()
        keep = [ln for ln in keep if not _CONTINUED.match(ln.text)]
        body.extend((ln, w) for ln in keep)
    if not body:
        return [], []
    cw = _mode([round(ln.cw, 1) for ln, _w in body], 7.2) or 7.2

    def is_mark(t):
        return bool(_MARGIN_MARK.match(t)) and len(t) <= 5

    # the action margin: the leftmost column that many lines start at (scene numbers in the margin don't count)
    starts = []
    for ln, _w in body:
        main = [s for s in ln.segs if not is_mark(s[2])]
        if main:
            starts.append(round(main[0][0]))
    hist = {}
    for x in starts:
        hist[x] = hist.get(x, 0) + 1
    need = max(2, 0.04 * len(starts))
    left = min((x for x, c in hist.items() if c >= need), default=min(starts) if starts else 72)
    near_left = []
    for ln, _w in body:
        main = [seg for seg in ln.segs if not is_mark(seg[2])]
        if main and abs(main[0][0] - left) < cw:
            near_left.append(main[-1][1])
    right = _pct(near_left, 0.97, left + 60 * cw)

    def split(ln):
        """(main text segments, scene number found in the margins)"""
        number, main = "", []
        for a, b, t in ln.segs:
            if b < left - 0.5 * cw and is_mark(t):
                number = number or t.strip("*").rstrip(".")
            elif a > right + 0.5 * cw and is_mark(t):
                number = number or t.strip("*").rstrip(".")
            else:
                main.append((a, b, t))
        return main, number

    rows = []                                            # (kind hint, text, x0, x1, gap before, number, page break before, line)
    prev_y, prev_page = None, None
    pitch = _mode([round(b.y - a.y) for (a, _), (b, _) in zip(body, body[1:])
                   if a.page == b.page and 0 < b.y - a.y <= 1.6 * max(a.size, b.size, 1)], 12) or 12    # not the blank-line gaps
    for ln, w in body:
        main, number = split(ln)
        if not main:
            continue
        new_page = ln.page != prev_page
        gap = new_page or (ln.y - prev_y) > 1.5 * pitch
        prev_y, prev_page = ln.y, ln.page
        if len(main) > 1 and all(b[0] - a[1] > 3 * cw for a, b in zip(main, main[1:])):
            rows.append({"segs": main, "gap": gap, "page": new_page, "number": number, "ln": ln, "w": w})
        else:
            t = " ".join(s[2] for s in main)
            rows.append({"segs": [(main[0][0], main[-1][1], t)], "gap": gap, "page": new_page, "number": number, "ln": ln, "w": w})

    def ci(x):
        return (x - left) / cw

    els = []
    warnings = []
    last_cue = None
    more = False
    k = 0
    n = len(rows)

    def add(kind, row, text, **kw):
        e = {"type": kind, "rows": [(text, row["segs"][0][0], row["segs"][-1][1])]}
        e.update(kw)
        els.append(e)
        return e

    def extend(row, text):
        els[-1]["rows"].append((text, row["segs"][0][0], row["segs"][-1][1], bool(row["gap"] and not row["page"]), bool(row["page"])))

    def next_is_speech(j):
        """Does the row after a possible cue carry dialogue (indented, right under it)?"""
        if j >= n:
            return False
        r = rows[j]
        if r["gap"] and not r["page"]:
            return False
        x = r["segs"][0][0]
        return ci(x) >= 4 or len(r["segs"]) > 1

    in_dialogue = False
    in_paren = False
    carried = False
    while k < n:
        r = rows[k]
        segs = r["segs"]
        x0, x1, t = segs[0][0], segs[-1][1], " ".join(s[2] for s in segs)
        if r["gap"] and in_dialogue and not (r["page"] and more):
            # a blank line inside a speech (some apps print one before a parenthetical or between paragraphs):
            # the speech goes on only if this line sits in the dialogue or parenthetical column
            c0 = ci(x0)
            if not ((t.startswith("(") and 4 <= c0 < 20) or (4 <= c0 < 12 and not is_cue(t))):
                in_dialogue = in_paren = False
        if _MORE.match(t):
            more = True
            k += 1
            continue
        # dual dialogue: two cues side by side, then two columns of speech
        if len(segs) > 1 and all(_cue_like(s[2]) for s in segs[:2]) and ci(segs[0][0]) >= 2:
            k = _dual(rows, k, els, cw)
            in_dialogue = in_paren = more = False
            continue
        c = ci(x0)
        if c < 4 and not (in_dialogue and not r["gap"] and c > 0.5):
            in_dialogue = in_paren = False
            more = False
            if _is_heading(t) or (r["number"] and _caps(t)):
                add("scene", r, t, number=r["number"])
            elif els and els[-1]["type"] == "scene" and not r["gap"] and _caps(t):
                extend(r, t)
            elif _TRANSITIONS.match(t) and x1 > right - 2 * cw and c > 0:
                add("transition", r, t)
            elif els and els[-1]["type"] == "action" and (not r["gap"] or (r["page"] and not _fits(els[-1]["rows"][-1][2], t, right, cw))):
                extend(r, t)                             # the same paragraph (it went on over the page break)
            else:
                add("action", r, t)
        elif (_TRANSITIONS.match(t) or t.endswith("TO:")) and _caps(t) and (x1 > right - 2 * cw or c >= 30) and not in_dialogue:
            add("transition", r, t)
            more = False
        elif _cue_like(t) and c >= 12 and next_is_speech(k + 1) and not (in_dialogue and in_paren):
            name = cue_name(t)
            if more and last_cue and name == last_cue and r["page"]:
                in_dialogue = True                       # JUNE (CONT'D) after (MORE): the same speech goes on
                carried = True
            else:
                add("character", r, t)
                last_cue = name
                in_dialogue = True
            more = False
            in_paren = False
        elif in_dialogue:
            more = False
            if carried:                                  # the first line after NAME (CONT'D) carries on the speech's paragraph
                r = dict(r, page=True)
                carried = False
            if in_paren or t.startswith("("):
                if in_paren and els[-1]["type"] == "parenthetical":
                    extend(r, t)
                else:
                    add("parenthetical", r, t)
                in_paren = not t.rstrip().endswith(")")
            elif els[-1]["type"] == "dialogue":
                extend(r, t)
            else:
                add("dialogue", r, t)
        elif abs((x0 + x1) / 2 - (left + right) / 2) < 3 * cw and c >= 4:
            add("centered", r, t)
        elif els and els[-1]["type"] in ("dialogue", "parenthetical") and r["page"] and c >= 4:
            if els[-1]["type"] == "dialogue":           # speech that runs on over a page break without (MORE)
                extend(r, t)
            else:
                add("dialogue", r, t)
            in_dialogue = True
        elif els and els[-1]["type"] == "action" and not r["gap"]:
            extend(r, t)
        else:
            add("action", r, t)
        k += 1
    return _reflow(els, cw, left, right), warnings


def _dual(rows, k, els, cw):
    """Dual dialogue from row k: the left and right columns become two speeches, the second marked dual."""
    a, b = rows[k]["segs"][0], rows[k]["segs"][1]
    mid = (a[1] + b[0]) / 2
    cols = [[], []]
    j = k + 1
    while j < len(rows) and not rows[j]["gap"]:
        for s in rows[j]["segs"]:
            cols[0 if s[0] < mid else 1].append(s)
        j += 1
    for side, cue in enumerate((a, b)):
        els.append({"type": "character", "rows": [(cue[2], cue[0], cue[1])], **({"dual": True} if side else {})})
        in_paren = False
        for s in cols[side]:
            t = s[2]
            if in_paren or t.startswith("("):
                if in_paren and els[-1]["type"] == "parenthetical":
                    els[-1]["rows"].append((t, s[0], s[1]))
                else:
                    els.append({"type": "parenthetical", "rows": [(t, s[0], s[1])]})
                in_paren = not t.endswith(")")
            elif els[-1]["type"] == "dialogue":
                els[-1]["rows"].append((t, s[0], s[1]))
            else:
                els.append({"type": "dialogue", "rows": [(t, s[0], s[1])]})
    return j


def _fits(prev_end, next_text, limit, cw):
    """Would the next line's first word have fitted after the previous line? The widest line printed in a column shows
    the column is at least that wide, so a word that fits within it means the line was broken on purpose."""
    return prev_end + (1 + len(next_text.split(" ")[0])) * cw <= limit + 0.3 * cw


def _join(rows, limit, cw, speech=False):
    """Printed lines -> text: wrapped lines are joined with a space, lines that end early on purpose keep their break
    (the next line's first word would have fitted on them)."""
    out, prev_end = "", None
    for t, _a, b, *flags in rows:
        gap, new_page = (flags + [False, False])[:2]
        if prev_end is None:
            out = t
        elif gap:
            out += "\n\n" + t                           # a blank line inside a speech
        elif new_page and speech:
            out += " " + t                               # a speech broken over a page, after (MORE): the same paragraph
        elif _fits(prev_end, t, limit, cw) or _TRANSITIONS.match(t.strip()):
            out += "\n" + t                             # it would have fitted, or it's a line of its own (FADE OUT.)
        elif out.endswith("-") and len(out) > 1 and out[-2].isalpha() and t[:1].isalpha():
            out += t                                     # a word hyphenated over the line end
        else:
            out += " " + t
        prev_end = b
    return out


def _reflow(els, cw, left, right):
    """Each element's printed lines -> its text. How wide a column is comes from the lines that start where these do
    (the two columns of dual dialogue, or dialogue and action, have different widths)."""
    ends = {}
    for e in els:
        for _t, a, b, *_g in e["rows"]:
            ends.setdefault((e["type"], round(a / cw)), []).append(b)

    def limit(kind, a):
        near = [b for (k, col), bs in ends.items() if k == kind and abs(col - round(a / cw)) <= 1 for b in bs]
        return _pct(near, 0.97, right) if near else right

    out = []
    for e in els:
        rows = e.pop("rows")
        kind = e["type"]
        if kind in ("character", "transition", "scene", "parenthetical"):
            text = " ".join(t for t, *_x in rows)
        elif kind == "centered":
            text = "\n".join(t for t, *_x in rows)
        else:
            text = _join(rows, limit(kind, rows[0][1]), cw, kind == "dialogue")
        e["text"] = re.sub(r"[ \t]+", " ", text).strip()
        if kind == "scene" and not e.get("number"):
            e.pop("number", None)
        out.append(e)
    return out


# ================================================================== breakdown: scenes, shots and characters
_HEAD_PARTS = re.compile(r"^(INT\.?\s*/\s*EXT\.?|EXT\.?\s*/\s*INT\.?|I\s*/\s*E\.?|E\s*/\s*I\.?|INT\.?|EXT\.?|EST\.?)\s*(.*)$", re.I)
CAMERA_WORDS = [  # (phrase in the action, camera move); the first phrase the action contains wins
    ("gliding forward", "push in"), ("moves forward", "push in"), ("dolly", "push in"), ("push in", "push in"), ("pushes in", "push in"), ("pushes closer", "push in"), ("eases toward", "push in"), ("eases in", "push in"),
    ("moves in", "push in"), ("creeps in", "push in"), ("dolly in", "push in"), ("zooms in", "push in"),
    ("pull back", "pull back"), ("pulls back", "pull back"), ("draws back", "pull back"), ("pulls out", "pull back"), ("widens", "pull back"),
    ("drifts up", "crane up"), ("drifts down", "crane down"), ("track with", "tracking"), ("track along", "tracking"),
    ("tracking", "tracking"), ("tracks", "tracking"), ("follows", "tracking"), ("gliding", "tracking"), ("glides", "tracking"),
    ("travels", "tracking"), ("crosses the", "tracking"), ("drifts", "tracking"),
    ("rises", "crane up"), ("crane up", "crane up"), ("cranes up", "crane up"), ("comes up", "crane up"),
    ("drops to", "crane down"), ("comes down", "crane down"), ("descends", "crane down"), ("crane down", "crane down"),
    ("pans left", "pan left"), ("pan left", "pan left"), ("pans right", "pan right"), ("pan right", "pan right"), ("pans", "pan right"),
    ("orbits", "orbit"), ("circles", "orbit"), ("handheld", "handheld"), ("hand-held", "handheld"),
    ("aerial", "drone"), ("drone", "drone"), ("overhead", "drone"), ("bird's-eye", "drone"),
    ("low angle", "low angle"), ("off the floor", "low angle"), ("from below", "low angle"),
    ("slow motion", "slow motion"), ("slow-motion", "slow motion"),
    ("close on", "close-up"), ("close-up", "close-up"), ("closeup", "close-up"), ("holds on", "close-up"), ("stays close", "close-up"),
]
# shot headings a writer puts in capitals at the action margin ("CLOSE ON JUNE'S HANDS"): each starts a shot, with its camera
SHOT_HEADINGS = [
    (r"EXTREME CLOSE|ECU\b|CLOSE ON|CLOSE UP|CLOSE-UP|CLOSEUP|CLOSER ON|INSERT\b|TIGHT ON", "close-up"),
    (r"WIDE\b|WIDE SHOT|WIDE ON|ESTABLISHING", "wide"), (r"POV\b|.*'S POV|.*’S POV", "pov"),
    (r"OVER (?:THE )?SHOULDER|OTS\b|O\.S\.S\.", "over the shoulder"), (r"HIGH ANGLE", "high angle"), (r"LOW ANGLE", "low angle"),
    (r"AERIAL|BIRD'S[- ]EYE", "drone"), (r"TRACKING|MOVING (?:SHOT|WITH)|FOLLOWING", "tracking"), (r"HANDHELD|HAND-HELD", "handheld"),
    (r"SLOW MOTION|SLOW-MO", "slow motion"),
    (r"ANGLE ON|NEW ANGLE|REVERSE ANGLE|REVERSE ON|ANOTHER ANGLE|TWO SHOT|TWO-SHOT|FAVORING|FAVOURING|BACK TO SCENE|ON\b|SERIES OF SHOTS|MONTAGE|INTERCUT", "static"),
]
_SHOT_HEADING = [(re.compile(r"^(?:" + rx + r")"), cam) for rx, cam in SHOT_HEADINGS]


def shot_heading(par):
    """The camera a shot heading asks for ('CLOSE ON JUNE' -> 'close-up'), or None if the paragraph isn't one."""
    t = par.strip()
    if not t or not _caps(t) or len(t) > 80:
        return None
    return next((cam for rx, cam in _SHOT_HEADING if rx.match(t)), None)


def _uid():
    import uuid
    return uuid.uuid4().hex[:8]


def nice_name(s):
    """'RAY MERCER' -> 'Ray Mercer', "O'NEIL" -> "O'Neil", 'McCLANE' stays."""
    if s != s.upper():
        return s
    return re.sub(r"[A-Za-z]+(?:['’][A-Za-z]+)?", lambda m: m.group()[0] + m.group()[1:].lower() if "'" not in m.group() and "’" not in m.group()
                  else re.sub(r"(['’])(\w)", lambda k: k.group(1) + k.group(2).upper(), m.group().capitalize()), s)


def split_heading(h):
    """'INT. THE GLASS HARBOUR DINER - LATE AFTERNOON' -> ('INT', 'THE GLASS HARBOUR DINER', 'LATE AFTERNOON')."""
    m = _HEAD_PARTS.match(h.strip())
    if not m:
        return "", h.strip(), ""
    ie = re.sub(r"[.\s]", "", m.group(1)).upper().replace("I/E", "INT/EXT").replace("E/I", "INT/EXT").replace("EXT/INT", "INT/EXT")
    rest = m.group(2).strip()
    parts = re.split(r"\s+[-–—]+\s+", rest)
    if len(parts) > 1:
        return ie, " - ".join(parts[:-1]).strip(), parts[-1].strip()
    return ie, rest, ""


def setting_text(scene):
    """How a shot's prompt says where and when: 'Inside The Glass Harbour Diner, late afternoon'."""
    if not scene:
        return ""
    loc = nice_name(scene.get("location") or "").strip()
    time_ = (scene.get("time") or "").strip().lower()
    where = {"INT": "Inside ", "EXT": "Outside ", "EST": "Establishing view of "}.get(scene.get("intext") or "", "")
    return ", ".join(x for x in [(where + loc) if loc else "", time_] if x)


def _sentences(t):
    return [x for x in re.split(r"(?<=[.!?])\s+", t.strip()) if x]


_TECH_TERMS = {"tracking", "close-up", "closeup", "aerial", "drone", "handheld", "hand-held", "slow motion", "slow-motion", "low angle",
               "bird's-eye", "dolly", "dolly in", "crane up", "crane down", "pan left", "pan right"}
_CAMERA_SUBJECT = re.compile(r"\b(?:the camera|camera|we|lens|the frame|the shot)\b", re.I)


def _scene_groups(els):
    groups, cur = [], None
    for e in els:
        if e["type"] == "scene":
            cur = {"head": e, "els": []}
            groups.append(cur)
        elif e["type"] in ("section", "synopsis", "page_break"):
            continue
        else:
            if cur is None:
                cur = {"head": None, "els": []}
                groups.append(cur)
            cur["els"].append(e)
    # what comes before the first heading counts only if it is more than a FADE IN:
    return [g for g in groups if g["head"] or any(e["type"] in ("action", "dialogue") and not _TRANSITIONS.match(e["text"].strip()) for e in g["els"])]


def _mentions(text, names):
    """Which characters (cue names) an action names, by their first name: 'Ray smiles' -> RAY."""
    out = []
    for n in names:
        first = n.split(" ")[0]
        if first and re.search(r"\b" + re.escape(first) + r"\b", text, re.I):
            out.append(n)
    return out


_HOLD_PHRASES = {"holds on", "stays close"}            # the camera staying where it is: the same setup
_MOVES = {"push in", "pull back", "tracking", "crane up", "crane down", "pan left", "pan right", "orbit", "handheld", "drone"}
_ONER = re.compile(r"\b(?:ONE SHOT|NO CUTS|ONE TAKE|SINGLE TAKE|ONE CONTINUOUS (?:SHOT|TAKE)|IN ONE (?:SHOT|TAKE)|ONER)\b", re.I)


def camera_setup(par):
    """Does this action paragraph call for a camera setup of its own, and which move? -> (setup, camera or None).
    A setup is a shot heading (CLOSE ON…), a technical term (tracking shot, close-up, aerial…) or a sentence about the
    camera that moves or re-frames it ("The camera eases toward Ray", "We drift…"). A camera that holds or stays close
    is not a new setup."""
    heading = shot_heading(par)
    if heading:
        return True, heading
    found, pos = [], 0
    for sent in _sentences(par):
        low = sent.lower()
        about_camera = bool(_CAMERA_SUBJECT.search(sent))
        for phrase, move in CAMERA_WORDS:
            if phrase in _HOLD_PHRASES or (not about_camera and phrase not in _TECH_TERMS):
                continue
            k = low.find(phrase)
            if k >= 0:
                found.append((move not in _MOVES, pos + k, -len(phrase), move))   # the first move described, its fullest phrase
                                                                                   # ("drifts up" over "drifts"); a framing only if no move
        pos += len(sent) + 1
    if not found:
        return False, None
    return True, min(found)[3]


def _beat_lines(beat):
    """Printed lines a beat takes on the standard page (with its blank line), for its screen time."""
    if beat[0] == "action":
        return 1 + len(wrap(beat[1], LAYOUT["action"][1]))
    ln = beat[1]
    return 2 + len(wrap(ln["text"], LAYOUT["dialogue"][1])) + (len(wrap(ln["paren"], LAYOUT["parenthetical"][1])) if ln.get("paren") else 0)


def screen_seconds(beats):
    """Screen time by the industry's rule of thumb, a page a minute, on the beats' share of the standard page."""
    return max(1, round(sum(_beat_lines(b) for b in beats) / LINES_A_PAGE * 60))


def beat_key(beat):
    """The text that identifies a beat (a shot is known by the beat it opens on): an action paragraph, or WHO: line."""
    if "action" in beat:
        return beat["action"].strip()
    ln = beat["line"]
    return f'{ln.get("who", "")}: {ln.get("text", "")}'.strip()


def shots_for(els, names, splits=(), joins=()):
    """A scene's elements -> its shots, one per camera setup. A new setup starts where the screenplay calls for one: a shot
    heading (CLOSE ON, ANGLE ON, INSERT, POV…), a cut (a transition), or a camera direction that moves or re-frames the
    camera ("The camera eases toward Ray"). Action and dialogue play inside the current setup, and so does a camera that
    holds or stays close. The scene's first camera direction is its opening shot: the description before it belongs to it.
    A scene written as one continuous take (ONE SHOT, NO CUTS) keeps its setups as the moves of that take: each shot after
    the first continues from the one before ("continues": true).
    The user's own decisions, kept with the scene: `splits` (beat keys a new setup starts at) and `joins` (beat keys where
    the screenplay's setup is merged into the shot before). Each beat keeps the line of the screenplay it comes from."""
    beats = []                                           # (kind, value, source line)
    who, paren = "", ""
    for e in els:
        t = (e.get("text") or "").strip()
        at = e.get("line", 0)
        if e["type"] in ("action", "centered", "lyrics"):
            for k, para in enumerate(t.split("\n")):
                para = para.strip()
                if para and not _TRANSITIONS.match(para):
                    beats.append(("action", para, at + k))
                elif para:
                    beats.append(("cut", para, at + k))
        elif e["type"] == "transition":
            beats.append(("cut", t, at))
        elif e["type"] == "character":
            who, paren = cue_name(t).upper(), ""
        elif e["type"] == "parenthetical":
            paren = t
        elif e["type"] == "dialogue" and t:
            beats.append(("line", {"who": who, "text": t, **({"paren": paren} if paren else {})}, at))
            paren = ""
    splits, joins = set(splits or ()), set(joins or ())
    oner = any(kind == "action" and _ONER.search(v) for kind, v, _at in beats)
    shots, cur, cut = [], None, False
    for kind, v, at in beats:
        if kind == "cut":
            cut = True
            continue
        beat = {"action": v, "at": at} if kind == "action" else {"line": v, "at": at}
        key = beat_key(beat)
        setup, cam = camera_setup(v) if kind == "action" else (False, None)
        heading = kind == "action" and shot_heading(v) is not None
        new = cur is None or cut or (heading and cur["beats"]) or (setup and cur["setup"])
        if cur is not None and key in joins:
            new = False
        split = cur is not None and cur["beats"] and key in splits
        if split:
            new = True
        if new:                                          # only a scene's first shot, or the one after a cut, takes in the
            cur = {"beats": [], "setup": bool(split), "camera": None, "lead": None}   # description before its first move
            shots.append(cur)
        cut = False
        cur["beats"].append(beat)
        if setup and not cur["setup"]:
            cur["setup"], cur["camera"], cur["lead"] = True, cam, v
    out = []
    for n, sh in enumerate(shots):
        out.append(_shot_from_beats(sh["beats"], names, sh["camera"], sh["lead"]))
        if oner and n:
            out[-1]["continues"] = True
    return out


def _shot_from_beats(beats, names, camera=None, lead=None):
    acts = [b["action"] for b in beats if "action" in b]
    lines = [b["line"] for b in beats if "line" in b]
    action = " ".join(acts)
    speakers = []
    for ln in lines:
        if ln["who"] and ln["who"] not in speakers:
            speakers.append(ln["who"])
    chars = speakers + [c for c in _mentions(action, names) if c not in speakers]
    if not action and speakers:                          # a setup with only dialogue in it: say who we see
        action = " and ".join(nice_name(x) for x in speakers) + (" speak." if len(speakers) > 1 else " speaks.")
    lead = lead or next((a for a in acts if not _caps(a)), acts[0] if acts else "")
    moving = [x for x in _sentences(lead) if _CAMERA_SUBJECT.search(x) or shot_heading(x)]
    title = (moving or [x for x in _sentences(lead) if not _caps(x)] or _sentences(lead) or
             [f'{nice_name(lines[0]["who"])}: {lines[0]["text"]}' if lines else "Shot"])[0]
    return {"id": _uid(), "anchor": beat_key(beats[0]) if beats else "", "title": title[:70].rstrip() + ("…" if len(title) > 70 else ""),
            "action": action, "lines": lines, "dialogue": "\n".join(f'{nice_name(x["who"])}: {x["text"]}' for x in lines),
            "characters": chars[:8], "camera": camera or "static", "auto_camera": camera or "static",
            "seconds": screen_seconds([("action", b["action"]) if "action" in b else ("line", b["line"]) for b in beats]),
            "beats": beats}


def _intro(name, actions):
    """Where the screenplay first introduces a character in capitals: 'RAY MERCER, fifty-seven, canvas work jacket…'
    -> ('RAY MERCER', 'fifty-seven, canvas work jacket…')."""
    rx = re.compile(r"\b(" + re.escape(name) + r"(?:\s+[A-Z][A-Z'’\-]+)*)\b(?![a-z])")
    for a in actions:
        for para in a.split("\n"):
            m = rx.search(para)
            if not m:
                continue
            rest = para[m.end():].lstrip(" ,;:-—(")
            desc = ""
            for sent in _sentences(rest):
                if len(desc) + len(sent) > 260:
                    break
                desc = (desc + " " + sent).strip()
            return m.group(1), (desc or rest[:260]).strip().rstrip(")")
    return name, ""


def _src(g):
    import hashlib
    body = "\n".join(f'{e["type"]}:{e.get("text", "")}' for e in ([g["head"]] if g["head"] else []) + g["els"])
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


_DAY_NIGHT = [(r"\bDAWN|SUNRISE|FIRST LIGHT", "DAWN"), (r"\bDUSK|SUNSET|MAGIC HOUR|TWILIGHT", "DUSK"),
              (r"\bNIGHT|MIDNIGHT|EVENING|\bLATE NIGHT", "NIGHT"), (r"\bDAY\b|MORNING|AFTERNOON|NOON|DAYTIME", "DAY")]


def day_night(time_of_day, previous=""):
    """A breakdown's Day/Night: 'LATE AFTERNOON' -> DAY, 'EVENING' -> NIGHT; CONTINUOUS, LATER, SAME TIME… take the
    previous scene's."""
    t = (time_of_day or "").upper()
    for rx, dn in _DAY_NIGHT:
        if re.search(rx, t):
            return dn
    return previous if t else ""


def scene_eighths(els, groups):
    """Each scene's length as a breakdown counts it: eighths of a page on the Hollywood standard page, page by page
    (a scene's part of a page rounds to the nearest eighth, never less than one)."""
    where = {}
    for gi, g in enumerate(groups):
        for e in ([g["head"]] if g["head"] else []) + g["els"]:
            where[id(e)] = gi
    index = {k: where.get(id(e)) for k, e in enumerate(els)}
    total = [0] * len(groups)
    for page in paginate(els):
        count, last = {}, None
        for ln in page:
            part = ln[0] if isinstance(ln, list) else ln
            gi = index.get(part.el) if part is not None else None
            if gi is None:
                gi = last
            last = gi if gi is not None else last
            if gi is not None:
                count[gi] = count.get(gi, 0) + 1
        for gi, n in count.items():
            total[gi] += max(1, round(n / LINES_A_PAGE * 8))
    return total


_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"                   # no I or O: they read as 1 and 0


def shot_letters(n):
    """The n-th shot of a scene (from 0): A, B, … Z (no I or O), then AA, AB, …"""
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, len(_LETTERS))
        out = _LETTERS[r] + out
    return out


def shot_codes(scenes, shots):
    """Shot numbers as a shot list and a storyboard write them: the scene's number and a letter (1A, 1B, … 12AA; for a scene
    numbered 12A: 12A-A, 12A-B)."""
    num = {s["id"]: s.get("number") or str(k + 1) for k, s in enumerate(scenes)}
    seen, out = {}, {}
    for s in shots:
        sc = s.get("scene")
        k = seen.get(sc, 0)
        seen[sc] = k + 1
        n = num.get(sc, "")
        out[s["id"]] = (n + ("-" if n[-1:].isalpha() else "") + shot_letters(k)) if sc in num else str(len(out) + 1)   # 12A-B, not 12AB
    return out


KEEP = ("id", "keyframe", "keyframe_out", "kf_versions", "kf_src", "kf_at", "voice_files", "notes")   # what a shot keeps when the screenplay changes under it


def _carry(fresh, old):
    """Match the new shots of a scene with its old ones, by the beat each opens on, then by position where the opening beat
    was rewritten, and carry over what the user did: keyframes, voices, a camera or a length set by hand."""
    import difflib
    pairs = {}
    a, b = [s.get("anchor", "") for s in old], [s["anchor"] for s in fresh]
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("equal", "replace"):
            for k in range(min(a2 - a1, b2 - b1)):
                pairs[b1 + k] = old[a1 + k]
    for k, s in enumerate(fresh):
        o = pairs.get(k)
        if not o:
            continue
        s.update({x: o[x] for x in KEEP if x in o})
        if o.get("camera_set"):
            s["camera"], s["camera_set"] = o["camera"], True
        if o.get("seconds_set"):
            s["seconds"], s["seconds_set"] = o["seconds"], True
    return fresh


def _migrate(keys, beat_keys):
    """The user's split and merge points, kept on the beats they were made at (a rewritten beat: the closest one)."""
    import difflib
    out = []
    for k in keys or ():
        if k in beat_keys:
            out.append(k)
        else:
            near = difflib.get_close_matches(k, beat_keys, n=1, cutoff=0.6)
            if near:
                out.append(near[0])
    return out


def breakdown(parsed, old_scenes=None, old_shots=None, redo=()):
    """The screenplay's breakdown, made again from the screenplay each time: {"scenes", "shots", "characters"}.

    Scenes come from the scene headings; shots from each scene's camera setups (see shots_for). With the previous
    breakdown given, what the user decided is kept: scenes keep their ids; their split and merge points ("splits",
    "joins") are applied again; each shot keeps its id, keyframe, voices, and a camera or length set by hand
    (camera_set, seconds_set), matched by the beat it opens on; shots added by hand ("manual") stay after the shot they
    followed. A scene in `redo` goes back to the screenplay's own setups (its splits, merges and added shots dropped;
    keyframes kept). Shots that belong to no scene (made before the screenplay) are kept at the end."""
    import difflib
    els = parsed["elements"]
    groups = _scene_groups(els)
    names = [c["name"] for c in parsed.get("characters") or []]
    old_scenes, old_shots = list(old_scenes or []), list(old_shots or [])
    keys_old = [(s.get("heading") or "").upper() for s in old_scenes]
    keys_new = [((g["head"] or {}).get("text") or "").upper() for g in groups]
    match = {}
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, keys_old, keys_new, autojunk=False).get_opcodes():
        if op in ("equal", "replace"):
            for k in range(min(a2 - a1, b2 - b1)):
                match[b1 + k] = old_scenes[a1 + k]
    by_scene = {}
    for s in old_shots:
        by_scene.setdefault(s.get("scene"), []).append(s)
    scenes, shots = [], []
    all_actions = [e["text"] for e in els if e["type"] == "action"]
    eighths = scene_eighths(els, groups)
    dn_prev = ""
    for k, g in enumerate(groups):
        head = g["head"]
        heading = head["text"] if head else ""
        ie, loc, tod = split_heading(heading) if heading else ("", "Opening", "")
        old = match.get(k)
        sid = old["id"] if old else _uid()
        reset = sid in redo
        speakers = []
        for e in g["els"]:
            if e["type"] == "character":
                n = cue_name(e["text"]).upper()
                if n and n not in speakers:
                    speakers.append(n)
        acts = " ".join(e["text"] for e in g["els"] if e["type"] == "action")
        chars = speakers + [n for n in _mentions(acts, names) if n not in speakers]
        prose = [x for e in g["els"] if e["type"] == "action" for x in _sentences(e["text"].replace("\n", " ")) if not _caps(x)]
        omitted = bool(re.match(r"^OMIT(TED)?\b", heading.upper()))
        oner = any(e["type"] == "action" and _ONER.search(e["text"]) for e in g["els"])
        dn = day_night(tod, dn_prev)
        dn_prev = dn or dn_prev
        scene = {"id": sid, "number": (head or {}).get("number") or str(k + 1), "heading": heading, "intext": ie, "location": loc,
                 "time": tod, "dn": dn, "characters": chars, "summary": " ".join(prose[:2])[:220], "eighths": eighths[k],
                 "line": (head or (g["els"][0] if g["els"] else {})).get("line", 0)}
        if oner:
            scene["continuous"] = True
        if omitted:
            scene.update(omitted=True, intext="", location="OMITTED", time="", dn="", characters=[], eighths=0)
            scenes.append(scene)
            continue
        kept = [] if not old else by_scene.get(old["id"], [])
        all_keys = [beat_key(b) for sh in shots_for(g["els"], names) for b in sh["beats"]]
        splits = [] if reset or not old else _migrate(old.get("splits"), all_keys)
        joins = [] if reset or not old else _migrate(old.get("joins"), all_keys)
        if splits:
            scene["splits"] = splits
        if joins:
            scene["joins"] = joins
        derived = [x for x in kept if not x.get("manual")]
        if reset:                                        # back to the screenplay's setups: keyframes stay, choices go
            derived = [{k: v for k, v in x.items() if k not in ("camera_set", "seconds_set")} for x in derived]
        fresh = _carry(shots_for(g["els"], names, splits, joins), derived)
        if not reset:                                    # shots added by hand stay after the shot they followed ("^": first)
            for m in [x for x in kept if x.get("manual")]:
                if m.get("after") == "^":
                    fresh.insert(0, m)
                    continue
                after = next((n for n, x in enumerate(fresh) if x["id"] == m.get("after")), None)
                fresh.insert(len(fresh) if after is None else after + 1, m)
        for s in fresh:
            s["scene"] = sid
        scenes.append(scene)
        shots.extend(fresh)
    shots.extend(s for s in old_shots if not s.get("scene"))
    characters = []
    for c in parsed.get("characters") or []:
        full, intro = _intro(c["name"], all_actions)
        characters.append({"name": c["name"], "full": nice_name(full), "intro": intro, "lines": c["lines"],
                           "scenes": [s["id"] for s in scenes if c["name"] in s["characters"]]})
    # the cast list's numbers: the most scenes first (then the most lines), as a breakdown and a call sheet number them
    for n, c in enumerate(sorted(characters, key=lambda c: (-len(c["scenes"]), -c["lines"])), start=1):
        c["cast"] = n
    num = {c["name"]: c["cast"] for c in characters}
    for sc in scenes:
        sc["cast"] = sorted(num[n] for n in sc["characters"] if n in num)
    return {"scenes": scenes, "shots": shots, "characters": characters, "locations": places(scenes)}


def places(scenes):
    """The screenplay's locations, from its scene headings: [{name (as the headings write it), full, intext (INT, EXT…),
    times, scenes (ids), description (the prose of the first scene set there)}], in the order they first appear."""
    out = {}
    for sc in scenes:
        name = (sc.get("location") or "").strip().upper()
        if not name or sc.get("omitted") or not sc.get("heading"):
            continue
        p = out.setdefault(name, {"name": name, "full": nice_name(name), "intext": [], "times": [], "scenes": [], "description": sc.get("summary") or ""})
        if sc.get("intext") and sc["intext"] not in p["intext"]:
            p["intext"].append(sc["intext"])
        t = (sc.get("time") or "").upper()
        if t and t not in p["times"]:
            p["times"].append(t)
        p["scenes"].append(sc["id"])
        if not p["description"] and sc.get("summary"):
            p["description"] = sc["summary"]
    return list(out.values())


# ================================================================== the Hollywood standard page
"""The page as The Hollywood Standard (Riley) and the studio templates lay it out: US Letter, Courier 12 (10 characters and
6 lines an inch), 55 lines a page between 1" margins, positions measured from the left edge of the paper:

    scene heading, action   1.5" to 7.5" (59 characters)      character cue   3.7"
    dialogue                2.5" to 6.0" (34 characters)      transition      6.0" (right-aligned to 7.5" if longer)
    parenthetical           3.1" to 5.6" (24 characters)      scene numbers   both margins; page numbers top right from page 2

(text stays inside the margins, so 6" of action holds 59 characters: the 60th would touch the right margin)

A blank line before each element except the lines of a speech. Page breaks never leave a scene heading or a character cue at
the foot of a page; a speech breaks only between sentences, with (MORE) under it and NAME (CONT'D) at the top of the next
page; an action paragraph breaks only between sentences, with two lines or more on each side; dual dialogue stays whole."""
LINES_A_PAGE = 55
MARGINS = {"scene": (1.5, 7.5), "action": (1.5, 7.5), "character": (3.7, 7.5), "parenthetical": (3.1, 5.6), "dialogue": (2.5, 6.0),
           "transition": (6.0, 7.5), "centered": (1.5, 7.5), "lyrics": (2.5, 6.0), "more": (3.7, 7.5)}
# a line's text stays inside the margins: 1.5" to 7.5" holds 59 characters (the 60th would touch the right margin)
LAYOUT = {k: (a, round((b - a) * 10) - 1) for k, (a, b) in MARGINS.items()}
SPACE_BEFORE = {"scene": 1, "action": 1, "character": 1, "transition": 1, "centered": 1, "lyrics": 1, "parenthetical": 0, "dialogue": 0}
DUAL = {"left": (1.5, 28, 2.4), "right": (4.6, 28, 5.5)}     # dialogue x, width, cue x of the two columns
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*$|--$|—$")


def styled(text):
    """Fountain's emphasis -> (the printed text, a style per character: 1 italic, 2 bold, 4 underline). Notes [[…]] aren't printed."""
    t = re.sub(r"\[\[.*?\]\]", "", text or "", flags=re.S).replace("\\*", "\ue000").replace("\\_", "\ue001")
    chars, sty, cur, i = [], [], 0, 0
    while i < len(t):
        m = re.match(r"\*\*\*|\*\*|\*|_", t[i:])
        if m:
            tok = m.group()
            bit = {"***": 3, "**": 2, "*": 1, "_": 4}[tok]
            rest = t[i + len(tok):].split("\n")[0]
            if cur & bit == bit:
                cur &= ~bit
                i += len(tok)
                continue
            if tok in rest:
                cur |= bit
                i += len(tok)
                continue
        chars.append({"\ue000": "*", "\ue001": "_"}.get(t[i], t[i]))
        sty.append(cur)
        i += 1
    return "".join(chars), sty


def wrap(text, width):
    """Lines as a browser and a screenwriting app wrap them: at spaces, or after a hyphen or dash inside a word; a hard line
    break always starts a line. -> [(start, end)] into text (the spaces at a break are dropped)."""
    out, base = [], 0
    for para in text.split("\n"):
        n, pos = len(para), 0
        if n == 0:
            out.append((base, base))
        while pos < n:
            if n - pos <= width:
                out.append((base + pos, base + n))
                break
            cut = -1
            for k in range(pos + width, pos, -1):
                if para[k] == " ":
                    cut = k
                    break
                if para[k - 1] in "-—" and k - 1 > pos and para[k - 2].isalnum() and para[k].isalnum():
                    cut = k
                    break
            if cut <= pos:
                cut = pos + width
            end = cut
            while end > pos and para[end - 1] == " ":
                end -= 1
            out.append((base + pos, base + end))
            pos = cut
            while pos < n and para[pos] == " ":
                pos += 1
        base += n + 1
    return out


class PLine:
    """One printed line: what it is, where it starts (inches from the left edge), its text and styles, and where it came from
    (the element's index, the line inside the element as the editor wraps it)."""
    __slots__ = ("kind", "x", "text", "sty", "el", "k", "align", "number", "col")

    def __init__(self, kind, x, text, sty, el=None, k=0, align="left", number="", col=None):
        self.kind, self.x, self.text, self.sty, self.el, self.k, self.align, self.number, self.col = kind, x, text, sty, el, k, align, number, col


def _part(e, i, kind=None, x=None, width=None):
    """An element as the page prints it: its printed text and styles, where and how wide."""
    kind = kind or e["type"]
    lx, lw = LAYOUT.get(kind, LAYOUT["action"])
    text = e.get("text") or ""
    if kind in ("scene", "transition"):
        text = text.upper()
    vis, sty = styled(text)
    if kind == "lyrics":
        sty = [v | 1 for v in sty]
    return {"kind": kind, "i": i, "vis": vis, "sty": sty, "x": lx if x is None else x, "w": width or lw, "k0": 0,
            "number": e.get("number", "") if kind == "scene" else ""}


def _part_lines(p):
    out = []
    for k, (a, b) in enumerate(wrap(p["vis"], p["w"])):
        align = "center" if p["kind"] == "centered" else "right" if p["kind"] == "transition" and b - a > LAYOUT["transition"][1] else "left"
        out.append(PLine(p["kind"], p["x"], p["vis"][a:b], p["sty"][a:b], p["i"], p["k0"] + k, align, p["number"] if k == 0 and p["k0"] == 0 else ""))
    return out


def _lines_of(e, i, kind=None, x=None, width=None):
    return _part_lines(_part(e, i, kind, x, width))


def _units(els):
    """Elements -> the units the page breaks work on: a scene heading, an action paragraph, a speech (the cue with its
    parentheticals and lines), dual dialogue, a transition, centered text, a forced page break."""
    units, k = [], 0
    printed = [(i, e) for i, e in enumerate(els) if e["type"] not in ("section", "synopsis")]
    while k < len(printed):
        i, e = printed[k]
        t = e["type"]
        if t == "page_break":
            units.append({"kind": "page_break", "parts": []})
            k += 1
            continue
        if t == "character":
            j = k + 1
            while j < len(printed) and printed[j][1]["type"] in ("parenthetical", "dialogue"):
                j += 1
            speech = printed[k:j]
            if j < len(printed) and printed[j][1]["type"] == "character" and printed[j][1].get("dual"):
                j2 = j + 1
                while j2 < len(printed) and printed[j2][1]["type"] in ("parenthetical", "dialogue"):
                    j2 += 1
                cols = []
                for side, part in (("left", speech), ("right", printed[j:j2])):
                    dx, dw, cx = DUAL[side]
                    ls = []
                    for pi, pe in part:
                        if pe["type"] == "character":
                            ls += _lines_of(pe, pi, "character", cx, 28)
                        elif pe["type"] == "parenthetical":
                            ls += _lines_of(pe, pi, "parenthetical", dx + 0.6, dw - 6)
                        else:
                            ls += _lines_of(pe, pi, "dialogue", dx, dw)
                    for ln in ls:
                        ln.col = side
                    cols.append(ls)
                rows = [[c[r] for c in cols if r < len(c)] for r in range(max(len(cols[0]), len(cols[1])))]
                units.append({"kind": "dual", "rows": rows, "space": 1})
                k = j2
                continue
            units.append({"kind": "speech", "parts": [_part(pe, pi) for pi, pe in speech], "space": 1, "cue": cue_name(e["text"])})
            k = j
            continue
        if t in ("parenthetical", "dialogue"):           # a speech without a cue (it can happen in the editor)
            units.append({"kind": "action", "parts": [_part(e, i, t)], "space": 1})
            k += 1
            continue
        units.append({"kind": t, "parts": [_part(e, i)], "space": SPACE_BEFORE.get(t, 1)})
        k += 1
    return units


def _unit_lines(u):
    if u["kind"] == "dual":
        return u["rows"]
    return [ln for p in u["parts"] for ln in _part_lines(p)]


def _cut_part(p, off):
    """A part cut at a printed-text offset: (before, after); the after part starts on the line of the editor's wrapping
    where the cut falls, so page marks land on the right line."""
    k = 0
    for n, (a, b) in enumerate(wrap(p["vis"], p["w"])):
        if a <= off:
            k = n
    before = dict(p, vis=p["vis"][:off].rstrip(), sty=p["sty"][:len(p["vis"][:off].rstrip())])
    rest = p["vis"][off:]
    lead = len(rest) - len(rest.lstrip())
    after = dict(p, vis=rest[lead:], sty=p["sty"][off + lead:], k0=p["k0"] + k, number="")
    return before, after


def _sentence_cuts(vis):
    """Where a text can break between sentences (or at a line break): offsets just after the end of a sentence."""
    return [m.end() for m in re.finditer(r"[.!?…]+[\"'”’)\]]*(?=\s)|--(?=\s)|—(?=\s)|(?=\n)", vis)]


def _split(u, room):
    """The most of a unit that can stay in `room` lines, cut as the standard allows: (before, after) units, or None.
    An action paragraph breaks between sentences with two lines or more on each side; a speech breaks between sentences
    (the rest re-wrapped on the next page), never right after its cue or a parenthetical, with two lines of it on this
    page and one or more on the next, leaving a line for (MORE)."""
    if u["kind"] not in ("action", "speech"):
        return None
    need_after = 2 if u["kind"] == "action" else 1
    more = 1 if u["kind"] == "speech" else 0
    best = None
    parts = u["parts"]
    for pi, p in enumerate(parts):
        if p["kind"] not in ("action", "dialogue"):
            continue
        cuts = _sentence_cuts(p["vis"]) + ([len(p["vis"])] if u["kind"] == "speech" and pi + 1 < len(parts) else [])
        for off in cuts:
            if off <= 0:
                continue
            b, a = _cut_part(p, off)
            if not b["vis"].strip():
                continue
            before = parts[:pi] + [b]
            after = ([a] if a["vis"].strip() else []) + parts[pi + 1:]
            if not after:
                continue
            bl = [ln for q in before for ln in _part_lines(q)]
            al = [ln for q in after for ln in _part_lines(q)]
            talk_before = sum(1 for ln in bl if ln.kind == "dialogue") if u["kind"] == "speech" else len(bl)
            talk_after = sum(1 for ln in al if ln.kind in ("dialogue", "parenthetical", "action"))
            if len(bl) + more > room or talk_before < 2 or talk_after < need_after:
                continue
            if bl[-1].kind != ("dialogue" if u["kind"] == "speech" else "action"):
                continue
            if best is None or len(bl) > best[0]:
                best = (len(bl), dict(u, parts=before), dict(u, parts=after))
    return (best[1], best[2]) if best else None


_LAID_OUT = [None]                                   # (key, pages), swapped whole: the routes call this from worker threads


def paginate(els):
    """Elements -> pages, each a list of lines: None for a blank line, a PLine, or (for dual dialogue) a list of PLines.
    The last layout is kept: a save asks for the page count, the scenes' eighths and the page marks of the same script."""
    key = tuple((e.get("type"), e.get("text"), e.get("number"), bool(e.get("dual"))) for e in els)
    last = _LAID_OUT[0]
    if last and last[0] == key:
        return last[1]
    pages = _paginate(els)
    _LAID_OUT[0] = (key, pages)
    return pages


def _paginate(els):
    units = _units(els)
    pages, cur = [], []

    def new_page():
        nonlocal cur
        pages.append(cur)
        cur = []

    def first_lines_needed(u):
        if not u or u["kind"] == "page_break":
            return 0
        n = len(_unit_lines(u))
        return min(n, 2) if u["kind"] == "action" else min(n, 3) if u["kind"] == "speech" else n

    for ui, u in enumerate(units):
        if u["kind"] == "page_break":
            if cur:
                new_page()
            continue
        first = True
        while True:
            lines = _unit_lines(u)
            if not lines:
                break
            space = u["space"] if cur and first else 0
            room = LINES_A_PAGE - len(cur)
            need = space + len(lines)
            if u["kind"] == "scene":                      # a heading goes with the start of what follows it
                nxt = units[ui + 1] if ui + 1 < len(units) else None
                need += (nxt["space"] if nxt and nxt["kind"] != "page_break" else 0) + first_lines_needed(nxt)
            if need <= room:
                cur.extend([None] * space + lines)
                break
            cut = _split(u, room - space) if cur else None
            if cut:
                before, after = cut
                cur.extend([None] * space + _unit_lines(before))
                if u["kind"] == "speech":
                    last = _unit_lines(before)[-1]
                    cur.append(PLine("more", LAYOUT["more"][0], "(MORE)", [0] * 6, last.el, -1))
                    new_page()
                    cont = u["cue"] + " (CONT'D)"
                    nxt = _unit_lines(after)[0]
                    cur.append(PLine("character", LAYOUT["character"][0], cont, [0] * len(cont), nxt.el, -1))
                    after = dict(after, parts=[p for p in after["parts"]])
                else:
                    new_page()
                u, first = after, False
                continue
            if not cur:                                  # longer than a page on its own, with no place to break
                cut = LINES_A_PAGE
                cur.extend(lines[:cut])
                new_page()
                rest = lines[cut:]
                u = {"kind": "dual", "rows": rest, "space": 0}
                first = False
                continue
            new_page()
            first = True
    if cur:
        pages.append(cur)
    return pages


def page_count(els, pages=None):
    if not any(e["type"] not in ("section", "synopsis", "page_break") for e in els):
        return 0
    return len(pages if pages is not None else paginate(els))


def page_starts(els, pages=None):
    """Where each page after the first starts, for the editor's page marks: [{"page", "el", "line"}] (the element's index in
    els, the line inside it)."""
    out = []
    for n, page in enumerate((pages if pages is not None else paginate(els))[1:], start=2):
        for ln in page:
            first = ln[0] if isinstance(ln, list) else ln
            if first is not None and first.k >= 0:
                out.append({"page": n, "el": first.el, "line": first.k})
                break
            if first is not None and first.k < 0:       # NAME (CONT'D): the page starts inside the speech
                nxt = next((x for x in page[1:] if x is not None and not isinstance(x, list)), None)
                if nxt:
                    out.append({"page": n, "el": nxt.el, "line": nxt.k})
                break
    return out


# ================================================================== PDF export, on the Hollywood standard page
def _pdf_text(s):
    """Text for a Courier (WinAnsi) string: what cp1252 can't hold is spelled with plain letters."""
    out = bytearray()
    for ch in s:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            plain = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore")
            out += plain or b"?"
    return b"(" + bytes(out).replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


_FONT_OF = {0: b"F1", 1: b"F3", 2: b"F2", 3: b"F4"}              # regular, italic, bold, bold italic


def _draw(ops, x_in, y_pt, text, sty):
    """One line in its styles (runs of Courier / -Oblique / -Bold), underlined where asked."""
    x = x_in * 72
    i = 0
    while i < len(text):
        j = i
        while j < len(text) and sty[j] == sty[i]:
            j += 1
        run, st = text[i:j], sty[i]
        ops.append(b"BT /%s 12 Tf %.2f %.2f Td %s Tj ET" % (_FONT_OF[st & 3], x + i * 7.2, y_pt, _pdf_text(run)))
        if st & 4 and run.strip():
            ops.append(b"%.2f %.2f m %.2f %.2f l S" % (x + i * 7.2, y_pt - 1.8, x + j * 7.2, y_pt - 1.8))
        i = j


def render_pdf(title_page, els, title="", revised=None, revision=""):
    """The screenplay as a PDF on the Hollywood standard page (see LAYOUT): a title page when there is one, then the
    script, page numbers from page 2, scene numbers in both margins, (MORE) and (CONT'D) over page breaks.
    revised: the indexes of the elements changed in this revision: an asterisk in the right margin on each of their lines,
    and `revision` ("Blue Rev. 05/01/26") at the top of the pages they are on, as revised pages are marked."""
    revised = set(revised or ())
    H = 792.0
    pages_ops = []
    tp = {k.lower(): v for k, v in title_page or [] if str(v).strip()}
    if any(tp.get(k) for k in ("title", "credit", "author", "authors", "source", "contact", "copyright", "draft date", "date", "revision", "notes")):
        ops = [b"0.6 w"]
        row = 21                                         # the title a third of the way down
        for key, gap in (("title", 0), ("credit", 3), ("author", 1), ("authors", 1), ("source", 3)):
            if not tp.get(key):
                continue
            row += gap
            for line in str(tp[key]).split("\n"):
                vis, sty = styled(line.strip())
                if key == "title":                       # the title in capitals, as the standard title page sets it
                    vis = vis.upper()
                _draw(ops, 4.25 - len(vis) * 0.05, H - (9 + row * 12), vis, sty)
                row += 1
            row += 1
        low = [str(tp[k]) for k in ("contact", "copyright") if tp.get(k)]
        date = [str(tp[k]) for k in ("draft date", "date", "revision", "notes") if tp.get(k)]
        for k, block in enumerate((low, date)):
            lines = [ln for b in block for ln in b.split("\n")]
            for n, line in enumerate(lines):
                vis, sty = styled(line.strip())
                x = 1.5 if k == 0 else 7.5 - len(vis) * 0.1
                _draw(ops, x, H - (9 + (55 - len(lines) + n) * 12), vis, sty)
        pages_ops.append(ops)
    for n, page in enumerate(paginate(els), start=1):
        ops = [b"0.6 w"]
        if n > 1:
            num = f"{n}."
            _draw(ops, 7.5 - len(num) * 0.1, H - 45, num, [0] * len(num))
        marked = revised and any(p.el in revised for ln in page if ln is not None for p in (ln if isinstance(ln, list) else [ln]))
        if marked and revision:
            _draw(ops, 1.0, H - 45, revision, [0] * len(revision))
        for row, ln in enumerate(page):
            if ln is None:
                continue
            y = H - (81 + row * 12)
            if revised and any(p.el in revised for p in (ln if isinstance(ln, list) else [ln])):
                _draw(ops, 8.1, y, "*", [2])
            for part in (ln if isinstance(ln, list) else [ln]):
                x = part.x
                if part.align == "right":
                    x = 7.5 - len(part.text) * 0.1
                elif part.align == "center":
                    x = 4.5 - len(part.text) * 0.05
                _draw(ops, x, y, part.text, part.sty)
                if part.number:
                    num = str(part.number)
                    _draw(ops, 1.2 - len(num) * 0.1, y, num, [0] * len(num))
                    _draw(ops, 7.8, y, num, [0] * len(num))
        pages_ops.append(ops)
    if not pages_ops:
        pages_ops.append([])
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    for k, name in enumerate(("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"), start=3):
        objs[k] = b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % name.encode()
    res = b"<< /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R /F4 6 0 R >> >>"
    kids, o = [], 10
    for ops in pages_ops:
        data = zlib.compress(b"\n".join(ops))
        objs[o] = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream"
        objs[o + 1] = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources %s /Contents %d 0 R >>" % (res, o)
        kids.append(o + 1)
        o += 2
    objs[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    info = o
    name = re.sub(r"[*_]+|\[\[.*?\]\]", "", title or tp.get("title", "")).replace("\n", " ").strip()
    objs[info] = b"<< /Title %s /Creator (Studio) /Producer (Studio) >>" % _pdf_text(name)
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for k in sorted(objs):
        offsets[k] = len(out)
        out += b"%d 0 obj\n" % k + objs[k] + b"\nendobj\n"
    xref = len(out)
    size = max(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for k in range(1, size):
        out += (b"%010d 00000 n \n" % offsets[k]) if k in offsets else b"0000000000 65535 f \n"
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, info, xref)
    return bytes(out)


# ================================================================== Final Draft (.fdx)
_FDX_TYPES = {"scene heading": "scene", "action": "action", "character": "character", "dialogue": "dialogue", "parenthetical": "parenthetical",
              "transition": "transition", "shot": "action", "general": "action", "cast list": "action", "lyrics": "lyrics",
              "new act": "section", "end of act": "action"}


def _fdx_text(par):
    """A Final Draft paragraph's text, its styles as Fountain's emphasis (**bold**, *italic*, _underline_)."""
    out = []
    for t in par.findall("Text"):
        txt = (t.text or "")
        style = (t.get("Style") or "").lower()
        if txt.strip():
            lead, core, trail = re.match(r"^(\s*)(.*?)(\s*)$", txt, re.S).groups()
            if "underline" in style:
                core = f"_{core}_"
            if "bold" in style and "italic" in style:
                core = f"***{core}***"
            elif "bold" in style:
                core = f"**{core}**"
            elif "italic" in style:
                core = f"*{core}*"
            txt = lead + core + trail
        out.append(txt)
    return "".join(out).replace("\r", "")


def fdx_to_fountain(data):
    """A Final Draft file -> Fountain text (its title page, scene numbers, dual dialogue and emphasis kept)."""
    import xml.etree.ElementTree as ET
    if len(data) > 20_000_000:
        raise ScreenplayError("That Final Draft file is too big.")
    if b"<!ENTITY" in data[:4096]:
        raise ScreenplayError("That Final Draft file can't be read (it declares entities).")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ScreenplayError(f"That isn't a Final Draft file that can be read ({e}).") from e
    if root.tag != "FinalDraft":
        raise ScreenplayError("That isn't a Final Draft (.fdx) file.")
    els = []

    def add(par, dual=False):
        kind = _FDX_TYPES.get((par.get("Type") or "Action").strip().lower(), "action")
        text = _fdx_text(par).strip("\n")
        if (par.get("StartsNewPage") or "").lower() == "yes" and els:
            els.append({"type": "page_break", "text": ""})
        if not text.strip():
            return
        if kind == "action" and (par.get("Alignment") or "").lower() == "center":
            kind = "centered"
        e = {"type": kind, "text": text.strip() if kind != "action" else text}
        if kind == "scene" and par.get("Number"):
            e["number"] = par.get("Number")
        if kind == "section":
            e["depth"] = 1
        if kind == "character" and dual:
            e["dual"] = True
        els.append(e)

    content = root.find("Content")
    for par in (content.findall("Paragraph") if content is not None else []):
        dd = par.find("DualDialogue")
        if dd is not None:
            cues = 0
            for p2 in dd.findall("Paragraph"):
                if (p2.get("Type") or "").lower() == "character":
                    cues += 1
                add(p2, dual=cues == 2 and (p2.get("Type") or "").lower() == "character")
            continue
        add(par)
    # the title page: its first lines are the title; "written by" and the name after it; the rest by what it says
    tp = {}
    lines = [_fdx_text(p).strip() for p in root.findall("./TitlePage/Content/Paragraph")]
    lines = [x for x in lines if x]
    k = 0
    while k < len(lines):
        ln = lines[k]
        if "Title" not in tp:
            tp["Title"] = ln
        elif re.fullmatch(r"(?i)(written|screenplay|story|teleplay)?\s*by|written by|by", ln):
            tp["Credit"] = ln
            if k + 1 < len(lines):
                tp["Author"] = lines[k + 1]
                k += 1
        elif re.search(r"(?i)©|copyright", ln):
            tp["Copyright"] = ln
        elif re.search(r"(?i)draft|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b(19|20)\d\d\b", ln) and "Draft date" not in tp:
            tp["Draft date"] = ln
        elif re.search(r"(?i)^based on", ln):
            tp["Source"] = ln
        else:
            tp["Contact"] = (tp.get("Contact", "") + "\n" + ln).strip()
        k += 1
    if not els:
        raise ScreenplayError("There's no screenplay text in that Final Draft file.")
    return to_fountain(list(tp.items()), els)


def _xml(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _fdx_runs(text):
    vis, sty = styled(text)
    out, i = [], 0
    while i < len(vis):
        j = i
        while j < len(vis) and sty[j] == sty[i]:
            j += 1
        st = [n for b, n in ((2, "Bold"), (1, "Italic"), (4, "Underline")) if sty[i] & b]
        out.append(f'<Text{" Style=" + chr(34) + "+".join(st) + chr(34) if st else ""}>{_xml(vis[i:j])}</Text>')
        i = j
    return "".join(out) or "<Text></Text>"


_FDX_OUT = {"scene": "Scene Heading", "action": "Action", "character": "Character", "dialogue": "Dialogue", "parenthetical": "Parenthetical",
            "transition": "Transition", "centered": "Action", "lyrics": "Lyrics"}


def to_fdx(title_page, els):
    """Elements -> a Final Draft file (.fdx): its paragraphs, scene numbers, dual dialogue, emphasis, page breaks and the
    title page. Sections and synopses (which don't print) aren't part of it."""
    def par(e, new_page=False):
        kind = e["type"]
        attrs = f' Type="{_FDX_OUT[kind]}"'
        if kind == "centered":
            attrs += ' Alignment="Center"'
        if kind == "scene" and e.get("number"):
            attrs += f' Number="{_xml(str(e["number"]))}"'
        if new_page:
            attrs += ' StartsNewPage="Yes"'
        text = (e.get("text") or "").strip("\n")
        if kind in ("scene", "transition", "character"):
            text = text.upper() if kind != "character" else text
        if kind == "parenthetical" and not text.startswith("("):
            text = f"({text})"
        return f"    <Paragraph{attrs}>{_fdx_runs(text)}</Paragraph>"

    body, new_page, k = [], False, 0
    items = [e for e in els if e["type"] in _FDX_OUT or e["type"] == "page_break"]
    while k < len(items):
        e = items[k]
        if e["type"] == "page_break":
            new_page = True
            k += 1
            continue
        if e["type"] == "character":
            j = k + 1
            while j < len(items) and items[j]["type"] in ("parenthetical", "dialogue"):
                j += 1
            if j < len(items) and items[j]["type"] == "character" and items[j].get("dual"):
                j2 = j + 1
                while j2 < len(items) and items[j2]["type"] in ("parenthetical", "dialogue"):
                    j2 += 1
                inner = "\n".join("  " + par(x) for x in items[k:j2])
                body.append(f'    <Paragraph{" StartsNewPage=" + chr(34) + "Yes" + chr(34) if new_page else ""}><DualDialogue>\n{inner}\n    </DualDialogue></Paragraph>')
                new_page, k = False, j2
                continue
        body.append(par(e, new_page))
        new_page = False
        k += 1
    tp = dict((str(a).lower(), str(b)) for a, b in title_page or [])
    tps = []
    for key, align in (("title", "Center"), ("credit", "Center"), ("author", "Center"), ("authors", "Center"), ("source", "Center"),
                       ("draft date", "Right"), ("contact", "Left"), ("copyright", "Left")):
        for ln in (tp.get(key) or "").split("\n"):
            if ln.strip():
                tps.append(f'      <Paragraph Alignment="{align}" Type="Action">{_fdx_runs(ln.strip())}</Paragraph>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n<FinalDraft DocumentType="Script" Template="No" Version="5">\n'
            "  <Content>\n" + "\n".join(body) + "\n  </Content>\n  <TitlePage>\n    <Content>\n" + "\n".join(tps) +
            "\n    </Content>\n  </TitlePage>\n</FinalDraft>\n").encode("utf-8")


def render_text(title_page, els):
    """The screenplay as plain text, laid out as the standard page is (10 characters an inch from a 1" margin, 55 lines a
    page, page numbers from page 2): for a reader with no screenwriting app. Pages are separated by a form feed."""
    col = lambda x: max(0, round((x - 1.0) * 10))
    pages = []
    tp = {k.lower(): v for k, v in title_page or [] if str(v).strip()}
    if tp.get("title"):
        rows = [""] * 20
        for key in ("title", "credit", "author", "authors", "source"):
            for ln in str(tp.get(key) or "").split("\n"):
                if ln.strip():
                    vis = styled(ln.strip())[0]
                    rows.append(" " * max(0, (65 - len(vis)) // 2) + (vis.upper() if key == "title" else vis))
            rows.append("")
        rows += [""] * max(0, 48 - len(rows))
        for key in ("contact", "copyright", "draft date", "date"):
            rows += [styled(ln.strip())[0] for ln in str(tp.get(key) or "").split("\n") if ln.strip()]
        pages.append("\n".join(r.rstrip() for r in rows))
    for n, page in enumerate(paginate(els), start=1):
        rows = [(" " * (64 - len(f"{n}.")) + f"{n}.") if n > 1 else "", ""]
        for ln in page:
            if ln is None:
                rows.append("")
                continue
            line = ""
            for part in (ln if isinstance(ln, list) else [ln]):
                x = part.x
                if part.align == "right":
                    x = 7.5 - len(part.text) * 0.1
                elif part.align == "center":
                    x = 4.5 - len(part.text) * 0.05
                c = col(x)
                if part.number:
                    line = (str(part.number) + " " * 5)[:5].ljust(5)
                line = line.ljust(c) + part.text
                if part.number:
                    line = line.ljust(66) + str(part.number)
            rows.append(line.rstrip())
        while rows and not rows[-1]:
            rows.pop()
        pages.append("\n".join(rows))
    return "\n\f\n".join(pages) + "\n"


def revised_elements(then_text, now_els):
    """The elements of now_els that are new or changed since the screenplay then_text (a saved draft): the revision."""
    import difflib
    then = [(e["type"], (e.get("text") or "").strip()) for e in parse(then_text or "")["elements"]]
    now = [(e["type"], (e.get("text") or "").strip()) for e in now_els]
    out = set()
    for op, _a1, _a2, b1, b2 in difflib.SequenceMatcher(None, then, now, autojunk=False).get_opcodes():
        if op in ("insert", "replace"):
            out.update(range(b1, b2))
    return out
