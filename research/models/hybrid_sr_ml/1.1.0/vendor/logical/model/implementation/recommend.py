import re,math
from logic_engine import inventory
FORBIDDEN=re.compile(r"(^|_)(?:body|payload)(?:_|$)",re.I)

def forbidden_name(name):
    name=re.sub(r'([A-Z]+)([A-Z][a-z])',r'\1_\2',name)
    name=re.sub(r'([a-z0-9])([A-Z])',r'\1_\2',name)
    return bool(FORBIDDEN.search(name)or re.search(r'(?:body|payload)$',name,re.I)
                or name.casefold()=='response_message')

def admit(case,maximum_rows=2000):
    for key in ("schema","documents","capture","records","question"):
        if key not in case:raise ValueError("Input contract missing "+key)
    if case["capture"].get("Body_included")is not False:
        raise ValueError("Explicit Body exclusion is required")
    if not isinstance(case["capture"].get("entity_family_complete"),bool):
        raise ValueError("Capture completeness must be explicit true/false; false permits reasoning/holds, not absence conclusions")
    if not case["capture"].get("source_version"):raise ValueError("Source version required")
    if maximum_rows is not None and sum(len(r)for r in case["records"].values())>maximum_rows:
        raise ValueError("LLM recommendation snapshots accept at most2000rows; use the separate no-LLM bulk serving path for full batches")
    for table,cols in inventory(case).items():
        if forbidden_name(table)or any(forbidden_name(c)for c in cols):
            raise ValueError("Body/payload field forbidden before model input")
        for row in case["records"][table]:
            for value in row.values():
                if value is not None and not isinstance(value,(str,int,float,bool)):
                    raise ValueError("Only scalar metadata record values are admitted; nested content must be excluded/profiled before model input")
                if isinstance(value,float)and not math.isfinite(value):raise ValueError("Non-finite numeric metadata is not admitted")
    return case

