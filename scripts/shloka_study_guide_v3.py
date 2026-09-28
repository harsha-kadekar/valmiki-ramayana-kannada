#!/usr/bin/env python3
"""
Generate a tri-lingual (Sanskrit / Kannada / English) study guide for a shloka,
using the Sarvam AI APIs:

  1. POST /transliterate        -> Devanagari -> Kannada script, Devanagari -> simple Roman
  2. POST /v1/chat/completions  -> padaccheda, padaartha, anvaya, bhaavaartha,
                                   IAST transliteration, word-by-word meaning, translation

Usage:
    export SARVAM_API_KEY="sk_..."        # from https://dashboard.sarvam.ai/
    pip install requests
    python shloka_study_guide.py                       # uses the default shloka
    python shloka_study_guide.py "your shloka here"   # custom shloka
    python shloka_study_guide.py --output guide.md     # write to a file

The markdown output follows this structure:

    ## संस्कृत (Sanskrit)   -> संहिता, पदच्छेद, पदार्थ, अन्वय, भावार्थ
    ## ಕನ್ನಡ (Kannada)      -> ಸಂಹಿತೆ, ಪದಚ್ಛೇದ, ಪದಾರ್ಥ, ಅನ್ವಯ, ಸಾರಾಂಶ
    ## English              -> IAST, simple Roman, Word By Word Meaning, Meaning
"""

import json
import os
import sys

import requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

API_KEY = os.environ.get("SARVAM_API_KEY")
if not API_KEY:
    sys.exit("Please set the SARVAM_API_KEY environment variable (get one at https://dashboard.sarvam.ai/)")

BASE_URL = "https://api.sarvam.ai"
# sarvam-30b is deprecated and rejected by the API with a 400 error;
# sarvam-105b is the currently supported chat model.
CHAT_MODEL = "sarvam-105b"

DEFAULT_SHLOKA = (
    "तपःस्वाध्यायनिरतं तपस्वी वाग्विदां वरम् |\n"
    "नारदं परिपप्रच्छ वाल्मीकिर्मुनिपुङ्गवम् ||"
)

# --------------------------------------------------------------------------
# Sarvam API helpers
# --------------------------------------------------------------------------


def transliterate(text: str, target: str, source: str = "hi-IN") -> str:
    """
    Call Sarvam's /transliterate endpoint.
    Devanagari is treated as hi-IN; target may be "kn-IN" (Kannada script)
    or "en-IN" (simple Roman, no diacritics).
    """
    response = requests.post(
        f"{BASE_URL}/transliterate",
        headers={"api-subscription-key": API_KEY},
        json={
            "input": text,
            "source_language_code": source,
            "target_language_code": target,
        },
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(
            f"transliterate failed: HTTP {response.status_code}: {response.text}"
        )
    return response.json()["transliterated_text"]


def chat_json(shloka: str) -> dict:
    """
    Ask the Sarvam chat model to produce the scholarly analysis as JSON.
    The script (not the model) is responsible for the final markdown layout,
    so the output format is guaranteed to match the template.
    """
    system_prompt = (
        "You are a Sanskrit scholar and teacher of Vyakarana. Given a shloka in "
        "Devanagari, analyse it and return ONLY a valid JSON object with exactly "
        "these keys:\n"
        "{\n"
        '  "padaccheda_sa": "word-split of the shloka in Devanagari, words joined with " + ", sandhi splits shown as "वाग्विदाम् + वरम्"",\n'
        '  "padartha_sa": ["देवनागरी word — सरल संस्कृत पर्याय (simple Sanskrit synonym/short gloss)", ...],\n'
        '  "anvaya_sa": "the shloka rearranged into natural Sanskrit prose order, Devanagari",\n'
        '  "bhavartha_sa": "2-4 sentences in simple Sanskrit giving the overall purport",\n'
        '  "padaccheda_kn": "the same word-split in Kannada script, words joined with "-"",\n'
        '  "padartha_kn": ["ಕನ್ನಡ ಪದ — ಕನ್ನಡ ಅರ್ಥ", ...],\n'
        '  "anvaya_kn": "the anvaya in Kannada prose",\n'
        '  "saransha_kn": "2-4 sentences in Kannada giving the overall purport",\n'
        '  "kannada_shloka": "the FULL shloka written in Kannada script (joined, no hyphens), keeping the | and || marks",\n'
        '  "iast": "IAST transliteration of the full shloka with diacritics (tapaḥ, ā, ṃ etc.), keeping the | and || marks",\n'
        '  "word_by_word": [{"translit": "simple Roman", "devanagari": "पदम्", "kannada": "ಪದ", "meaning": "English meaning"}, ...],\n'
        '  "meaning_en": "2-4 sentences in English: a faithful translation of the shloka plus its context/significance"\n'
        "}\n"
        "Rules: padartha_sa, padartha_kn and word_by_word must each list EVERY word "
        "of the shloka in order. Use scholarly but simple language. Output JSON only, "
        "no markdown fences."
    )

    # Send both auth styles; either one is accepted.
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "api-subscription-key": API_KEY,
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": shloka},
    ]

    def _post(payload: dict) -> requests.Response:
        return requests.post(
            f"{BASE_URL}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=180,
        )

    # Thinking mode is ON by default: reasoning tokens count toward
    # max_tokens, so a modest budget gets consumed entirely by reasoning and
    # message.content comes back as null with finish_reason "length".
    # We therefore disable reasoning ("reasoning_effort": null) -- this task
    # needs direct output, not chain-of-thought -- and escalate the token
    # budget / drop optional parameters if a request still fails.
    base_payload = {
        "model": CHAT_MODEL,
        "messages": messages,
        "temperature": 0.2,
    }
    attempts = [
        # 1) preferred: no reasoning, JSON mode, standard budget
        {**base_payload, "reasoning_effort": None, "max_tokens": 4096,
         "response_format": {"type": "json_object"}},
        # 2) if response_format or the token cap was rejected: simpler body
        {**base_payload, "reasoning_effort": None, "max_tokens": 8192},
        # 3) if reasoning_effort itself was rejected: bare request
        {**base_payload, "max_tokens": 8192},
    ]

    last_error = "no attempt was made"
    for i, payload in enumerate(attempts, 1):
        response = _post(payload)
        if not response.ok:
            last_error = f"HTTP {response.status_code}: {response.text}"
            print(f"[warn] attempt {i} failed ({last_error[:120]}...)", file=sys.stderr)
            continue

        data = response.json()
        choice = data["choices"][0]
        content = choice["message"].get("content")
        if not content:
            last_error = (
                f"finish_reason={choice.get('finish_reason')}, content empty "
                f"(reasoning consumed the token budget at "
                f"max_tokens={payload.get('max_tokens')})"
            )
            print(f"[warn] attempt {i}: {last_error}", file=sys.stderr)
            continue
        return _parse_json_leniently(content)

    raise RuntimeError(
        "chat/completions failed after all attempts; last error: " + last_error
    )


def _parse_json_leniently(content: str) -> dict:
    """Parse a model reply as JSON, tolerating markdown code fences."""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3]
    # If the model wrapped JSON in prose, grab the outermost braces.
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        content = content[start : end + 1]
    return json.loads(content)


def transliterate_to_kannada(text: str, fallback: str = "") -> str:
    """
    Devanagari -> Kannada script. The /transliterate API does not support
    Indic-Indic transliteration, so we go via Roman: Devanagari -> en-IN,
    then the Roman text -> kn-IN. Falls back to the model-provided text.
    """
    try:
        roman = transliterate(text, "en-IN")
        return transliterate(roman, "kn-IN", source="en-IN")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[warn] two-hop transliteration to Kannada failed ({exc}); "
            f"using fallback",
            file=sys.stderr,
        )
        return fallback


def safe_transliterate(text: str, target: str, fallback: str = "") -> str:
    """Transliterate via the API; if it fails, return the fallback."""
    try:
        return transliterate(text, target)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] transliterate({target}) failed ({exc}); using fallback", file=sys.stderr)
        return fallback


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------


def render_markdown(shloka: str, kannada_shloka: str, roman_shloka: str, analysis: dict) -> str:
    """Assemble the tri-lingual study guide in the exact template layout."""
    lines = []

    # ---------------- Sanskrit ----------------
    lines += [
        "## संस्कृत (Sanskrit)",
        "",
        "### संहिता",
        "",
        shloka,
        "",
        "### पदच्छेद",
        "",
        analysis["padaccheda_sa"],
        "",
        "### पदार्थ",
        "",
    ]
    lines += [f"- {item}" for item in analysis["padartha_sa"]]
    lines += [
        "",
        "### अन्वय",
        "",
        analysis["anvaya_sa"],
        "",
        "### भावार्थ",
        "",
        analysis["bhavartha_sa"],
        "",
    ]

    # ---------------- Kannada ----------------
    lines += [
        "## ಕನ್ನಡ (Kannada)",
        "",
        "### ಸಂಹಿತೆ",
        "",
        kannada_shloka,
        "",
        "### ಪದಚ್ಛೇದ",
        "",
        analysis["padaccheda_kn"],
        "",
        "### ಪದಾರ್ಥ",
        "",
    ]
    lines += [f"- {item}" for item in analysis["padartha_kn"]]
    lines += [
        "",
        "### ಅನ್ವಯ",
        "",
        analysis["anvaya_kn"],
        "",
        "### ಸಾರಾಂಶ",
        "",
        analysis["saransha_kn"],
        "",
    ]

    # ---------------- English ----------------
    lines += [
        "## English",
        "",
        analysis["iast"],
        "",
        "OR",
        "",
        roman_shloka,
        "",
        "### Word By Word Meaning",
        "",
    ]
    for w in analysis["word_by_word"]:
        lines.append(
            f"- {w['translit']} - {w['devanagari']} - {w['kannada']} = {w['meaning']}"
        )
    lines += [
        "",
        "### Meaning",
        "",
        analysis["meaning_en"],
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> None:
    args = [a for a in sys.argv[1:]]
    output_path = None
    if "--output" in args:
        i = args.index("--output")
        output_path = args[i + 1]
        del args[i : i + 2]
    shloka = " ".join(args) if args else DEFAULT_SHLOKA

    # 1. Scholarly analysis from the chat model
    print("Analysing the shloka with the chat model...")
    analysis = chat_json(shloka)

    # 2. Script conversions from the transliteration API.
    #    Kannada needs a two-hop route (Devanagari -> Roman -> Kannada)
    #    because the API rejects Indic-Indic transliteration; the model's
    #    own Kannada rendering (kannada_shloka) is the fallback.
    kannada_shloka = transliterate_to_kannada(
        shloka,
        fallback=analysis.get("kannada_shloka") or analysis["padaccheda_kn"],
    )
    roman_shloka = safe_transliterate(shloka, "en-IN", fallback=analysis["iast"])

    # 3. Render and deliver
    guide = render_markdown(shloka, kannada_shloka, roman_shloka, analysis)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(guide)
        print(f"Study guide written to {output_path}")
    else:
        print(guide)


if __name__ == "__main__":
    main()
