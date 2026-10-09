# Authoring the curriculum

How to write and change the YAML files in `curriculum/`. For people and for AI assistants
drafting content: follow it exactly, the importer rejects anything else.

The schema is enforced by `backend/app/curriculum/schema.py`; the original design is in
`docs/design/M1.md` §1. If this guide and the schema ever disagree, the schema wins — fix the
guide.

## 1. Layout

```
curriculum/de/                 one folder per target language
├── lexicon/*.yaml             words; any file name, by convention one per level (a1.yaml, ...)
├── grammar/<slug>.yaml        one grammar point per file; file name = id without "gram:"
└── constructions.yaml         verb-preposition patterns and set phrases
```

Every file is UTF-8 YAML. Lexicon and constructions files are **lists**; a grammar file is a
**single mapping**.

## 2. The rules that matter most

1. **Ids are permanent.** An id is the key of the learner's whole history for that item. Never
   rename an id, never reuse a removed id for something else, never change what an id *means*
   (e.g. turn `lex:bank#money` into the bench). To change the meaning, add a new entry with a new
   id and delete the old one.
2. **Deleting an entry doesn't delete anything.** The importer marks a missing id as *suspended*:
   it stops being taught, the learner's history stays. Putting it back un-suspends it.
3. **Editing content is safe.** Fixing a translation, an example or a reference text updates the
   item in place on the next import; the learner's memory state is untouched.
4. **Accuracy first.** Learners memorize this. Check every gender, plural, auxiliary, participle,
   case and table. When unsure, leave the field out rather than guess.
5. **Original content only.** Choosing common words is fine; copying a published list or
   textbook (Goethe-Institut word lists, Profile Deutsch, …) is not.
6. **Learner-facing explanations are in Italian** (`*_it` fields), examples are German with an
   Italian translation. The YAML keys, ids and these docs are English.

## 3. Ids

| Kind | Pattern | Examples |
|---|---|---|
| word | `lex:<slug>` or `lex:<slug>#<sense>` | `lex:tisch`, `lex:maedchen`, `lex:bank#money`, `lex:bank#bench` |
| grammar point | `gram:<slug>` | `gram:adjective-endings` |
| construction | `cx:<slug>` | `cx:warten-auf-akk` |

Slugs: lowercase ASCII letters, digits and `-` only. Transliterate: `ä→ae`, `ö→oe`, `ü→ue`,
`ß→ss`. Use a `#sense` suffix only for homonyms that need separate items (different meaning,
gender or plural). Ids are unique across the whole language folder.

## 4. Words (`lexicon/*.yaml`)

```yaml
- id: lex:sonne
  lemma: Sonne                     # dictionary form, German spelling and capitalization
  pos: noun                        # noun verb adj adv prep conj pron det num particle phrase
  gender: f                        # nouns only: m | f | n
  plural: Sonnen                   # nouns only: full plural form; omit/null if not in common use
  level: A1                        # A1 A2 B1 B2 C1 C2
  translations:
    it: sole                       # main meaning first; alternatives comma-separated
    en: sun
  example:                         # strongly recommended: short, natural, at or below `level`
    de: Die Sonne scheint heute.
    it: Oggi splende il sole.
  interference:                    # optional, see below
    it_gender: m
    note_it: "In italiano è maschile (il sole), in tedesco femminile: die Sonne."
```

Field rules (enforced):
- `gender`, `plural`, `plural_only` are for **nouns only**. A noun needs `gender` unless it has
  `plural_only: true` (e.g. `Leute`, `Eltern`), in which case `gender` is null.
- `verb` is for **verbs only**: `aux: haben|sein`, `separable: true|false`, `irregular:
  true|false`, `praeteritum:` (3rd person singular, e.g. `ging`, `rief an`), `partizip:` (e.g.
  `gegangen`, `angerufen`). Fill all five for every verb. `aux: sein` for movement and change of
  state; when both are used (`fahren`, `ziehen`), give the most common one and explain in
  `interference.note_it`.
- `prep_case` is for **prepositions only**: `acc | dat | gen | acc_dat`.
- Reflexive verbs: lemma and id both include `sich` (`lemma: sich freuen`, `id:
  lex:sich-freuen`), as in the existing entries.
- No unknown keys: a typo in a key name is an error.

`interference` (how the app warns Italian speakers — fill it whenever it applies):
- `it_gender: m|f` — the gender of the main Italian translation. Set it for **every noun** whose
  Italian translation has a gender. When it differs from `gender`, the app prioritizes the word
  and shows the mismatch automatically ("In italiano è maschile, in tedesco femminile").
- `false_friend: {lang: en|it, word: <the misleading word>, note_it: <explanation>}` — e.g.
  `bekommen` vs English *become*, `Gift` vs *gift*, `kalt` vs Italian *caldo*.
- `note_it:` — any other short warning (e.g. *das Mädchen* is neuter).

`requires` (optional): ids that should be known before this word is taught. Rarely needed for
words.

## 5. Grammar points (`grammar/<slug>.yaml`)

```yaml
id: gram:adjective-endings        # must equal "gram:" + the file name without .yaml
title_it: "Declinazione dell'aggettivo (desinenze)"
title_en: "Adjective endings"
level: A2
requires: ["gram:articles-nom-akk", "gram:dative-case"]
diagnostic_tags:                  # how the grader classifies errors on this point
  case: [nom, akk, dat]
  gender_number: [m, f, n, pl]
  declension: [strong, weak, mixed]
reference_it: |
  ## Regola
  ...
  ## Esempi
  ...
  ## Errori tipici di chi parla italiano
  ...
examples:                          # at least 2 (aim for 3–5)
  - {de: "Das ist ein interessantes Buch.", it: "È un libro interessante."}
  - {de: "Ich kaufe den roten Pullover.", it: "Compro il maglione rosso."}
```

- **Granularity: one item per teachable point, fine detail in tags.** "Adjective endings" is one
  item; case × gender × declension are tags, not separate items. Split a point only if learners
  practise the parts at different times.
- **`diagnostic_tags`**: dimension → list of short lowercase values. The grader can only use
  these exact values, and the progress page ranks a learner's errors by them, so choose
  dimensions that describe *how* people get the point wrong. Use `{}` only when there's truly
  nothing to classify. Quote values that YAML would read as booleans (`"yes"`, `"no"`, `"true"`).
- **`reference_it`** is Markdown in Italian. It is shown to the learner *and* given to the LLM as
  the authority for explanations and grading, so it must be correct and self-contained:
  - `## Regola`: the rule, with Markdown tables where useful (declensions, conjugations);
  - `## Esempi`: 3–6 examples with translations;
  - `## Errori tipici di chi parla italiano`: interference from Italian (no cases, genders that
    differ, verb position, *avere/essere* vs *haben/sein*, …). Mark wrong forms with `*` and bold,
    e.g. `**\*Das Haus ist große**`.
  - Don't rely on concepts from points that aren't in `requires` (the app avoids explaining with
    concepts the learner hasn't met).
- **`requires`** builds the teaching order: a point is offered only after its prerequisites are
  known. Keep it acyclic and minimal (direct prerequisites only). Word order follows the
  acquisition sequence: `svo-word-order → adverb-fronting → verbal-bracket → inversion-v2 →
  verb-final-subordinate`.

## 6. Constructions (`constructions.yaml`)

```yaml
- id: cx:warten-auf-akk
  pattern: "warten auf + Akk"
  level: A1
  translations: {it: "aspettare (qualcuno/qualcosa)", en: "to wait for"}
  example: {de: "Ich warte auf den Bus.", it: "Aspetto l'autobus."}
  requires: ["gram:prepositions-akk"]
```

`pattern` shows the governed case (`+ Akk`, `+ Dat`, `jemandem …`). `example` is required.

## 7. YAML pitfalls

- Quote strings containing `:` followed by a space, `#`, or starting with `*`, `&`, `!`, `[`, `{`,
  `'`, `"`, `%`, `@` or a backtick — and every Italian string with an apostrophe inside single
  quotes (`'c''è'`), or simply use double quotes (`"c'è"`).
- `no`, `yes`, `on`, `off`, `true`, `false`, `null` and numbers with leading zeros are not
  strings unless quoted.
- Use `|` for multi-line Markdown (`reference_it`), keep indentation consistent, no tabs.

## 8. Checking and importing

```sh
scripts/import-curriculum.sh        # validates everything, then imports into the local DB
```

Errors are listed all at once as `file:index: message` (index = position in the list, 0 for a
grammar file), plus unknown prerequisites, duplicate ids and prerequisite cycles. The import is
idempotent and reports added / updated / unchanged / suspended counts — check that "suspended" is
what you intended.

The test suite also validates the real curriculum (`cd backend && uv run pytest -q
tests/test_curriculum_import.py -k real`), and so does CI. The Docker image imports the
curriculum on every start, so a deploy picks up changes automatically.

Before committing new content, also review it as a learner would: read each example aloud, check
each table cell, and make sure every word in an example is at or below the entry's level.

## 9. Checklist for an AI drafting content

- [ ] Ids follow §3, are new (grep the folder first) and never repurpose an existing one.
- [ ] Every noun has gender (or `plural_only`), plural, and `interference.it_gender`.
- [ ] Every verb has the full `verb` block; every preposition has `prep_case`.
- [ ] Every entry has an example within its level; translations in both `it` and `en`.
- [ ] Grammar: file name = slug, `reference_it` has the three sections, ≥ 3 examples,
      meaningful `diagnostic_tags`, `requires` only lists existing ids.
- [ ] Nothing copied from a published list or textbook.
- [ ] `scripts/import-curriculum.sh` passes; list anything you were unsure about for human review.
