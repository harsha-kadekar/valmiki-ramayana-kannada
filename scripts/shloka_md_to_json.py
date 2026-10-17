#!/usr/bin/env python3
"""
Convert a filled-out shloka markdown study guide into a structured JSON file.

The markdown layout it understands:

    ---
    ಕಾಂಡ: ಬಾಲ ಕಾಂಡ
    ಸರ್ಗ: ೧
    ಶ್ಲೋಕ: ೨
    tags:
      - balakanda
      - sarga-1
      - shloka
    sanskrit-scholar-verified:
    kannada-scholar-verified:
    ---
    ## संस्कृत (Sanskrit)
    ### संहिता        (verse)
    ### पदच्छेद       (word split)
    ### पदार्थ        (bulleted "word — gloss")
    ### अन्वया / अन्वय  (prose)
    ### भावार्थ       (prose)
    ## ಕನ್ನಡ (Kannada)
    ### ಸಂಹಿತೆ, ಪದಚ್ಛೇದ, ಪದಾರ್ಥ, ಅನ್ವಯ, ಸಾರಾಂಶ
    ## English
    <IAST verse>
    OR
    <plain-Roman verse>
    ### Word By Word Meaning   (bulleted "translit - devanagari - kannada = meaning")
    ### Meaning                (prose)

Usage:
    python shloka_md_to_json.py shloka-1-1-2.md          # -> shloka-1-1-2.json
    python shloka_md_to_json.py --input x.md --output y.json
    python shloka_md_to_json.py --selftest
"""

import argparse
import json
import os
import re
import sys

# --------------------------------------------------------------------------
# Heading -> JSON key, per language block.  Unknown headings keep their text.
# --------------------------------------------------------------------------

_HEADING_KEYS = {
    # Sanskrit
    "संहिता": "samhita",
    "पदच्छेद": "padaccheda",
    "पदार्थ": "padartha",
    "अन्वय": "anvaya",
    "अन्वया": "anvaya",
    "भावार्थ": "bhavartha",
    # Kannada
    "ಸಂಹಿತೆ": "samhite",
    "ಪದಚ್ಛೇದ": "padaccheda",
    "ಪದಾರ್ಥ": "padartha",
    "ಅನ್ವಯ": "anvaya",
    "ಸಾರಾಂಶ": "saransha",
    # English
    "IAST": "iast",
    "Word By Word Meaning": "word_by_word",
    "Meaning": "meaning",
}

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FILENAME_RE = re.compile(r"shloka[-_](\d+)[-_](\d+)[-_](\d+)", re.IGNORECASE)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def clean_text(block: str) -> str:
    """Trim blank edges, trailing spaces and any wrapping ``` fence, while
    preserving internal line breaks."""
    lines = [ln.rstrip() for ln in block.strip("\n").splitlines()]
    # the verse / split / prose blocks are fenced in the markdown; drop it
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def first_of(mapping: dict, keys, default=""):
    for key in keys:
        if key in mapping and mapping[key] not in ("", None, []):
            return mapping[key]
    return default


def lang_key(heading: str) -> str:
    h = heading.lower()
    if "sanskrit" in h or "संस्कृत" in heading:
        return "sanskrit"
    if "kannada" in h or "ಕನ್ನಡ" in heading:
        return "kannada"
    if "english" in h:
        return "english"
    return heading.strip()


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_frontmatter(text: str):
    """Return (meta_dict, remaining_text).  Handles 'key: value' and list keys."""
    meta = {}
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return meta, text
    current_list_key = None
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):                       # list item
            if current_list_key:
                meta.setdefault(current_list_key, []).append(stripped[2:].strip())
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if value == "":
                current_list_key = key
                meta[key] = []
            else:
                current_list_key = None
                meta[key] = value
    # A key written with no value and no list items (e.g. the blank
    # scholar-verification flags) is an empty scalar, not a list.
    for key, value in list(meta.items()):
        if value == [] and key != "tags":
            meta[key] = ""
    return meta, text[match.end():]


def split_languages(text: str) -> dict:
    """Split on '## ' headings -> {language: {subsection_key: block}}."""
    result = {}
    parts = re.split(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE)
    # parts = [pre, heading, body, heading, body, ...]
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        result[lang_key(heading)] = split_subsections(body)
    return result


def split_subsections(body: str) -> dict:
    """Split a language body on '### ' headings.  Text before the first heading
    is stored under 'preamble'."""
    out = {}
    parts = re.split(r"^###\s+(.+?)\s*$", body, flags=re.MULTILINE)
    out["preamble"] = parts[0]
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        block = parts[i + 1] if i + 1 < len(parts) else ""
        out[_HEADING_KEYS.get(heading, heading)] = block
    return out


def parse_bullets(block: str) -> list:
    items = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


def parse_padartha(block: str) -> list:
    """'- word — gloss'  ->  [{'word': ..., 'gloss': ...}]"""
    out = []
    for item in parse_bullets(block):
        if " — " in item:                       # em dash
            word, gloss = item.split(" — ", 1)
            out.append({"word": word.strip(), "gloss": gloss.strip()})
        else:
            out.append({"word": "", "gloss": item})
    return out


def parse_word_by_word(block: str) -> list:
    """'- translit - devanagari - kannada = meaning'  ->  list of dicts."""
    out = []
    for item in parse_bullets(block):
        left, meaning = (item.split(" = ", 1) + [""])[:2]
        parts = [p.strip() for p in left.split(" - ")]
        row = {"translit": "", "devanagari": "", "kannada": "", "meaning": meaning.strip()}
        for slot, value in zip(("translit", "devanagari", "kannada"), parts):
            row[slot] = value
        out.append(row)
    return out


def parse_english_preamble(preamble: str):
    """Split the English preamble on the 'OR' line -> (iast, roman)."""
    iast_lines, roman_lines, in_roman = [], [], False
    for line in preamble.strip().splitlines():
        if line.strip().upper() == "OR":
            in_roman = True
            continue
        (roman_lines if in_roman else iast_lines).append(line.rstrip())
    return clean_text("\n".join(iast_lines)), clean_text("\n".join(roman_lines))


def parse_filename(path: str):
    match = _FILENAME_RE.search(os.path.basename(path))
    return tuple(int(g) for g in match.groups()) if match else None


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def md_to_dict(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    meta, rest = parse_frontmatter(text)
    sections = split_languages(rest)

    sa = sections.get("sanskrit", {})
    kn = sections.get("kannada", {})
    en = sections.get("english", {})
    iast, roman = parse_english_preamble(en.get("preamble", ""))

    doc = {
        "kanda": first_of(meta, ["ಕಾಂಡ", "kanda", "Kanda"]),
        "sarga": first_of(meta, ["ಸರ್ಗ", "ಸಗ೯", "sarga", "Sarga"]),
        "shloka_no": first_of(meta, ["ಶ್ಲೋಕ", "shloka", "shloka_no"]),
        "tags": meta.get("tags", []),
        "sanskrit_scholar_verified": first_of(
            meta, ["sanskrit-scholar-verified", "sanskrit_scholar_verified"]),
        "kannada_scholar_verified": first_of(
            meta, ["kannada-scholar-verified", "kannada_scholar_verified"]),
        "sanskrit": {
            "samhita": clean_text(sa.get("samhita", "")),
            "padaccheda": clean_text(sa.get("padaccheda", "")),
            "padartha": parse_padartha(sa.get("padartha", "")),
            "anvaya": clean_text(sa.get("anvaya", "")),
            "bhavartha": clean_text(sa.get("bhavartha", "")),
        },
        "kannada": {
            "samhite": clean_text(kn.get("samhite", "")),
            "padaccheda": clean_text(kn.get("padaccheda", "")),
            "padartha": parse_padartha(kn.get("padartha", "")),
            "anvaya": clean_text(kn.get("anvaya", "")),
            "saransha": clean_text(kn.get("saransha", "")),
        },
        "english": {
            "iast": iast,
            "roman": roman,
            "word_by_word": parse_word_by_word(en.get("word_by_word", "")),
            "meaning": clean_text(en.get("meaning", "")),
        },
    }

    ids = parse_filename(path)
    if ids:
        doc["kanda_number"], doc["sarga_number"], doc["shloka_number"] = ids
    doc["source_file"] = os.path.basename(path)
    return doc


def report_missing(doc: dict) -> list:
    """Return a list of empty-field paths, for a friendly warning."""
    missing = []
    for lang, fields in (("sanskrit", ["samhita", "padaccheda", "padartha", "anvaya", "bhavartha"]),
                         ("kannada", ["samhite", "padaccheda", "padartha", "anvaya", "saransha"]),
                         ("english", ["iast", "roman", "word_by_word", "meaning"])):
        for field in fields:
            value = doc[lang][field]
            if value in ("", [], None):
                missing.append(f"{lang}.{field}")
    return missing


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

_SAMPLE_MD = """\
---
ಕಾಂಡ: ಬಾಲ ಕಾಂಡ
ಸರ್ಗ: ೧
ಶ್ಲೋಕ: ೨
tags:
  - balakanda
  - sarga-1
---
## संस्कृत (Sanskrit)

### संहिता

को न्वस्मिन् साम्प्रतं लोके गुणवान् कश्च वीर्यवान् |
धर्मज्ञश्च कृतज्ञश्च सत्यवाक्यो दृढव्रतः ||

### पदच्छेद

कः + नु + अस्मिन्, साम्प्रतम्, लोके

### पदार्थ

- कः — किम् (प्रश्नार्थकः)
- नु — नु (प्रश्नार्थकम् अव्ययम्)

### अन्वया

कः नु अस्मिन् लोके साम्प्रतं गुणवान् वीर्यवान् च?

### भावार्थ

अयम् एव प्रश्नः रामायणस्य बीजम्।

## ಕನ್ನಡ (Kannada)

### ಸಂಹಿತೆ

ಕೋ ನ್ವಸ್ಮಿನ್ ಸಾಮ್ಪ್ರತಂ ಲೋಕೇ ಗುಣವಾನ್ ಕಶ್ಚ ವೀರ್ಯವಾನ್ |

### ಪದಚ್ಛೇದ

ಕಃ-ನು-ಅಸ್ಮಿನ್, ಸಾಮ್ಪ್ರತಮ್

### ಪದಾರ್ಥ

- ಕಃ — ಯಾರು
- ನು — ಪ್ರಶ್ನಾರ್ಥಕ ಅವ್ಯಯ

### ಅನ್ವಯ

ಈ ಲೋಕದಲ್ಲಿ ಈಗ ಗುಣವಂತನು ಯಾರು?

### ಸಾರಾಂಶ

ಈ ಪ್ರಶ್ನೆಯೇ ಇಡೀ ರಾಮಾಯಣಕ್ಕೆ ಬೀಜ.

## English

ko nv asmin sāmprataṃ loke guṇavān kaś ca vīryavān |

OR

ko nv asmin sampratam loke gunavan kash ca viryavan |

### Word By Word Meaning

- kaḥ - कः - ಕಃ = who
- nu - नु - ನು = indeed

### Meaning

Who in this world today is virtuous and valiant?
"""


def selftest() -> None:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix="shloka-1-1-2.md",
                                     encoding="utf-8", delete=False) as fh:
        fh.write(_SAMPLE_MD)
        path = fh.name
    try:
        doc = md_to_dict(path)
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        print("\nmissing fields:", report_missing(doc) or "none")
    finally:
        os.unlink(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="?", help="the filled markdown file")
    parser.add_argument("--input", dest="input_opt", help="the filled markdown file")
    parser.add_argument("--output", help="output JSON path (default: <input>.json)")
    parser.add_argument("--selftest", action="store_true",
                        help="run on a built-in sample and print the JSON")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return

    in_path = args.input_opt or args.input
    if not in_path:
        parser.error("no input file given (pass a path or --input)")
    if not os.path.exists(in_path):
        sys.exit(f"Input file not found: {in_path}")

    doc = md_to_dict(in_path)

    missing = report_missing(doc)
    if missing:
        print(f"[warn] empty sections in {os.path.basename(in_path)}: "
              f"{', '.join(missing)}", file=sys.stderr)

    out_path = args.output or os.path.splitext(in_path)[0] + ".json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    n_words = len(doc["english"]["word_by_word"])
    print(f"Wrote {out_path}  "
          f"(kanda={doc['kanda']!r} sarga={doc['sarga']} shloka={doc['shloka_no']}, "
          f"{n_words} word-by-word entries)")


if __name__ == "__main__":
    main()
