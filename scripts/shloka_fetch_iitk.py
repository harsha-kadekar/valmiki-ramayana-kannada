#!/usr/bin/env python3
"""
Generate the input markdown file (frontmatter + the Sanskrit shloka only) that
shloka_study_guide.py consumes, by fetching the verse from the IIT Kanpur
Heritage Scripture Server API.

Source
------
    IIT Kanpur Heritage Scripture Server — Public API
    Base URL : https://heritage.iitk.ac.in/api/v1/
    Auth     : header  Authorization: Api-Key <YOUR_KEY>
    Text code: VRAM  (Śrīmad Vālmīki Rāmāyaṇam)
    Verse ref: <kanda>/<sarga>/<shloka>   e.g. 1/1/3
    Moolam   : GET /api/v1/VRAM/1/1/3/moolam/            (Devanagari)
               GET /api/v1/VRAM/1/1/3/moolam/<script>/   (other scripts)

Get a free key (50 units/day) at https://heritage.iitk.ac.in/api-access
The Sanskrit moolam is public domain; translations/commentaries are not, and we
only ever fetch the moolam here.

Usage
-----
    export HERITAGE_API_KEY="your-key"

    # the shloka number comes from the file name: shloka-<kanda>-<sarga>-<shloka>.md
    python shloka_fetch_iitk.py shloka-1-1-3.md

    # or give the numbers explicitly
    python shloka_fetch_iitk.py --kanda 1 --sarga 1 --shloka 3 --output shloka-1-1-3.md

    python shloka_fetch_iitk.py --check       # verify connectivity / key
    python shloka_fetch_iitk.py --selftest    # offline: test parsing + rendering
"""

import argparse
import json
import os
import re
import sys
import time

import requests

API_BASE = "https://heritage.iitk.ac.in/api/v1"
TEXT_CODE = "VRAM"                     # Valmiki Ramayana
TIMEOUT = 30
MAX_RETRIES = 3                        # for 429 / transient errors

# Ramayana kandas, in order (Kannada names used in the frontmatter).
_KANDA_NAMES = {
    1: "ಬಾಲ ಕಾಂಡ", 2: "ಅಯೋಧ್ಯ ಕಾಂಡ", 3: "ಅರಣ್ಯ ಕಾಂಡ", 4: "ಕಿಷ್ಕಿಂಧಾ ಕಾಂಡ",
    5: "ಸುಂದರ ಕಾಂಡ", 6: "ಯುದ್ಧ ಕಾಂಡ", 7: "ಉತ್ತರ ಕಾಂಡ",
}
_KANDA_SLUGS = {
    1: "balakanda", 2: "ayodhyakanda", 3: "aranyakanda", 4: "kishkindhakanda",
    5: "sundarakanda", 6: "yuddhakanda", 7: "uttarakanda",
}

_ASCII_TO_KANNADA_DIGITS = str.maketrans("0123456789", "೦೧೨೩೪೫೬೭೮೯")
_FILENAME_RE = re.compile(r"shloka[-_](\d+)[-_](\d+)[-_](\d+)", re.IGNORECASE)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def to_kannada_digits(value) -> str:
    return str(value).translate(_ASCII_TO_KANNADA_DIGITS)


def parse_filename(path: str):
    """shloka-<kanda>-<sarga>-<shloka>.md -> (kanda, sarga, shloka) or None."""
    match = _FILENAME_RE.search(os.path.basename(path))
    return tuple(int(g) for g in match.groups()) if match else None


def api_key_from_env() -> str:
    key = os.environ.get("HERITAGE_API_KEY")
    if not key:
        sys.exit("Please set HERITAGE_API_KEY (get a free key at "
                 "https://heritage.iitk.ac.in/api-access)")
    return key


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------


def fetch_moolam(kanda: int, sarga: int, shloka: int, key: str,
                 script: str | None = None, debug: bool = False) -> str:
    """Return the raw moolam text for a verse."""
    ref = f"{kanda}/{sarga}/{shloka}"
    url = f"{API_BASE}/{TEXT_CODE}/{ref}/moolam/"
    if script:
        url += f"{script}/"
    headers = {"Authorization": f"Api-Key {key}", "Accept": "application/json"}

    last_error = "no attempt made"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_error = f"network error: {exc}"
            time.sleep(1.5 * attempt)
            continue

        if response.status_code == 200:
            payload = response.json()
            if debug:
                print("--- raw API response ---", file=sys.stderr)
                print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000],
                      file=sys.stderr)
            return extract_verse_text(payload)

        if response.status_code == 429:                      # rate limited
            last_error = "429 rate limited (30 req/min cap)"
            time.sleep(5 * attempt)
            continue

        if response.status_code in (401, 403):
            sys.exit(f"Authentication failed (HTTP {response.status_code}): "
                     f"{response.text[:200]}\nCheck HERITAGE_API_KEY "
                     f"(get one at https://heritage.iitk.ac.in/api-access)")
        if response.status_code == 404:
            sys.exit(f"Verse not found (HTTP 404) for {TEXT_CODE} ref '{ref}'. "
                     f"URL: {url}\n"
                     f"If your edition numbers the verse differently, adjust "
                     f"the kanda/sarga/shloka values.")
        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        time.sleep(1.5 * attempt)

    sys.exit(f"Failed to fetch {url} after {MAX_RETRIES} attempts; last: {last_error}")


def extract_verse_text(payload) -> str:
    """
    Pull the Sanskrit text out of the API response, tolerating several shapes:
      * a plain string
      * a list of moolam objects               [{"text": ..., "script": ...}]
      * {"moolams": [...]}  or  {"moolam": ...} or  {"data": ...}
      * a single object with a text-ish key
    """
    def pick(obj):
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            for key in ("text", "moolam_text", "content", "verse", "shloka",
                        "moolam", "data", "moolams", "results", "items"):
                if key in obj:
                    got = pick(obj[key])
                    if got:
                        return got
        if isinstance(obj, list):
            for item in obj:
                got = pick(item)
                if got:
                    return got
        return ""

    text = pick(payload)
    if not text:
        sys.exit("Could not find the verse text in the API response. "
                 "Re-run with --debug to inspect it.")
    return text


# --------------------------------------------------------------------------
# verse normalisation (raw moolam -> the two-line form the md uses)
# --------------------------------------------------------------------------


def normalize_verse(text: str) -> str:
    """
    'तपस्स्वाध्यायनिरतं तपस्वी वाग्विदां वरम् । नारदं परिपप्रच्छ वाल्मीकिर्मुनिपुङ्गवम् ।।1.1.1।।'
        ->
    'तपस्स्वाध्यायनिरतं तपस्वी वाग्विदां वरम् |
     नारदं परिपप्रच्छ वाल्मीकिर्मुनिपुङ्गवम् ||'
    """
    t = re.sub(r"\s+", " ", text).strip()
    # drop a trailing verse reference such as ।।1.1.1।।, ॥ १.१.१ ॥ or ॥ २ ॥
    # (must be preceded by a danda so a genuine number in the text survives)
    t = re.sub(r"[।॥|]+\s*[०-९0-9]+(?:\s*[.\-–]\s*[०-९0-9]+)*\s*[।॥|]*\s*$",
               "", t).strip()
    t = re.sub(r"[।|]+\s*$", "", t).strip()
    padas = [p.strip() for p in re.split(r"[।|]+", t) if p.strip()]
    if len(padas) >= 2:
        return " |\n".join(padas) + " ||"
    return (t + " ||") if t else ""


# --------------------------------------------------------------------------
# markdown template (same layout shloka_study_guide.py reads)
# --------------------------------------------------------------------------


def build_markdown(kanda: int, sarga: int, shloka: int, verse: str) -> str:
    lines = [
        "---",
        f"ಕಾಂಡ: {_KANDA_NAMES.get(kanda, f'ಕಾಂಡ {kanda}')}",
        f"ಸರ್ಗ: {to_kannada_digits(sarga)}",
        f"ಶ್ಲೋಕ: {to_kannada_digits(shloka)}",
        "tags:",
        f"  - {_KANDA_SLUGS.get(kanda, 'kanda')}",
        f"  - sarga-{sarga}",
        "  - shloka",
        # scholar-verification fields: blank by default, filled by a human later
        "sanskrit-scholar-verified:",
        "sanskrit-scholar-verified-by:",
        "sanskrit-scholar-verified-on:",
        "kannada-scholar-verified:",
        "kannada-scholar-verified-by:",
        "kannada-scholar-verified-on:",
        "---",
        "## संस्कृत (Sanskrit)",
        "",
        "### संहिता",
        "",
        "```",
        verse,
        "```",
        "",
        "### पदच्छेद",
        "",
        "### पदार्थ",
        "",
        "### अन्वया",
        "",
        "### भावार्थ",
        "",
        "## ಕನ್ನಡ (Kannada)",
        "",
        "### ಸಂಹಿತೆ",
        "",
        "### ಪದಚ್ಛೇದ",
        "",
        "### ಪದಾರ್ಥ",
        "",
        "### ಅನ್ವಯ",
        "",
        "### ಸಾರಾಂಶ",
        "",
        "## English",
        "",
        "### Word By Word Meaning",
        "",
        "### Meaning",
        "",
    ]
    return "\n".join(lines)


def write_atomically(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------

_SAMPLE_PAYLOAD = {
    "moolams": [
        {"script": "Devanagari", "language": "sa", "meter": "anuṣṭubh",
         "text": "तपस्स्वाध्यायनिरतं तपस्वी वाग्विदां वरम् । "
                 "नारदं परिपप्रच्छ वाल्मीकिर्मुनिपुङ्गवम् ।।1.1.1।।"}
    ]
}


def selftest() -> None:
    print("== extract_verse_text on several payload shapes ==")
    for payload in (
        _SAMPLE_PAYLOAD,
        [{"text": "कः न्वस्मिन् साम्प्रतं लोके गुणवान् कश्च वीर्यवान् । "
                  "धर्मज्ञश्च कृतज्ञश्च सत्यवाक्यो दृढव्रतः ।।1.1.2।।"}],
        {"moolam": {"text": "अगस्त्यमथ ... ।।4.5.3।।"}},
        {"text": "श्लोकः । द्वितीयः पादः ॥ २ ॥"},
    ):
        raw = extract_verse_text(payload)
        print("  raw   :", raw)
        print("  norm  :", normalize_verse(raw).replace("\n", "\\n"))
    print("\n== parse_filename ==")
    for name in ("shloka-1-1-3.md", "shloka-4-5-3.md", "Shloka-1-1-1.md", "other.md"):
        print(f"  {name} -> {parse_filename(name)}")
    print("\n== generated markdown (sample) ==")
    print(build_markdown(1, 1, 1, normalize_verse(extract_verse_text(_SAMPLE_PAYLOAD))))


def check(key: str) -> None:
    """Confirm the key works and the Ramayana text is reachable."""
    root = requests.get(f"{API_BASE}/", timeout=TIMEOUT)
    print(f"API root: HTTP {root.status_code} — "
          f"{root.json().get('description', '')}")
    response = requests.get(
        f"{API_BASE}/{TEXT_CODE}/1/1/1/moolam/",
        headers={"Authorization": f"Api-Key {key}"}, timeout=TIMEOUT)
    print(f"VRAM 1/1/1 moolam: HTTP {response.status_code}")
    if response.status_code == 200:
        print("  text:", normalize_verse(extract_verse_text(response.json()))
              .replace("\n", "\\n"))
    else:
        print("  body:", response.text[:300])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?",
                        help="output md path, e.g. shloka-1-1-3.md "
                             "(kanda/sarga/shloka are read from the name)")
    parser.add_argument("--output", help="output md path (overrides target)")
    parser.add_argument("--kanda", type=int, help="kanda number (1-7)")
    parser.add_argument("--sarga", type=int, help="sarga number")
    parser.add_argument("--shloka", type=int, help="shloka number")
    parser.add_argument("--api-key", help="Heritage API key (else $HERITAGE_API_KEY)")
    parser.add_argument("--script", help="fetch moolam in this script "
                                         "(default: Devanagari)")
    parser.add_argument("--debug", action="store_true", help="dump the raw response")
    parser.add_argument("--stdout", action="store_true", help="print, don't write")
    parser.add_argument("--check", action="store_true", help="verify key + connectivity")
    parser.add_argument("--selftest", action="store_true", help="offline parsing test")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return

    key = args.api_key or api_key_from_env()

    if args.check:
        check(key)
        return

    # ---- work out kanda / sarga / shloka ----
    out_path = args.output or args.target
    from_name = parse_filename(out_path) if out_path else None
    kanda = args.kanda or (from_name[0] if from_name else None)
    sarga = args.sarga or (from_name[1] if from_name else None)
    shloka = args.shloka or (from_name[2] if from_name else None)

    if not all((kanda, sarga, shloka)):
        parser.error("could not determine kanda/sarga/shloka — pass an output "
                     "path like shloka-1-1-3.md, or use --kanda/--sarga/--shloka")
    if not out_path and not args.stdout:
        parser.error("no output path given (pass a path or --output)")

    print(f"Fetching {TEXT_CODE} {kanda}/{sarga}/{shloka} moolam...", file=sys.stderr)
    raw = fetch_moolam(kanda, sarga, shloka, key, script=args.script,
                       debug=args.debug)
    verse = normalize_verse(raw)
    print(f"  verse: {verse.replace(chr(10), ' / ')}", file=sys.stderr)

    markdown = build_markdown(kanda, sarga, shloka, verse)

    if args.stdout or not out_path:
        print(markdown)
    else:
        write_atomically(out_path, markdown)
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
