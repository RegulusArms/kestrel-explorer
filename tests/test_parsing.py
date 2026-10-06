"""Parsing that must match the C++ version, which has its own: shlex_split there against shlex.split here, and its
ordered JSON reader against json.loads. The expected results are Python's, the same in both suites."""
import json
import shlex

from common import check, finish

from kestrel import stats

# -- shlex.split: (input, result)
words = [
    ("a b  c", ["a", "b", "c"]),
    ("'two words' x", ["two words", "x"]),
    ('"dq \\"inner\\" x"', ['dq "inner" x']),
    ("a\\ b", ["a b"]),
    ('"a\\$b" "c\\`d"', ["a\\$b", "c\\`d"]),
    ("''", [""]),
    ("a '' b", ["a", "", "b"]),
    ('x"y"z', ["xyz"]),
    ("tab\there", ["tab", "here"]),
    ("nb sp", ["nb sp"]),
    ("a\\\nb", ["a\nb"]),
    ('"back\\\\slash"', ["back\\slash"]),
    ("'single \\ kept'", ["single \\ kept"]),
    ("-o 'x y' --flag=\"a b\"", ["-o", "x y", "--flag=a b"]),
]
wrong = [i for i, want in words if shlex.split(i) != want]
check(not wrong, "shlex_split splits as Python's shlex.split does (quotes, escapes, empty words, spaces)"
      + (f" (differs for: {' | '.join(wrong)})" if wrong else ""))
refused = True
for i in ("unclosed 'quote", 'unclosed "dq', "trailing\\"):
    try:
        shlex.split(i)
        refused = False
    except ValueError:
        pass
check(refused, "...and refuses an unclosed quote or a trailing backslash")


# -- ordered JSON: json.loads keeps the keys in order
def ordered_object(raw):
    try:
        d = json.loads(raw)
    except ValueError:
        return []
    if isinstance(d, list) and d and isinstance(d[0], dict):
        d = d[0]
    return list(d.items()) if isinstance(d, dict) else []


tricky = ('{"z": 1, "a\\"q": "x", "\\u00e9t\\u00e9": [1, {"k": "}"}], "m:n": {"in": "]\\\\"}, '
          '"n": -1.5e3, "t": true, "nul": null, "a\\"q": "last"}')
got = ordered_object(tricky)
check([k for k, _ in got] == ["z", 'a"q', "été", "m:n", "n", "t", "nul"] and got[4][1] == -1500,
      "metadata JSON keeps the writer's key order (escaped quotes, \\u escapes, braces in strings, nesting)")
exif = [k for k, _ in ordered_object('[{"SourceFile": "f", "EXIF:Make": "C", "XMP:Title": "T"}]')]
check(got[1][1] == "last" and exif == ["SourceFile", "EXIF:Make", "XMP:Title"] and ordered_object("{not json") == [],
      "...each key once with its last value (as Python), exiftool's [{…}] form, and nothing from invalid JSON")

# -- KESTREL_STATS's report: the same text in both versions
stats.enable()
stats.sample("b timing (ms)", 1.0)
stats.sample("b timing (ms)", 4.5)
stats.count("a count", 3)
for v in (2, 7, 5):
    stats.peak("c peak", v)
check(stats.summary() == "Kestrel stats (KESTREL_STATS):\n  a count: 3\n  b timing (ms): 2×, average 2.8, largest 4.5\n"
                         "  c peak: most 7\n",
      "the KESTREL_STATS report reads the same in both versions (sorted names, counts, averages, peaks)")
finish()
