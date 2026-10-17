# ವಾಲ್ಮೀಕಿ ರಾಮಾಯಣ — Sanskrit & Kannada Study Guide

**Vālmīki Rāmāyaṇa** in Sanskrit, with a Kannada study guide for every śloka.

This repository holds one entry per verse — a Sanskrit *saṃhitā* (the verse as it is
chanted), its *padaccheda* (word-split), *padārtha* (word-by-word meaning), *anvaya*
(prose order) and *bhāvārtha* (purport), the same in Kannada, and an English
transliteration with word-by-word gloss. Each verse is stored twice:

- a human-readable **Markdown** file you can read and copy from any editor, and
- a machine-readable **JSON** file with the same content in a structured form.

Entries are named `shloka-<kanda>-<sarga>-<shloka>.md`, so `shloka-1-1-2.md` is
Bāla Kāṇḍa, Sarga 1, Śloka 2.

It is built on two external services — the **IIT Kanpur Heritage Scripture Server**
for the verse text and **Sarvam AI** for the analysis. Both are documented in
[Data sources and APIs](#data-sources-and-apis).

---

## Repository layout

```
valmiki-ramayana-kannada/
├── data/
│   └── balakanda/
│       └── sarga-1/
│           ├── shloka-1-1-1.md      # the study guide (human-readable)
│           └── shloka-1-1-1.json    # the same content, structured
├── scripts/
│   ├── shloka_fetch_iitk.py         # fetch the verse  -> input .md
│   ├── shloka_study_guide.py        # generate the guide
│   └── shloka_md_to_json.py         # .md -> .json
├── template/
│   └── shloka-template.md           # blank entry, shows the exact section order
├── LICENSE
└── README.md
```

---

## Anatomy of an entry

Every Markdown file opens with YAML front matter and then three language sections.

```markdown
---
ಕಾಂಡ: ಬಾಲ ಕಾಂಡ
ಸರ್ಗ: ೧
ಶ್ಲೋಕ: ೧
tags:
  - balakanda
  - sarga-1
  - shloka
sanskrit-scholar-verified:
sanskrit-scholar-verified-by:
sanskrit-scholar-verified-on:
kannada-scholar-verified:
kannada-scholar-verified-by:
kannada-scholar-verified-on:
---
## संस्कृत (Sanskrit)
### संहिता      the verse
### पदच्छेद     word-split
### पदार्थ      word-by-word meaning (bulleted)
### अन्वया      the verse in prose order
### भावार्थ     purport

## ಕನ್ನಡ (Kannada)
### ಸಂಹಿತೆ
### ಪದಚ್ಛೇದ
### ಪದಾರ್ಥ
### ಅನ್ವಯ
### ಸಾರಾಂಶ

## English
### (IAST transliteration, plain-Roman transliteration,
###  Word By Word Meaning, Meaning)
```

Verse and prose blocks are wrapped in fenced code blocks (triple backticks) so they
copy cleanly from a Markdown editor; the bulleted lists are left as lists.

### Front matter fields

| Field | Meaning |
|---|---|
| `ಕಾಂಡ` | kāṇḍa (book), in Kannada |
| `ಸರ್ಗ` | sarga (chapter), in Kannada numerals |
| `ಶ್ಲೋಕ` | śloka (verse), in Kannada numerals |
| `tags` | index tags, e.g. `balakanda`, `sarga-1`, `shloka` |
| `sanskrit-scholar-verified` | `true` / `false` — has a Sanskrit scholar reviewed this entry |
| `sanskrit-scholar-verified-by` | who verified it |
| `sanskrit-scholar-verified-on` | when they verified it (a date) |
| `kannada-scholar-verified` | `true` / `false` — has a Kannada scholar reviewed this entry |
| `kannada-scholar-verified-by` | who verified it |
| `kannada-scholar-verified-on` | when they verified it (a date) |

Verification is a human act, so the generators never presume a value: all six fields
are blank until someone fills them in. **Regenerating an entry resets them**, because
new content has to be re-verified — the script warns when it clears fields that had
been set. To record a verification, set the fields yourself (or pass the six
`--sanskrit-verified*` / `--kannada-verified*` flags) *after* the final regeneration.

Example of a verified entry:

```yaml
sanskrit-scholar-verified: true
sanskrit-scholar-verified-by: Dr. Rao
sanskrit-scholar-verified-on: 2026-10-17
kannada-scholar-verified: true
kannada-scholar-verified-by: Prof. Hegde
kannada-scholar-verified-on: 2026-10-18
```

---

## JSON schema

The JSON file mirrors the Markdown exactly.

```jsonc
{
  "kanda": "ಬಾಲ ಕಾಂಡ",
  "sarga": "೧",
  "shloka_no": "೧",
  "tags": ["balakanda", "sarga-1", "shloka"],
  "sanskrit_scholar_verified": false,
  "sanskrit_scholar_verified_by": "",
  "sanskrit_scholar_verified_on": "",
  "kannada_scholar_verified": false,
  "kannada_scholar_verified_by": "",
  "kannada_scholar_verified_on": "",
  "sanskrit": {
    "samhita": "…",             // verse
    "padaccheda": "…",          // word-split
    "padartha": [ { "word": "तपः", "gloss": "तपस् (शरीर-वाङ्-मनःसु संयमः)" } ],
    "anvaya": "…",
    "bhavartha": "…"
  },
  "kannada": {
    "samhite": "…",
    "padaccheda": "…",
    "padartha": [ { "word": "ತಪಃ", "gloss": "ತಪಸ್ಸು" } ],
    "anvaya": "…",
    "saransha": "…"
  },
  "english": {
    "iast": "…",                // transliteration with diacritics
    "roman": "…",               // plain ASCII Roman
    "word_by_word": [
      { "translit": "tapah", "devanagari": "तपः", "kannada": "ತಪಃ", "meaning": "austerity" }
    ],
    "meaning": "…"
  },
  "kanda_number": 1,            // parsed from the file name
  "sarga_number": 1,
  "shloka_number": 1,
  "source_file": "shloka-1-1-1.md"
}
```

---

## The pipeline

Three scripts, one job each.

### 1. `shloka_fetch_iitk.py` — fetch the verse

Fetches the Sanskrit *mūlam* from the **IIT Kanpur Heritage Scripture Server**
(`https://heritage.iitk.ac.in/api/v1/`, text code `VRAM`) and writes the input
Markdown — front matter plus the verse under `### संहिता`, everything else empty.

```bash
export HERITAGE_API_KEY="your-key"
python scripts/shloka_fetch_iitk.py data/balakanda/sarga-1/shloka-1-1-3.md
```

The kāṇḍa / sarga / śloka numbers are read from the file name; you can also pass
`--kanda 1 --sarga 1 --shloka 3`. Useful flags: `--check` (verify your key),
`--debug` (dump the raw API response), `--selftest` (offline), `--stdout`.

### 2. `shloka_study_guide.py` — generate the guide

Reads the verse out of the input file, generates the analysis with the **Sarvam AI**
chat model, and **rewrites that same file in place**.

```bash
export SARVAM_API_KEY="your-key"
python scripts/shloka_study_guide.py --input data/balakanda/sarga-1/shloka-1-1-3.md
```

Design notes worth knowing:

- The model supplies **content only**. Every script conversion — Devanagari → Kannada,
  Devanagari → IAST, IAST → plain Roman — is computed **locally** by deterministic
  Unicode maps, so the Kannada and transliteration lines cannot drift.
- The analysis is split into **four small calls** (word-split, Sanskrit prose, Kannada
  prose, English meaning), because a small single-purpose reply is far easier for a
  model to keep valid than one large one.
- Replies are parsed defensively; malformed JSON is repaired (unescaped quotes, raw
  newlines, trailing commas, smart quotes) before failing.

### 3. `shloka_md_to_json.py` — convert to JSON

```bash
python scripts/shloka_md_to_json.py data/balakanda/sarga-1/shloka-1-1-1.md
# -> data/balakanda/sarga-1/shloka-1-1-1.json
```

Needs no API key. It warns about any empty sections, which doubles as a
completeness check.

---

## Setup

Requires **Python 3.10+**.

```bash
git clone https://github.com/harsha-kadekar/valmiki-ramayana-kannada.git
cd valmiki-ramayana-kannada
pip install requests
```

Two API keys, both free to obtain:

| Key | Used by | Where to get it |
|---|---|---|
| `HERITAGE_API_KEY` | `shloka_fetch_iitk.py` | https://heritage.iitk.ac.in/api-access (50 units/day free) |
| `SARVAM_API_KEY` | `shloka_study_guide.py` | https://dashboard.sarvam.ai/ |

Neither key is ever written into a file; both are read from the environment.

### Full run for one verse

```bash
export HERITAGE_API_KEY="…" SARVAM_API_KEY="…"

python scripts/shloka_fetch_iitk.py       data/balakanda/sarga-1/shloka-1-1-3.md
python scripts/shloka_study_guide.py --input data/balakanda/sarga-1/shloka-1-1-3.md
python scripts/shloka_md_to_json.py       data/balakanda/sarga-1/shloka-1-1-3.md
```

---

## The seven kāṇḍas

| # | Kannada | Slug |
|---|---|---|
| 1 | ಬಾಲ ಕಾಂಡ | `balakanda` |
| 2 | ಅಯೋಧ್ಯ ಕಾಂಡ | `ayodhyakanda` |
| 3 | ಅರಣ್ಯ ಕಾಂಡ | `aranyakanda` |
| 4 | ಕಿಷ್ಕಿಂಧಾ ಕಾಂಡ | `kishkindhakanda` |
| 5 | ಸುಂದರ ಕಾಂಡ | `sundarakanda` |
| 6 | ಯುದ್ಧ ಕಾಂಡ | `yuddhakanda` |
| 7 | ಉತ್ತರ ಕಾಂಡ | `uttarakanda` |

Entries live at `data/<slug>/sarga-<n>/shloka-<kanda>-<sarga>-<shloka>.md`.

---

## Data sources and APIs

This project is a thin pipeline over two external services. Neither is bundled; both
are called at build time, and both need a key.

### 1. Verse text — IIT Kanpur, *Heritage Scripture Server*

| | |
|---|---|
| Provider | Indian Institute of Technology Kanpur — Heritage Scripture Server |
| Website | https://heritage.iitk.ac.in/ |
| API base | `https://heritage.iitk.ac.in/api/v1/` |
| Documentation | https://heritage.iitk.ac.in/docs |
| OpenAPI schema | https://heritage.iitk.ac.in/api/schema/ |
| Text code | `VRAM` — *Śrīmad Vālmīki Rāmāyaṇam* |
| Endpoint used | `GET /api/v1/VRAM/{kanda}/{sarga}/{shloka}/moolam/` |
| Returns | the *mūlam* (source verse) in Devanagari |
| Auth | `Authorization: Api-Key <key>` |
| Get a key | https://heritage.iitk.ac.in/api-access |
| Limits | 30 requests/min; free tier 50 units/day (a mūlam call costs 1 unit) |
| Licence | HCAL-1.1 — the Sanskrit mūlam is public domain; translations and commentaries remain copyrighted by their authors and publishers |

Used by [`scripts/shloka_fetch_iitk.py`](scripts/shloka_fetch_iitk.py), which fetches
only the **mūlam** — never the copyright-restricted translations or commentaries.

### 2. Analysis — Sarvam AI

| | |
|---|---|
| Provider | Sarvam AI |
| Website | https://www.sarvam.ai/ |
| Documentation | https://docs.sarvam.ai/ |
| API base | `https://api.sarvam.ai` |
| Endpoint used | `POST /v1/chat/completions` (OpenAI-compatible) |
| Model | `sarvam-105b` |
| Auth | `Authorization: Bearer <key>` |
| Get a key | https://dashboard.sarvam.ai/ |

Used by [`scripts/shloka_study_guide.py`](scripts/shloka_study_guide.py) to draft the
padaccheda, padārtha, anvaya and bhāvārtha (in Sanskrit and Kannada) and the English
meaning. Only the **content** comes from the model; every script conversion is done
locally (see [The pipeline](#the-pipeline)).

[`scripts/shloka_md_to_json.py`](scripts/shloka_md_to_json.py) calls no external
service.

---

## Editions

- The verse text follows the **Southern recension** (via IIT Kanpur). The Baroda
  **Critical Edition** — used by GRETIL and Sanskrit Documents — numbers and reads some
  verses differently: this project's `1-1-1` is `तपःस्वाध्यायनिरतं…`, whereas a
  Southern-recension text may write `तपस्स्वाध्यायनिरतं…`. An entry's number is only
  meaningful together with the edition it came from.
- The analysis is a machine-generated **first draft**, then subject to human review.

---

## Review workflow

Machine-generated analysis is a **first draft**. To verify an entry:

1. Read the Markdown file and correct anything wrong — Sanskrit grammar, Kannada
   spelling, the purport.
2. Set the verification fields in the front matter to record who reviewed it and
   when — the flag, the reviewer, and the date:

   ```yaml
   sanskrit-scholar-verified: true
   sanskrit-scholar-verified-by: Dr. Rao
   sanskrit-scholar-verified-on: 2026-10-17
   kannada-scholar-verified: true
   kannada-scholar-verified-by: Prof. Hegde
   kannada-scholar-verified-on: 2026-10-18
   ```

3. Regenerate the JSON: `python scripts/shloka_md_to_json.py <file>.md`.

Order matters: regenerating the entry resets those flags, so always **verify last**.
If you re-run `shloka_study_guide.py` after verifying, the flags are cleared (with a
warning) and the entry needs reviewing again. It also rewrites the whole file, so any
hand-edits you made are lost too — make them only after the final regeneration.

---

## Credits

- Verse text: [IIT Kanpur Heritage Scripture Server](https://heritage.iitk.ac.in/) —
  Śrīmad Vālmīki Rāmāyaṇam (`VRAM`).
- Analysis: [Sarvam AI](https://www.sarvam.ai/) (`sarvam-105b`).
- This project is an independent work and is not affiliated with either provider.

---

## Licensing

- **Sanskrit source verses** are public domain.
- **Translations and commentaries** on the Heritage platform are copyrighted by their
  authors and publishers; this project fetches only the mūlam.
- Everything in this repository is released under the **MIT License** — see
  [`LICENSE`](LICENSE).

---

## Contributing

Corrections are the most valuable contribution. If a verse's word-split, grammar or
Kannada rendering is wrong, open an issue or a pull request against the Markdown file
(and regenerate its JSON). Please say which edition your correction follows.

---

## Status

Work in progress, starting from Bāla Kāṇḍa, Sarga 1. See `data/` for what exists.
