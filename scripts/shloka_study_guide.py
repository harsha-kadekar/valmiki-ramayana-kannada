#!/usr/bin/env python3
"""
Generate a tri-lingual (Sanskrit / Kannada / English) study guide for a shloka
using the Sarvam AI Chat Completions API.

Design principles (learned from earlier failures):

  * The model is NEVER trusted to emit a script other than Devanagari/IAST.
    Kannada is produced by a deterministic Devanagari -> Kannada Unicode map
    in Python (Devanagari and Kannada are near-isomorphic Brahmic blocks, so
    this is exact), and the diacritic-free Roman line is derived from the
    model's IAST by stripping diacritics.

  * The /transliterate endpoint is NOT used for Sanskrit: it has no sa-IN
    source and treats Devanagari as Hindi, applying Hindi phonology (schwa
    deletion, cluster simplification) that mangles Sanskrit. Indic-Indic
    transliteration is unsupported anyway.

  * The analysis is split into several SMALL chat calls instead of one giant
    JSON, because a small, single-purpose reply is far easier for the model
    to obey than a large multi-section one.

  * The script owns the markdown layout; the model only supplies content.

Usage:
    export SARVAM_API_KEY="sk_..."      # https://dashboard.sarvam.ai/
    pip install requests

    python shloka_study_guide.py --selftest          # check the script map offline
    python shloka_study_guide.py                     # default shloka -> stdout
    python shloka_study_guide.py "तपःस्वाध्यायनिरतं ..."   # custom shloka -> stdout
    python shloka_study_guide.py --input shloka-1-1-2.md   # read shloka from file,
                                                           # replace that file

Input-file mode:
    The file is read to extract the Sanskrit shloka (from its '### संहिता'
    section, or failing that, its Devanagari lines).  The file NAME supplies
    the metadata: 'shloka-<kanda>-<sarga>-<shloka-no>.md', e.g. shloka-1-1-2.md
    -> kanda 1, sarga 1, shloka 2.  The kanda number is mapped to its Kannada
    name.  The generated guide then REPLACES the input file.
"""

import argparse
import json
import os
import re
import sys

import requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

BASE_URL = "https://api.sarvam.ai"
CHAT_MODEL = "sarvam-105b"  # sarvam-30b is deprecated and rejected with 400

DEFAULT_SHLOKA = (
    "तपःस्वाध्यायनिरतं तपस्वी वाग्विदां वरम् |\n"
    "नारदं परिपप्रच्छ वाल्मीकिर्मुनिपुङ्गवम् ||"
)

# --------------------------------------------------------------------------
# Deterministic Devanagari <-> Kannada script conversion
# --------------------------------------------------------------------------
# Devanagari and Kannada are both Brahmic scripts with parallel Unicode blocks
# and a near one-to-one letter correspondence, so a direct codepoint map gives
# an exact transliteration (no API, no model, no lossy Hindi-phonology hop).

_DEVA_KANNADA_PAIRS = [
    # signs
    ("ँ", "ಁ"), ("ं", "ಂ"), ("ः", "ಃ"), ("्", "್"), ("ऽ", "ಽ"),
    # independent vowels
    ("अ", "ಅ"), ("आ", "ಆ"), ("इ", "ಇ"), ("ई", "ಈ"), ("उ", "ಉ"), ("ऊ", "ಊ"),
    ("ऋ", "ಋ"), ("ऌ", "ಌ"), ("ॠ", "ೠ"), ("ॡ", "ೡ"),
    ("ऍ", "಍"), ("ऎ", "ಎ"), ("ए", "ಏ"), ("ऐ", "ಐ"),
    ("ऑ", "ಒ"), ("ऒ", "ಒ"), ("ओ", "ಓ"), ("औ", "ಔ"),
    # consonants
    ("क", "ಕ"), ("ख", "ಖ"), ("ग", "ಗ"), ("घ", "ಘ"), ("ङ", "ಙ"),
    ("च", "ಚ"), ("छ", "ಛ"), ("ज", "ಜ"), ("झ", "ಝ"), ("ञ", "ಞ"),
    ("ट", "ಟ"), ("ठ", "ಠ"), ("ड", "ಡ"), ("ढ", "ಢ"), ("ण", "ಣ"),
    ("त", "ತ"), ("थ", "ಥ"), ("द", "ದ"), ("ध", "ಧ"), ("न", "ನ"), ("ऩ", "ನ"),
    ("प", "ಪ"), ("फ", "ಫ"), ("ब", "ಬ"), ("भ", "ಭ"), ("म", "ಮ"),
    ("य", "ಯ"), ("र", "ರ"), ("ऱ", "ಱ"), ("ल", "ಲ"), ("ळ", "ಳ"),
    ("ऴ", "಴"), ("व", "ವ"),
    ("श", "ಶ"), ("ष", "ಷ"), ("स", "ಸ"), ("ह", "ಹ"),
    # dependent vowel signs (matras)
    ("ा", "ಾ"), ("ि", "ಿ"), ("ी", "ೀ"), ("ु", "ು"), ("ू", "ೂ"),
    ("ृ", "ೃ"), ("ॄ", "ೄ"), ("ॢ", "ೢ"), ("ॣ", "ೣ"),
    ("ॅ", "ೆ"), ("े", "ೇ"), ("ै", "ೈ"), ("ॉ", "ೊ"), ("ो", "ೋ"), ("ौ", "ೌ"),
    # digits
    ("०", "೦"), ("१", "೧"), ("२", "೨"), ("३", "೩"), ("४", "೪"),
    ("५", "೫"), ("६", "೬"), ("७", "೭"), ("८", "೮"), ("९", "೯"),
]

_DEV_TO_KAN = str.maketrans({ord(d): k for d, k in _DEVA_KANNADA_PAIRS})

# Reverse map: several Devanagari letters can share one Kannada letter
# (e.g. न and ऩ both -> ನ), so keep the FIRST pair and never overwrite it.
_rev = {}
for _d, _k in _DEVA_KANNADA_PAIRS:
    _rev.setdefault(_k, _d)
_KAN_TO_DEV = str.maketrans(_rev)

# Kannada letters that may leak into a "Devanagari" field, for validation.
_KANNADA_RANGE = "\u0C80-\u0CFF"


def devanagari_to_kannada(text: str) -> str:
    """Exact Devanagari -> Kannada script conversion."""
    return text.translate(_DEV_TO_KAN)


def to_devanagari(text: str) -> str:
    """Force any Kannada characters back to Devanagari (normalises model slips)."""
    return text.translate(_KAN_TO_DEV)


def contains_kannada(text: str) -> bool:
    return any("\u0C80" <= ch <= "\u0CFF" for ch in text)


# --------------------------------------------------------------------------
# IAST -> simple Roman (strip diacritics)
# --------------------------------------------------------------------------

_DIACRITICS = {
    "ā": "a", "Ā": "A", "ī": "i", "Ī": "I", "ū": "u", "Ū": "U",
    "ṛ": "ri", "Ṛ": "Ri", "ṝ": "ri", "ḷ": "l", "ḹ": "l",
    "ṃ": "m", "ṁ": "m", "ḥ": "h", "Ḥ": "H", "ś": "sh", "Ś": "Sh",
    "ṣ": "sh", "Ṣ": "Sh", "ṭ": "t", "ḍ": "d", "ṇ": "n", "ñ": "n",
    "ṅ": "n", "ḻ": "l", "ṉ": "n", "ṟ": "r",
}


def strip_diacritics(text: str) -> str:
    """Turn an IAST string into plain ASCII Roman."""
    for a, b in _DIACRITICS.items():
        text = text.replace(a, b)
    return text


# --------------------------------------------------------------------------
# Devanagari -> IAST (deterministic; the model is never asked for IAST)
# --------------------------------------------------------------------------
# Same reasoning as the Kannada map: Devanagari and IAST have a fixed letter
# correspondence, so we COMPUTE the transliteration rather than trusting the
# model (which once returned the padaccheda in its place).

_DEV_IAST_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ṅ",
    "च": "c", "छ": "ch", "ज": "j", "झ": "jh", "ञ": "ñ",
    "ट": "ṭ", "ठ": "ṭh", "ड": "ḍ", "ढ": "ḍh", "ण": "ṇ",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "ऩ": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ऱ": "ṟ", "ल": "l", "ळ": "ḷ", "ऴ": "ḻ",
    "व": "v", "श": "ś", "ष": "ṣ", "स": "s", "ह": "h",
}
_DEV_IAST_VOWELS = {
    "अ": "a", "आ": "ā", "इ": "i", "ई": "ī", "उ": "u", "ऊ": "ū",
    "ऋ": "ṛ", "ॠ": "ṝ", "ऌ": "ḷ", "ॡ": "ḹ",
    "ऍ": "ĕ", "ऎ": "e", "ए": "e", "ऐ": "ai",
    "ऑ": "ŏ", "ऒ": "o", "ओ": "o", "औ": "au",
}
_DEV_IAST_MATRAS = {
    "ा": "ā", "ि": "i", "ी": "ī", "ु": "u", "ू": "ū",
    "ृ": "ṛ", "ॄ": "ṝ", "ॢ": "ḷ", "ॣ": "ḹ",
    "ॅ": "ĕ", "े": "e", "ै": "ai", "ॉ": "ŏ", "ो": "o", "ौ": "au",
}
_DEV_IAST_OTHER = {
    "ं": "ṃ", "ँ": "m̐", "ः": "ḥ", "ऽ": "'",
    "।": "|", "॥": "||",
    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
    "५": "5", "६": "6", "७": "7", "८": "8", "९": "9",
}
_VIRAMA = "्"


def devanagari_to_iast(text: str) -> str:
    """
    Deterministic Devanagari -> IAST.  A consonant carries the inherent 'a'
    unless a virama or a vowel sign follows it; everything else maps directly.
    """
    out = []
    chars = list(text)
    i, n = 0, len(chars)
    while i < n:
        ch = chars[i]
        if ch in _DEV_IAST_CONSONANTS:
            out.append(_DEV_IAST_CONSONANTS[ch])
            nxt = chars[i + 1] if i + 1 < n else ""
            if nxt == _VIRAMA:                 # cluster: no inherent vowel
                i += 2
                continue
            if nxt in _DEV_IAST_MATRAS:        # explicit vowel sign
                out.append(_DEV_IAST_MATRAS[nxt])
                i += 2
                continue
            out.append("a")                    # inherent vowel
            i += 1
            continue
        if ch in _DEV_IAST_VOWELS:
            out.append(_DEV_IAST_VOWELS[ch])
        elif ch in _DEV_IAST_MATRAS:           # stray vowel sign
            out.append(_DEV_IAST_MATRAS[ch])
        elif ch == _VIRAMA:
            pass
        elif ch in _DEV_IAST_OTHER:
            out.append(_DEV_IAST_OTHER[ch])
        else:
            out.append(ch)                     # spaces, ASCII, punctuation
        i += 1
    return "".join(out)


def clean_padaccheda(text: str) -> str:
    """Tidy a model-produced padaccheda: drop the danda marks the model likes
    to include as if they were words, and normalise the separators."""
    text = re.sub(r"[|।॥]+", " ", text)          # drop danda marks
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*\+\s*", " + ", text)      # normalise the '+' separator
    text = re.sub(r"\s*,\s*", ", ", text)        # normalise commas
    text = text.replace(", + ", ", ").replace(" + , ", ", ")
    text = re.sub(r"(?:\+\s*)+", "+ ", text)
    text = re.sub(r"\s*,\s*,\s*", ", ", text)
    return text.strip(" ,+").strip()


def normalise_kannada(text: str) -> str:
    """Fix Devanagari characters that leaked into a Kannada field."""
    if re.search(r"[\u0900-\u097F]", text):
        return devanagari_to_kannada(text)
    return text


def normalise_padartha_list(items) -> list:
    """
    Guarantee one padartha entry per word.

    The model sometimes returns the whole padartha_kn list as a SINGLE string
    of comma-separated 'word — gloss' pairs, which would render as one bullet.
    Split such a string back into one entry per pair.  A comma only ends an
    entry when what follows it still contains an em dash (i.e. a new pair
    starts), so a gloss that itself contains a comma is left intact.
    """
    if isinstance(items, str):
        items = [items]
    out = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if text.count("—") > 1:                 # several pairs collapsed into one
            parts = re.split(r"[,;।\n]\s*(?=[^,;।\n]*—)", text)
            out.extend(p.strip(" ,;।") for p in parts if p.strip(" ,;।"))
        else:
            out.append(text)
    return out


# --------------------------------------------------------------------------
# Chat Completions helper (small calls, robust parameter fallback)
# --------------------------------------------------------------------------


def _headers() -> dict:
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        sys.exit("Please set the SARVAM_API_KEY environment variable "
                 "(get one at https://dashboard.sarvam.ai/)")
    return {"Authorization": f"Bearer {key}", "api-subscription-key": key}


def _repair_unescaped_quotes(s: str) -> str:
    """
    Escape straight double-quotes that appear INSIDE a JSON string value.

    The model sometimes quotes a phrase inside a value without escaping it
    (e.g. ... — "ಈ ಲೋಕದಲ್ಲಿ ...?" ಎಂದು ...), which breaks strict JSON.  A quote
    is treated as closing only when the next non-space character is one of
    , } ] : (or end of input); otherwise it is an inner quote and is escaped.
    """
    out = []
    in_string = False
    escaped = False
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            out.append(ch)
            escaped = True
        elif ch == '"':
            if not in_string:
                in_string = True
                out.append(ch)
            else:
                j = i + 1
                while j < n and s[j] in " \t\r\n":
                    j += 1
                nxt = s[j] if j < n else ""
                if nxt in ",}]:" or nxt == "":
                    in_string = False
                    out.append(ch)
                else:
                    out.append('\\"')
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _escape_control_chars_in_strings(s: str) -> str:
    """Escape raw newlines / tabs that sit INSIDE a JSON string (the model often
    writes a multi-line value literally instead of using \\n)."""
    out, in_string, escaped = [], False, False
    for ch in s:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ch in "\n\r\t":
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
            continue
        out.append(ch)
    return "".join(out)


def _parse_json_leniently(content: str) -> dict:
    """
    Parse a model reply as JSON, repairing the malformations we actually see:
    markdown fences, prose around the object, unescaped inner quotes, raw
    newlines inside strings, trailing commas and smart quotes.  Each repair is
    tried in turn; the first one that parses wins.
    """
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3]
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        content = content[start:end + 1]

    # build progressively-repaired candidates
    quotes_fixed = _repair_unescaped_quotes(content)
    controls_fixed = _escape_control_chars_in_strings(quotes_fixed)
    no_trailing_commas = re.sub(r",(\s*[}\]])", r"\1", controls_fixed)
    smart_fixed = (no_trailing_commas.replace("“", '"').replace("”", '"')
                   .replace("‘", "'").replace("’", "'"))

    candidates = [content, quotes_fixed, controls_fixed, no_trailing_commas,
                  smart_fixed, re.sub(r",(\s*[}\]\s])", r"\1", content)]

    last_error = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    raise last_error


def chat_json(system: str, user: str, schema: dict | None = None,
              max_tokens: int = 4096) -> dict:
    """
    One small chat call returning JSON.

    Parameter fallback ladder (thinking mode is ON by default and its tokens
    count toward max_tokens, so it must be disabled; strict json_schema may be
    unsupported and must degrade gracefully):
      1. reasoning off + strict json_schema (if a schema was given)
      2. reasoning off + json_object
      3. reasoning off, larger budget, no response_format
      4. bare request, larger budget
    """
    headers = _headers()
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    base = {"model": CHAT_MODEL, "messages": messages, "temperature": 0.2,
            "reasoning_effort": None}

    attempts = []
    if schema:
        attempts.append({**base, "max_tokens": max_tokens,
                         "response_format": {"type": "json_schema",
                                             "json_schema": {"name": "guide",
                                                             "strict": True,
                                                             "schema": schema}}})
    attempts.append({**base, "max_tokens": max_tokens,
                     "response_format": {"type": "json_object"}})
    attempts.append({**base, "max_tokens": max_tokens * 2})
    attempts.append({"model": CHAT_MODEL, "messages": messages,
                     "temperature": 0.2, "max_tokens": max_tokens * 2})

    last = "no attempt made"
    for i, body in enumerate(attempts, 1):
        try:
            response = requests.post(f"{BASE_URL}/v1/chat/completions",
                                     headers=headers, json=body, timeout=180)
        except requests.RequestException as exc:
            last = f"network error: {exc}"
            continue
        if not response.ok:
            last = f"HTTP {response.status_code}: {response.text[:200]}"
            print(f"[warn] chat attempt {i}: {last}", file=sys.stderr)
            continue
        choice = response.json()["choices"][0]
        content = choice["message"].get("content")
        if not content:
            last = f"finish_reason={choice.get('finish_reason')}, content empty"
            print(f"[warn] chat attempt {i}: {last}", file=sys.stderr)
            continue
        try:
            return _parse_json_leniently(content)
        except json.JSONDecodeError as exc:
            # a malformed reply from one mode must not abort the whole call:
            # log it and fall through to the next attempt
            last = f"invalid JSON: {exc}"
            print(f"[warn] chat attempt {i}: {last}", file=sys.stderr)
            continue

    raise RuntimeError("chat/completions failed after all attempts; last: " + last)


# --------------------------------------------------------------------------
# The three analysis calls
# --------------------------------------------------------------------------

_WORDS_SCHEMA = {
    "type": "object",
    "properties": {
        "padaccheda_sa": {"type": "string"},
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "devanagari": {"type": "string"},
                    "sanskrit_gloss": {"type": "string"},
                    "english_gloss": {"type": "string"},
                },
                "required": ["devanagari", "sanskrit_gloss", "english_gloss"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["padaccheda_sa", "words"],
    "additionalProperties": False,
}


def call_words(shloka: str) -> dict:
    system = (
        "You are a Sanskrit scholar. Analyse the shloka the user gives and "
        "return JSON with exactly these keys:\n"
        '  "padaccheda_sa": the shloka split into words, in Devanagari. Join '
        "words that are split by sandhi with ' + ', and separate the resulting "
        "word-groups with ', '. Split genuine compounds (e.g. मुनिपुङ्गवम् -> "
        "मुनि + पुङ्गवम्) but NEVER break a single word into meaningless "
        "fragments (e.g. वाग्विदाम् is ONE word, not वाक् + वि + दाम्). Do NOT "
        "include the danda marks (| ||) at all.\n"
        '  "words": an array, one object per word IN ORDER, each with:\n'
        '      "devanagari": the word in DEVANAGARI ONLY (never Kannada),\n'
        '      "sanskrit_gloss": a real Sanskrit synonym in Devanagari, '
        "optionally with a short parenthetical (e.g. "
        "\"तपस् (शरीर-वाङ्-मनःसु संयमः)\"). Never simply repeat the word "
        "itself.\n"
        '      "english_gloss": a short English meaning.\n'
        "Every devanagari field must contain ONLY Devanagari characters. "
        "Do not use straight double-quote characters inside values (use single "
        "quotes if you need to quote a phrase). Return JSON only, no markdown "
        "fences."
    )
    return chat_json(system, shloka, schema=_WORDS_SCHEMA)


def call_sanskrit_prose(shloka: str) -> dict:
    system = (
        "You are a Sanskrit scholar. Return JSON with exactly two keys:\n"
        '  "anvaya_sa": the shloka rearranged into natural Sanskrit prose '
        "order in Devanagari, with CORRECT case endings (the subject in the "
        "nominative, the object in the accusative). Do not repeat words.\n"
        '  "bhavartha_sa": 3-5 sentences of simple Sanskrit giving the overall '
        "purport. Mention that this is the first verse of the Ramayana and that "
        "Valmiki asks Narada the question beginning "
        "\"को न्वस्मिन् साम्प्रतं लोके...\".\n"
        "Do not use straight double-quote characters inside values (use single "
        "quotes if you need to quote a phrase). Return JSON only, no markdown "
        "fences."
    )
    return chat_json(system, shloka)


# The Kannada and English content is fetched in TWO small calls rather than
# one: a four-key reply was too large for the model to keep as valid JSON.


def call_kannada(shloka: str) -> dict:
    system = (
        "You are a scholar fluent in Kannada. Return JSON with exactly three "
        "keys:\n"
        '  "padartha_kn": an array, one string per word IN ORDER, each '
        "formatted as 'ಕನ್ನಡಪದ — ಕನ್ನಡ ಅರ್ಥ' (the Kannada word, an em dash, then "
        "a real Kannada meaning, not the word repeated).\n"
        '  "anvaya_kn": the verse in natural Kannada prose (a real Kannada '
        "sentence with correct grammar, not a transliteration of the Sanskrit).\n"
        '  "saransha_kn": 3-5 sentences in Kannada giving the overall purport, '
        "including the question Valmiki asks Narada.\n"
        "Use correct Kannada spelling (e.g. ವಾಗ್ವಿದ, ಪರಿಪಪ್ರಚ್ಛ). Write Kannada "
        "in KANNADA SCRIPT ONLY, never Devanagari. padartha_kn MUST be a JSON "
        "array with one element per word, in the same order as the shloka — "
        "never a single string containing the whole list. Do not invent events "
        "or details that are not in the verse. EVERY string value must be a "
        "single line with no literal line breaks. Do not use straight "
        "double-quote characters inside values (use single quotes if you need "
        "to quote a phrase). Return JSON only, no markdown fences."
    )
    return chat_json(system, shloka)


def call_english(shloka: str) -> dict:
    system = (
        "You are a scholar of Sanskrit. Return JSON with exactly one key:\n"
        '  "meaning_en": two paragraphs in English — a faithful translation of '
        "the verse, then its context and significance. Separate the two "
        "paragraphs with the two characters \\n (escaped), never a literal line "
        "break. Do not invent events or details that are not in the verse. Do "
        "not use straight double-quote characters inside the value. Return JSON "
        "only, no markdown fences."
    )
    return chat_json(system, shloka)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def fence(text: str) -> str:
    """Wrap a block in a fenced code block so it can be copied cleanly from a
    markdown editor (the verse, the word-split and the prose paragraphs)."""
    return "```\n" + text.strip("\n") + "\n```"


def strip_code_fence(text: str) -> str:
    """Remove a wrapping ``` fence, if present (so a fenced file can still be
    read back as input)."""
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def hard_break_lines(text: str) -> str:
    """Append two trailing spaces to every line but the last, so the verse's
    line break survives markdown rendering (a hard break)."""
    lines = text.split("\n")
    return "\n".join(ln + "  " if i < len(lines) - 1 else ln
                     for i, ln in enumerate(lines))


# The six scholar-verification front-matter fields: a flag plus who verified it
# and when, for each language. They are reset on every regeneration.
_VERIFICATION_FIELDS = (
    "sanskrit-scholar-verified",
    "sanskrit-scholar-verified-by",
    "sanskrit-scholar-verified-on",
    "kannada-scholar-verified",
    "kannada-scholar-verified-by",
    "kannada-scholar-verified-on",
)


def fm(key: str, value: str) -> str:
    """Render a front-matter line, with no trailing space when the value is blank."""
    return f"{key}:{' ' + value if value else ''}"


def render_markdown(shloka: str, words: dict, sanskrit: dict,
                    kannada: dict, meta: dict) -> str:
    lines = []
    padaccheda_sa = clean_padaccheda(words["padaccheda_sa"])
    shloka_display = hard_break_lines(shloka)
    kannada_display = hard_break_lines(devanagari_to_kannada(shloka))
    # IAST is computed locally, so the English section can never show the
    # padaccheda in its place again.
    iast_display = hard_break_lines(devanagari_to_iast(shloka))
    roman_display = hard_break_lines(strip_diacritics(devanagari_to_iast(shloka)))

    # ---- frontmatter ----
    lines += ["---",
              f"ಕಾಂಡ: {meta['kanda']}",
              f"ಸರ್ಗ: {meta['sarga']}",
              f"ಶ್ಲೋಕ: {meta['shloka_no']}",
              "tags:"]
    lines += [f"  - {t}" for t in meta["tags"]]
    # scholar-verification fields — always reset on regeneration
    lines += [fm(field, meta.get(field.replace("-", "_"), ""))
              for field in _VERIFICATION_FIELDS]
    lines += ["---"]

    # ---- Sanskrit ----  (verse / word-split / prose blocks are fenced so they
    # copy cleanly; the padartha bullet list is left as a list)
    lines += ["## संस्कृत (Sanskrit)", "",
              "### संहिता", "", fence(shloka_display), "",
              "### पदच्छेद", "", fence(padaccheda_sa), "",
              "### पदार्थ", ""]
    for w in words["words"]:
        lines.append(f"- {w['devanagari']} — {w['sanskrit_gloss']}")
    lines += ["", "### अन्वया", "", fence(sanskrit["anvaya_sa"]), "",
              "### भावार्थ", "", fence(sanskrit["bhavartha_sa"]), ""]

    # ---- Kannada (all scripts derived deterministically in Python) ----
    # The Kannada padaccheda follows the same grouping as the Sanskrit one but
    # joins sandhi-split words with '-' (the convention in the reference guide).
    padaccheda_kn = devanagari_to_kannada(padaccheda_sa).replace(" + ", "-")
    lines += ["## ಕನ್ನಡ (Kannada)", "",
              "### ಸಂಹಿತೆ", "", fence(kannada_display), "",
              "### ಪದಚ್ಛೇದ", "", fence(padaccheda_kn), "",
              "### ಪದಾರ್ಥ", ""]
    padartha_kn = normalise_padartha_list(kannada["padartha_kn"])
    for item in padartha_kn:
        lines.append(f"- {normalise_kannada(item)}")
    lines += ["", "### ಅನ್ವಯ", "", fence(normalise_kannada(kannada["anvaya_kn"])), "",
              "### ಸಾರಾಂಶ", "", fence(normalise_kannada(kannada["saransha_kn"])), ""]
    if len(padartha_kn) != len(words["words"]):
        print(f"[warn] padartha_kn has {len(padartha_kn)} entries "
              f"but the shloka has {len(words['words'])} words", file=sys.stderr)

    # ---- English ----
    lines += ["## English", "", fence(iast_display), "", "OR", "",
              fence(roman_display), "",
              "### Word By Word Meaning", ""]
    for w in words["words"]:
        dev = to_devanagari(w["devanagari"])          # normalise any script slip
        if contains_kannada(w["devanagari"]):
            print(f"[warn] model put Kannada in a Devanagari field: {w['devanagari']}",
                  file=sys.stderr)
        translit = strip_diacritics(devanagari_to_iast(dev))
        lines.append(
            f"- {translit} - {dev} - "
            f"{devanagari_to_kannada(dev)} = {w['english_gloss']}"
        )
    lines += ["", "### Meaning", "", fence(kannada["meaning_en"]), ""]

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Input file handling (read shloka + derive metadata from the file name)
# --------------------------------------------------------------------------

# Ramayana kandas, in order.
_KANDA_NAMES = {
    1: "ಬಾಲ ಕಾಂಡ",
    2: "ಅಯೋಧ್ಯ ಕಾಂಡ",
    3: "ಅರಣ್ಯ ಕಾಂಡ",
    4: "ಕಿಷ್ಕಿಂಧಾ ಕಾಂಡ",
    5: "ಸುಂದರ ಕಾಂಡ",
    6: "ಯುದ್ಧ ಕಾಂಡ",
    7: "ಉತ್ತರ ಕಾಂಡ",
}
_KANDA_SLUGS = {
    1: "balakanda", 2: "ayodhyakanda", 3: "aranyakanda",
    4: "kishkindhakanda", 5: "sundarakanda", 6: "yuddhakanda",
    7: "uttarakanda",
}

_ASCII_TO_KANNADA_DIGITS = str.maketrans("0123456789", "೦೧೨೩೪೫೬೭೮೯")


def to_kannada_digits(value) -> str:
    """'1' -> '೧' (Kannada numerals, as used in the frontmatter)."""
    return str(value).translate(_ASCII_TO_KANNADA_DIGITS)


def parse_filename(path: str):
    """shloka-<kanda>-<sarga>-<shloka>.md -> (kanda, sarga, shloka) ints, or None."""
    match = re.search(r"shloka[-_](\d+)[-_](\d+)[-_](\d+)",
                      os.path.basename(path), re.IGNORECASE)
    if not match:
        return None
    return tuple(int(g) for g in match.groups())


def extract_shloka(md_text: str) -> str:
    """
    Pull the Sanskrit shloka out of a markdown file.

    Prefers the body of the '### संहिता' section; otherwise falls back to
    collecting the Devanagari lines (ignoring frontmatter and headings).
    """
    section = re.search(r"^###\s*संहिता\s*$(.*?)(?=^###|^##|\Z)",
                        md_text, re.MULTILINE | re.DOTALL)
    if section:
        body = strip_code_fence(section.group(1).strip())
        if body:
            return "\n".join(ln.strip() for ln in body.splitlines())

    lines = [ln.strip() for ln in md_text.splitlines()
             if re.search(r"[\u0900-\u097F]", ln) and not ln.lstrip().startswith("#")]
    return "\n".join(lines).strip()


def read_frontmatter_flags(md_text: str) -> dict:
    """Read the scholar-verification flags from a file's YAML front matter.

    Used only to WARN when regenerating a file that had been verified: the
    flags are reset, because new content has to be re-verified."""
    flags = {}
    match = re.match(r"\A---\s*\n(.*?)\n---", md_text, re.DOTALL)
    if not match:
        return flags
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key in _VERIFICATION_FIELDS:
            flags[key.replace("-", "_")] = value.strip()
    return flags


def derive_meta_from_filename(path: str):
    """Return (kanda_name, sarga_str, shloka_str, tags_list) or None."""
    parsed = parse_filename(path)
    if not parsed:
        return None
    kanda_num, sarga, shloka_no = parsed
    return (
        _KANDA_NAMES.get(kanda_num, f"ಕಾಂಡ {kanda_num}"),
        to_kannada_digits(sarga),
        to_kannada_digits(shloka_no),
        [_KANDA_SLUGS.get(kanda_num, "kanda"), f"sarga-{sarga}", "shloka"],
    )


def write_atomically(path: str, text: str) -> None:
    """Write via a temp file + os.replace so the target is never half-written."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def selftest() -> None:
    """Verify the deterministic script map offline (no API key needed)."""
    print("Devanagari -> Kannada self-test on the default shloka:\n")
    print(devanagari_to_kannada(DEFAULT_SHLOKA))
    print("\nRound-trip back to Devanagari:\n")
    print(to_devanagari(devanagari_to_kannada(DEFAULT_SHLOKA)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shloka", nargs="*", help="the shloka (default: Bala Kanda 1.1)")
    parser.add_argument("--input", help="markdown file to read the shloka from and replace")
    parser.add_argument("--output", help="write the guide here (default: replace --input, else stdout)")
    parser.add_argument("--kanda", help="kanda name (default: derived from --input)")
    parser.add_argument("--sarga", help="sarga number (default: derived from --input)")
    parser.add_argument("--shloka-no", help="shloka number (default: derived from --input)")
    parser.add_argument("--tags", help="comma-separated tags (default: derived from --input)")
    parser.add_argument("--sanskrit-verified",
                        help="sanskrit-scholar-verified: true/false")
    parser.add_argument("--sanskrit-verified-by",
                        help="sanskrit-scholar-verified-by: who verified it")
    parser.add_argument("--sanskrit-verified-on",
                        help="sanskrit-scholar-verified-on: date of verification")
    parser.add_argument("--kannada-verified",
                        help="kannada-scholar-verified: true/false")
    parser.add_argument("--kannada-verified-by",
                        help="kannada-scholar-verified-by: who verified it")
    parser.add_argument("--kannada-verified-on",
                        help="kannada-scholar-verified-on: date of verification")
    parser.add_argument("--selftest", action="store_true",
                        help="test the Devanagari->Kannada map and exit")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return

    # ---- work out the shloka and the output target ----
    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            source_text = fh.read()
        shloka = extract_shloka(source_text)
        if not shloka:
            sys.exit(f"No Sanskrit shloka found in {args.input}")
        output_path = args.output or args.input        # replace in place by default
        derived = derive_meta_from_filename(args.input)
        # Regenerating the content invalidates any earlier verification, so the
        # scholar-verification flags are RESET. Warn if the file was verified.
        previous_flags = read_frontmatter_flags(source_text)
        if any(previous_flags.values()):
            print(f"[warn] resetting scholar-verification flags in {args.input} "
                  f"({previous_flags}) — the regenerated content must be "
                  f"re-verified", file=sys.stderr)
        if not derived:
            print(f"[warn] {args.input}: filename is not "
                  f"shloka-<kanda>-<sarga>-<shloka>.md; using defaults",
                  file=sys.stderr)
    else:
        shloka = " ".join(args.shloka) if args.shloka else DEFAULT_SHLOKA
        output_path = args.output
        derived = None

    # explicit flags win over filename-derived values, which win over defaults
    kanda = args.kanda or (derived[0] if derived else None) or "ಬಾಲ ಕಾಂಡ"
    sarga = args.sarga or (derived[1] if derived else None) or "೧"
    shloka_no = args.shloka_no or (derived[2] if derived else None) or "೧"
    tags = args.tags or (",".join(derived[3]) if derived else "balakanda,sarga-1,shloka")

    meta = {"kanda": kanda, "sarga": sarga, "shloka_no": shloka_no,
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
            # reset to blank on every regeneration; only an explicit flag sets them
            "sanskrit_scholar_verified": args.sanskrit_verified or "",
            "sanskrit_scholar_verified_by": args.sanskrit_verified_by or "",
            "sanskrit_scholar_verified_on": args.sanskrit_verified_on or "",
            "kannada_scholar_verified": args.kannada_verified or "",
            "kannada_scholar_verified_by": args.kannada_verified_by or "",
            "kannada_scholar_verified_on": args.kannada_verified_on or ""}

    print(f"Metadata: kanda={kanda!r} sarga={sarga} shloka={shloka_no} "
          f"tags={meta['tags']}", file=sys.stderr)

    print("Call 1/4: splitting words...", file=sys.stderr)
    words = call_words(shloka)
    print("Call 2/4: Sanskrit anvaya + bhavartha...", file=sys.stderr)
    sanskrit = call_sanskrit_prose(shloka)
    print("Call 3/4: Kannada padartha + anvaya + saransha...", file=sys.stderr)
    kannada = call_kannada(shloka)
    print("Call 4/4: English meaning...", file=sys.stderr)
    kannada.update(call_english(shloka))   # render_markdown reads meaning_en from here

    guide = render_markdown(shloka, words, sanskrit, kannada, meta)

    if output_path:
        write_atomically(output_path, guide)
        print(f"Study guide written to {output_path}")
    else:
        print(guide)


if __name__ == "__main__":
    main()
