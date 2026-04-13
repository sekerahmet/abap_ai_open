import re

class ABAPHighlighter:
    # Colors for Dark Theme
    COLORS = {
        "keyword": "#569cd6", 
        "string": "#ce9178",
        "comment": "#6a9955"
    }

    KEYWORDS_PATTERN = r"\b(REPORT|DATA|TABLES|SELECT|FROM|WHERE|LOOP|ENDLOOP|METHOD|ENDMETHOD|CLASS|ENDCLASS|MODULE|ENDMODULE|IF|ENDIF|CASE|ENDCASE|WHEN|DO|ENDDO|APPEND|APPENDING|MODIFY|DELETE|INSERT|UPDATE|FIELD-SYMBOLS|ASSIGNING|TYPE|REF\sTO|INTO|UP\sTO|ROWS|ORDER\sBY|GROUP\sBY|CHECK|EXIT|CONTINUE|INITIALIZATION|START-OF-SELECTION|END-OF-SELECTION|AT\sSELECTION-SCREEN|PARAMETERS|SELECT-OPTIONS|INCLUDE)\b"

    @classmethod
    def apply(cls, textbox):
        content = textbox.get("1.0", "end")
        
        # Define Tags if not already defined (handled in widget init usually)
        for tag, color in cls.COLORS.items():
            textbox.tag_config(tag, foreground=color)

        # Clear existing highlighting (optional, usually re-doing it)
        
        # Keywords
        cls._highlight_pattern(textbox, cls.KEYWORDS_PATTERN, "keyword")
        
        # Strings
        cls._highlight_pattern(textbox, r"'.*?'", "string")
        cls._highlight_pattern(textbox, r"`.*?`", "string")
        
        # Comments
        cls._highlight_pattern(textbox, r"^\*.*$", "comment")
        cls._highlight_pattern(textbox, r"\".*$", "comment")

    @staticmethod
    def _highlight_pattern(textbox, pattern, tag):
        content = textbox.get("1.0", "end")
        for match in re.finditer(pattern, content, re.IGNORECASE | re.MULTILINE):
            start = f"1.0 + {match.start()} chars"
            end = f"1.0 + {match.end()} chars"
            textbox.tag_add(tag, start, end)
