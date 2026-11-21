# Translating JARVIS 🌍

Translations are one of the easiest and most valuable ways to contribute, and
**native-speaker help is very welcome** — you don't need to touch any Python or
JavaScript. English is always the source language and the fallback, so a partial
translation never breaks anything: whatever you don't translate simply stays in
English.

There are two places JARVIS speaks to you, each translated a little differently.

## 1. Panel UI strings — `frontend/i18n/<lang>.json`

One JSON file per language, keyed by the **exact English string** shown in the
panel:

```json
{
  "3D MODEL": "3D-MODELL",
  "ADDRESS NOT SET": "ADRESSE NICHT GESETZT"
}
```

The panel loads the file matching your Home Assistant language automatically
(region variants like `pt-br.json` fall back to `pt.json`). Any string without
an entry stays English.

**To extend an existing language:** open its file and translate any values still
in English.

**To add a new language:** copy the file closest to yours (e.g. `de.json`),
rename it to your language code (`sv.json`, `zh-tw.json`, …), and translate the
values. **Keep the keys (the English side) exactly as they are** — the key is how
the panel finds your translation.

## 2. Safety & security notifications — `notify_i18n.py`

Freeze, intrusion and lockdown alerts are generated **without** the LLM so they
stay reliable, which means they carry their own translations in
`custom_components/jarvis/notify_i18n.py`. To add a language, add its entry to
**every** `TITLES` and `MESSAGES` table and to the `_AND` conjunction map. A test
enforces that a language, once added, covers every key — a half-translated
language would otherwise emit English mid-sentence.

## The only rules

- **Never translate anything in `{curly_braces}`** — `{honorific}`, `{names}`,
  `{reading}`, `{count}`, etc. are placeholders filled in at runtime. Keep them
  exactly, in a spot where they read naturally in your language.
- **Never translate technical values** — entity IDs, model names, numbers.
- For the panel, keep the English keys byte-for-byte identical.

## Check your work

```bash
python3 scripts/i18n_coverage.py          # coverage report for both systems
python3 -m pytest tests/unit/test_notify_i18n.py -q   # notification tables
```

The coverage script shows how complete each language is and, for notifications,
which languages are still welcome. Then open a pull request — thank you! 🙏
