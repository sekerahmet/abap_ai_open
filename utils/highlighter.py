"""
ABAPHighlighter — line-based syntax colouring for a tkinter/CTk Text widget.

Uses "line.col" indices (O(1) per tag) instead of "1.0 + N chars" (O(N) per
tag), so large programs no longer freeze the UI.  Strings are tokenised first,
so a '"' inside a literal is not mistaken for a comment.
"""

import re


class ABAPHighlighter:
    COLORS = {
        "keyword": "#569cd6",
        "string":  "#ce9178",
        "comment": "#6a9955",
        "number":  "#b5cea8",
    }

    KEYWORDS = {
        "REPORT", "PROGRAM", "INCLUDE", "DATA", "TYPES", "CONSTANTS", "TABLES", "PARAMETERS",
        "SELECT-OPTIONS", "FIELD-SYMBOLS", "ASSIGN", "ASSIGNING", "UNASSIGN", "STATICS",
        "CLASS-DATA", "CLASS-METHODS", "CLASS-EVENTS", "RANGES",
        "SELECT", "SINGLE", "DISTINCT", "FROM", "WHERE", "INTO", "APPENDING", "CORRESPONDING",
        "UP", "ROWS", "ORDER", "BY", "GROUP", "HAVING", "INNER", "LEFT", "OUTER", "JOIN", "ON",
        "ENDSELECT", "FOR", "ALL", "ENTRIES", "AS", "FIELDS", "UNION",
        "INSERT", "UPDATE", "MODIFY", "DELETE", "COMMIT", "ROLLBACK", "WORK", "SET",
        "LOOP", "ENDLOOP", "AT", "DO", "ENDDO", "WHILE", "ENDWHILE", "TIMES",
        "IF", "ELSEIF", "ELSE", "ENDIF", "CASE", "WHEN", "OTHERS", "ENDCASE",
        "CHECK", "EXIT", "CONTINUE", "RETURN", "LEAVE", "STOP", "REJECT",
        "TRY", "CATCH", "CLEANUP", "ENDTRY", "RAISE", "EXCEPTION", "EXCEPTIONS",
        "FORM", "ENDFORM", "PERFORM", "USING", "CHANGING", "TABLES", "RAISING",
        "FUNCTION", "ENDFUNCTION", "CALL", "METHOD", "ENDMETHOD", "METHODS",
        "CLASS", "ENDCLASS", "DEFINITION", "IMPLEMENTATION", "PUBLIC", "PROTECTED", "PRIVATE",
        "SECTION", "FINAL", "ABSTRACT", "INHERITING", "CREATE", "OBJECT", "INTERFACE",
        "ENDINTERFACE", "INTERFACES", "ALIASES", "REDEFINITION", "IMPORTING", "EXPORTING",
        "RETURNING", "OPTIONAL", "DEFAULT", "PREFERRED", "PARAMETER", "NEW", "REF", "TO",
        "TYPE", "LIKE", "LINE", "OF", "TABLE", "STANDARD", "SORTED", "HASHED", "WITH", "KEY",
        "UNIQUE", "NON-UNIQUE", "OCCURS", "HEADER", "STRUCTURE", "BEGIN", "END", "VALUE",
        "LENGTH", "DECIMALS", "INITIAL", "SIZE", "IS", "NOT", "AND", "OR", "EQ", "NE", "LT",
        "GT", "LE", "GE", "IN", "BETWEEN", "BOUND", "ASSIGNED", "SUPPLIED", "REQUESTED",
        "APPEND", "COLLECT", "READ", "INDEX", "TRANSPORTING", "NO", "BINARY", "SEARCH",
        "SORT", "ASCENDING", "DESCENDING", "CLEAR", "REFRESH", "FREE", "MOVE", "MOVE-CORRESPONDING",
        "WRITE", "SKIP", "ULINE", "NEW-PAGE", "NEW-LINE", "FORMAT", "COLOR", "MESSAGE",
        "CONCATENATE", "SPLIT", "CONDENSE", "TRANSLATE", "REPLACE", "SHIFT", "OVERLAY",
        "SEPARATED", "STRLEN", "LINES", "DESCRIBE", "FIELD", "COMPUTE", "ADD", "SUBTRACT",
        "MULTIPLY", "DIVIDE", "MOD", "DIV", "ABS", "CONV", "CAST", "COND", "SWITCH", "REDUCE",
        "FILTER", "EXACT", "MODULE", "ENDMODULE", "OUTPUT", "INPUT", "SCREEN", "DYNPRO",
        "INITIALIZATION", "START-OF-SELECTION", "END-OF-SELECTION", "SELECTION-SCREEN",
        "TOP-OF-PAGE", "END-OF-PAGE", "AT", "LINE-SELECTION", "USER-COMMAND", "BLOCK", "FRAME",
        "TITLE", "OBLIGATORY", "AUTHORITY-CHECK", "ID", "EXPORT", "IMPORT", "MEMORY",
        "DATABASE", "SUBMIT", "VIA", "AND", "RETURN", "DESTINATION", "STARTING", "ENDING",
        "COMMIT", "SY", "SPACE", "ABAP_TRUE", "ABAP_FALSE", "ME", "SUPER", "TEXT", "OPEN",
        "CURSOR", "FETCH", "CLOSE", "DATASET", "TRANSFER", "ENHANCEMENT", "ENDENHANCEMENT",
        "ENHANCEMENT-POINT", "ENHANCEMENT-SECTION", "END-ENHANCEMENT-SECTION", "LOCAL",
        "GLOBAL", "CLASS-POOL", "FUNCTION-POOL", "TYPE-POOLS", "TYPE-POOL", "LOAD-OF-PROGRAM",
        "RANGE", "RESULTS", "ANY", "CLIKE", "SIMPLE", "NUMERIC", "CSEQUENCE", "XSEQUENCE",
    }

    _WORD   = re.compile(r"[A-Za-z_][\w\-]*")
    _NUMBER = re.compile(r"\d+")

    @classmethod
    def apply(cls, textbox):
        for tag, color in cls.COLORS.items():
            textbox.tag_config(tag, foreground=color)
            textbox.tag_remove(tag, "1.0", "end")

        content = textbox.get("1.0", "end-1c")
        for ln, line in enumerate(content.split("\n"), start=1):
            cls._highlight_line(textbox, ln, line)

    @classmethod
    def _highlight_line(cls, tb, ln, line):
        if line.startswith("*"):
            tb.tag_add("comment", f"{ln}.0", f"{ln}.end")
            return

        i, n = 0, len(line)
        while i < n:
            ch = line[i]

            if ch in ("'", "`"):
                j = i + 1
                while j < n:
                    if line[j] == ch:
                        if ch == "'" and j + 1 < n and line[j + 1] == "'":
                            j += 2          # escaped ''
                            continue
                        break
                    j += 1
                tb.tag_add("string", f"{ln}.{i}", f"{ln}.{min(j + 1, n)}")
                i = j + 1
                continue

            if ch == "|":                   # string template |...|
                j = line.find("|", i + 1)
                j = n - 1 if j < 0 else j
                tb.tag_add("string", f"{ln}.{i}", f"{ln}.{j + 1}")
                i = j + 1
                continue

            if ch == '"':                   # trailing comment
                tb.tag_add("comment", f"{ln}.{i}", f"{ln}.end")
                return

            if ch.isalpha() or ch == "_":
                m = cls._WORD.match(line, i)
                word = m.group(0)
                if word.upper() in cls.KEYWORDS:
                    tb.tag_add("keyword", f"{ln}.{i}", f"{ln}.{m.end()}")
                i = m.end()
                continue

            if ch.isdigit():
                m = cls._NUMBER.match(line, i)
                tb.tag_add("number", f"{ln}.{i}", f"{ln}.{m.end()}")
                i = m.end()
                continue

            i += 1
