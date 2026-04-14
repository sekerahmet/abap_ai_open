import re

# ABAP built-in keywords/types that are NOT dictionary objects
_ABAP_KEYWORDS = {
    # Primitive types
    "STANDARD", "SORTED", "HASHED", "TABLE", "REF", "TO", "TYPE", "OF",
    "DATS", "TIMS", "CHAR", "NUMC", "INT4", "INT2", "INT1", "INT8",
    "FLTP", "DECFLOAT16", "DECFLOAT34", "XSTRING", "STRING", "BOOLEAN",
    "ANY", "VOID", "SIMPLE", "NUMERIC", "INITIAL", "OBJECT", "DATA",
    "STRUCTURE", "LINE", "INDEX", "C", "N", "P", "X", "F", "D", "T",
    "I", "B", "S", "WITH", "DEEP", "HEADER", "PACKED", "RAW",
    # Statement keywords that appear after TYPE/FROM/LIKE
    "END", "BEGIN", "START", "SELECTION", "SCREEN", "MESSAGE", "TEXT",
    "VALUE", "BLOCK", "FRAME", "TITLE", "COMMENT", "FIELD", "REQUEST",
    "ICON", "LIST", "GROUP", "POSITION", "DEFAULT", "INTERVAL",
    "LOW", "HIGH", "SIGN", "OPTION", "PARAMETERS", "OBLIGATORY",
    "NO", "YES", "ID", "MEMORY", "MATCHCODE", "PUSHBUTTON",
    "CHECKBOX", "RADIOBUTTON", "BUTTON", "MENU",
    # Logical operators / misc
    "EQ", "NE", "LT", "LE", "GT", "GE", "BETWEEN", "IN", "NOT",
    "AND", "OR", "IF", "ELSE", "ENDIF", "WHEN", "CASE", "ENDCASE",
    "LOOP", "ENDLOOP", "DO", "ENDDO", "WHILE", "ENDWHILE",
    "SY", "SYST", "SPACE", "TRUE", "FALSE", "NULL",
    "ABAP", "SAP", "ME", "SUPER", "NONE", "ALL",
}

class ABAPParser:
    PATTERNS = {
        # TABLES statement, TYPE STRUCT-FIELD references, SELECT FROM, INTO TABLE OF
        "DICT": [
            r"(?i)\bTABLES\s*:?\s*(\w+)",                        # TABLES: TABLENAME
            r"(?i)\bTYPE\s+(\w+)-\w+",                           # TYPE struct-field  → captures struct
            r"(?i)\bLIKE\s+(\w+)-\w+",                           # LIKE struct-field  → captures struct
            r"(?i)\bLIKE\s+(\w+)(?!-)",                          # LIKE tablename (OCCURS/WITH...)  → captures table
            r"(?i)\bFROM\s+(\w+)\b",                              # SELECT/DELETE FROM tablename
            r"(?i)\bTABLE\s+OF\s+(\w+)\b",                       # ... TABLE OF typename
            r"(?i)\bINTO\s+(?:TABLE\s+)?@?\w+\s+TYPE\s+(\w+)",   # INTO ... TYPE typename
            r"(?i)\bTYPE\s+(Z\w+|Y\w+|[A-Z]{2,}_\w+)\b",        # TYPE customer-namespaced type (Z/Y/XX_)
        ],
        "CLASS": [r"(?i)\bCLASS\b\s+(\w+)\b\s+DEFINITION"],
        "FIELDS": [r"(?i)\bDATA\b\s+(\w+)\b"],
        "EVENTS": [r"(?i)\bINITIALIZATION\b", r"(?i)\bSTART-OF-SELECTION\b", r"(?i)\bEND-OF-SELECTION\b",
                   r"(?i)\bAT\s+SELECTION-SCREEN\b"],
        "PBO": [r"(?i)\bMODULE\b\s+(\w+)\b\s+OUTPUT"],
        "PAI": [r"(?i)\bMODULE\b\s+(\w+)\b\s+INPUT"],
        "INCLUDES": [r"(?i)\bINCLUDE\b\s+(\w+)\b"]
    }

    @classmethod
    def get_objects(cls, code):
        """Returns a dictionary of category -> list of (name, line_number)."""
        results = {}
        lines = code.splitlines()
        
        # Pre-calculating line start offsets for efficient mapping
        line_starts = []
        offset = 0
        for line in lines:
            line_starts.append(offset)
            offset += len(line) + 1 # +1 for newline

        for cat, regexes in cls.PATTERNS.items():
            objs = []
            seen = set()
            for r in regexes:
                for match in re.finditer(r, code):
                    # For groups, take the first captured group
                    name = match.group(1) if match.groups() else match.group(0)
                    name = name.strip().upper()
                    
                    if cat == "DICT" and name in _ABAP_KEYWORDS:
                        continue
                        
                    if name and name not in seen:
                        # Find line number (1-based)
                        start_pos = match.start()
                        line_num = 1
                        for i, start in enumerate(line_starts):
                            if start <= start_pos:
                                line_num = i + 1
                            else:
                                break
                        
                        objs.append({"name": name, "line": line_num})
                        seen.add(name)
            results[cat] = sorted(objs, key=lambda x: x["name"])
        return results
