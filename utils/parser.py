"""
ABAPParser — extracts referenced objects from ABAP source with regexes.

Categories returned by get_objects():
    DICT      tables / structures / views referenced in TYPE, LIKE, SELECT, …
    CLASS     class definitions and class usages (REF TO, =>, NEW, INHERITING)
    INCLUDES  INCLUDE statements (INCLUDE STRUCTURE/TYPE go to DICT instead)
    FORMS     FORM routines and PBO/PAI modules (jump targets)
    FIELDS    local declarations (jump targets)
    EVENTS    report events (jump targets)

Comments (full-line '*' and trailing '"') are stripped before matching, so
commented-out code never produces objects.  Names are upper-cased.  DICT and
CLASS names are filtered against _ABAP_KEYWORDS; the TADIR check in the IDE is
the second safety net.
"""

import re
import bisect

_ABAP_KEYWORDS = {
    # Primitive / generic types
    "STANDARD", "SORTED", "HASHED", "TABLE", "REF", "TO", "TYPE", "OF", "LIKE",
    "DATS", "TIMS", "CHAR", "NUMC", "INT4", "INT2", "INT1", "INT8", "UTCLONG",
    "FLTP", "DECFLOAT16", "DECFLOAT34", "XSTRING", "STRING", "BOOLEAN", "ABAP_BOOL",
    "ANY", "VOID", "SIMPLE", "NUMERIC", "INITIAL", "OBJECT", "DATA", "CLIKE", "CSEQUENCE",
    "STRUCTURE", "LINE", "INDEX", "C", "N", "P", "X", "F", "D", "T", "I", "B", "S",
    "WITH", "DEEP", "HEADER", "PACKED", "RAW", "RANGE", "OCCURS", "LENGTH", "DECIMALS",
    # Statement keywords that appear after TYPE/FROM/LIKE
    "END", "BEGIN", "START", "SELECTION", "SCREEN", "MESSAGE", "TEXT",
    "VALUE", "BLOCK", "FRAME", "TITLE", "COMMENT", "FIELD", "REQUEST",
    "ICON", "LIST", "GROUP", "POSITION", "DEFAULT", "INTERVAL",
    "LOW", "HIGH", "SIGN", "OPTION", "PARAMETERS", "OBLIGATORY",
    "NO", "YES", "ID", "MEMORY", "MATCHCODE", "PUSHBUTTON", "DATABASE", "SHARED",
    "CHECKBOX", "RADIOBUTTON", "BUTTON", "MENU", "SINGLE", "DISTINCT", "COUNT",
    "TABLES", "FIELDS", "INTO", "WHERE", "SET", "KEY", "USING", "CHANGING",
    # Logical operators / misc
    "EQ", "NE", "LT", "LE", "GT", "GE", "BETWEEN", "IN", "NOT", "IS", "CP", "CS",
    "AND", "OR", "IF", "ELSE", "ENDIF", "WHEN", "CASE", "ENDCASE",
    "LOOP", "ENDLOOP", "DO", "ENDDO", "WHILE", "ENDWHILE",
    "SY", "SYST", "SPACE", "TRUE", "FALSE", "NULL", "ME", "SUPER", "NONE", "ALL",
    "ABAP", "SAP", "SCREEN", "DYNPRO", "WA", "LS", "LT", "IT", "GT", "GS", "LV", "GV",
}

# Words that must not become CLASS entries
_CLASS_EXCLUDE = _ABAP_KEYWORDS | {"DATA", "OBJECT", "ME", "SUPER", "CL_ABAP_TYPEDESCR"}


def _strip_comments(code: str) -> str:
    """Blank out full-line '*' comments and trailing '"' comments (outside literals)."""
    out = []
    for line in code.splitlines():
        if line.startswith("*"):
            out.append("")
            continue
        if '"' not in line:
            out.append(line)
            continue
        buf, quote = [], None
        for ch in line:
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
            elif ch in ("'", "`", "|"):
                quote = ch
                buf.append(ch)
            elif ch == '"':
                break
            else:
                buf.append(ch)
        out.append("".join(buf))
    return "\n".join(out)


class ABAPParser:
    # Each pattern must capture the object name in group 1.
    PATTERNS = {
        "DICT": [
            r"(?i)\bTYPE\s+(\w+)-\w+",                                  # TYPE struct-field
            r"(?i)\bLIKE\s+(\w+)-\w+",                                  # LIKE struct-field
            r"(?i)\bLIKE\s+(?:LINE\s+OF\s+)?(\w+)\b(?!-)",              # LIKE tablename
            r"(?is)\bSELECT\b[^.]*?\bFROM\s+\(?\s*(\w+)",               # SELECT … FROM tab
            r"(?is)\bSELECT\b[^.]*?\bJOIN\s+(\w+)",                      # … INNER JOIN tab
            r"(?i)\b(?:DELETE\s+FROM|INSERT\s+INTO|UPDATE|INSERT|MODIFY)\s+(\w+)\b",
            r"(?i)\bTABLE\s+OF\s+(\w+)\b",                              # TABLE OF typename
            r"(?i)\bLINE\s+OF\s+(\w+)\b",                               # LINE OF tabtype
            r"(?i)\bINCLUDE\s+(?:STRUCTURE|TYPE)\s+(\w+)",              # INCLUDE STRUCTURE x
            r"(?i)\bSTRUCTURE\s+(\w+)\b",                               # <fs> STRUCTURE x
            r"(?i)\bFOR\s+(\w+)-\w+",                                   # SELECT-OPTIONS s FOR tab-fld
            r"(?i)\bTYPE\s+(Z\w+|Y\w+|[A-Z]{2,}_\w+)\b",                # TYPE custom-namespaced type
        ],
        "CLASS": [
            r"(?i)\bCLASS\s+(\w+)\s+DEFINITION\b",
            r"(?i)\bCLASS\s+(\w+)\s+IMPLEMENTATION\b",
            r"(?i)\bREF\s+TO\s+(\w+)\b",
            r"(?i)\b(\w+)=>",
            r"(?i)\bCREATE\s+OBJECT\s+\w+\s+TYPE\s+(\w+)\b",
            r"(?i)\bNEW\s+(\w+)\s*\(",
            r"(?i)\bINHERITING\s+FROM\s+(\w+)\b",
            r"(?i)\bINTERFACES\s*:?\s*(\w+)\b",
        ],
        "INCLUDES": [
            r"(?i)\bINCLUDE\s+(?!STRUCTURE\b|TYPE\b|METHODS\b)(\w+)\b",
        ],
        "FORMS": [
            r"(?i)\bFORM\s+(\w+)\b",
            r"(?i)\bMODULE\s+(\w+)\s+(?:OUTPUT|INPUT)\b",
        ],
        "FIELDS": [
            r"(?i)\bDATA\s*:?\s*(\w+)\b",
            r"(?i)\bDATA\((\w+)\)",
            r"(?i)\bPARAMETERS\s*:?\s*(\w+)\b",
            r"(?i)\bSELECT-OPTIONS\s*:?\s*(\w+)\b",
            r"(?i)\bFIELD-SYMBOLS\s*:?\s*<(\w+)>",
            r"(?i)\bCONSTANTS\s*:?\s*(\w+)\b",
        ],
        "EVENTS": [
            r"(?i)\b(INITIALIZATION)\b",
            r"(?i)\b(START-OF-SELECTION)\b",
            r"(?i)\b(END-OF-SELECTION)\b",
            r"(?i)\b(AT\s+SELECTION-SCREEN)\b",
            r"(?i)\b(TOP-OF-PAGE)\b",
        ],
    }

    # TABLES: a, b, c.  — chained statement, handled separately
    _TABLES_RE = re.compile(r"(?is)\bTABLES\s*:?\s*([^.]+)\.")

    @classmethod
    def get_objects(cls, code: str) -> dict:
        """Returns {category: [ {"name": NAME, "line": int}, … ]} sorted by name."""
        text = _strip_comments(code)

        line_starts = [0]
        for m in re.finditer("\n", text):
            line_starts.append(m.end())

        def line_of(pos: int) -> int:
            return bisect.bisect_right(line_starts, pos)

        results = {}
        for cat, regexes in cls.PATTERNS.items():
            objs, seen = [], set()

            def _add(name: str, pos: int):
                name = name.strip().upper()
                if not name or name in seen:
                    return
                if cat == "DICT" and name in _ABAP_KEYWORDS:
                    return
                if cat == "CLASS" and name in _CLASS_EXCLUDE:
                    return
                if cat == "DICT" and name.isdigit():
                    return
                if cat == "FIELDS" and name in ("BEGIN", "END", "OF"):
                    return
                seen.add(name)
                objs.append({"name": name, "line": line_of(pos)})

            if cat == "DICT":
                for m in cls._TABLES_RE.finditer(text):
                    for tok in re.split(r"[\s,]+", m.group(1)):
                        if tok:
                            _add(tok, m.start())

            for r in regexes:
                for m in re.finditer(r, text):
                    _add(m.group(1), m.start(1))

            results[cat] = sorted(objs, key=lambda o: o["name"])
        return results
