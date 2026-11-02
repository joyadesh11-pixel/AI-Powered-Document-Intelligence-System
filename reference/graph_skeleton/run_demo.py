from graph import process

CASES = [
    ("digital PDF, clean",            "a.pdf",  {}, "validated_ok"),
    ("scanned image",                 "a.png",  {}, "validated_ok"),
    ("mixed PDF + low-conf OCR unit", "a.pdf",  {"scanned_pages": [2], "units": 2, "low_conf_units": [1]}, "validated_ok"),
    ("arith error fixed by vision",   "a.pdf",  {"arith_fail_units": [0]}, "validated_ok"),
    ("arith error persists",          "a.pdf",  {"persistent_fail_units": [0]}, "needs_review"),
    ("xlsx tabular",                  "a.xlsx", {}, "validated_ok"),
    ("xlsx mapping bad once (retry)", "a.xlsx", {"bad_mapping": 1}, "validated_ok"),
    ("xlsx mapping bad twice",        "a.xlsx", {"bad_mapping": 2}, "needs_review"),
    ("csv free-form",                 "a.csv",  {"freeform": True}, "validated_ok"),
    ("corrupt file",                  "a.pdf",  {"corrupt": True}, "failed"),
    ("unsupported .docx",             "a.docx", {}, "failed"),
    ("exception inside segment",      "a.pdf",  {"raise_in": "segment"}, "failed"),
]

bad = 0
for name, f, sim, expect in CASES:
    r = process(f, sim)
    path = " > ".join(h["node"] for h in r["history"])
    ok = r["status"] == expect
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name:32s} {r['status']:13s} {path}")
print(f"\n{len(CASES)-bad}/{len(CASES)} scenarios as expected")
