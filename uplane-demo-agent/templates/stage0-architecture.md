<!--
TEMPLATE — Stage 0: Client install (Demo Agent). Fixed template: same sections, same
order, every run, so runs are comparable at a glance (SPEC.md §3.2, §5 stage 0).
HARD TEXT BUDGET: 400 words outside the tree block, tables, and TRACE
(house-rules/rules.csv RULE-22 enforces this).
BLANK SCAFFOLD: fill every <placeholder>. Never invent client data — anything unknown
stays as its placeholder and gets a row in Open items.
TRACE stays at the TOP (house-rules/rules.csv RULE-50).
-->

```
GATE  — stage <N>: <GREEN | YELLOW (warnings) | RED ⚠ OVERRIDDEN — what was missing · why>
TRACE — stage 0 · <client>
- read: <Notion pages / Slack channels / Drive docs / desktop paths actually read>
- checks: <gates run + results, e.g. canon_check --stage 0: GREEN|YELLOW|RED>
- called: <skills/tools invoked, e.g. intake-normalize, client-scaffold>
- chose: <decisions made + why, summarized>
- open: <unresolved items or questions>
```

# Stage 0 — Client install · <client>

Date: <YYYY-MM-DD> · Business model: <business_model> · Home market: <market.home>

## 1. Folder architecture (present / missing / misfiled)

Markers: `[present]` = exists and populated · `[missing]` = required, not there ·
`[misfiled -> path]` = content exists but at the wrong place, arrow names the correct home.

```
clients/<client>/
  README.md                      [<marker>]
  scope.yaml                     [<marker>]
  research/
    knowledge-base-<client>-<YYYYMMDD>.md   [<marker>]
    intake/                      [<marker>]
    site-snapshot/               [<marker>]
    avatars/                     [<marker>]
  offers/                        [<marker>]
  brand-brain/                   [<marker>]
  brand-kit/                     [<marker>]
  products/<slug>/               [<marker>]
  creative/                      [<marker>]
  performance/                   [<marker>]
  campaigns/                     [<marker>]
  _inbox/OPEN-ITEMS.md           [<marker>]
  _archive/                      [<marker>]
```

## 2. What was pulled, and where it was filed

One row per source item. Originals archived as received; a normalized, text-extractable
`.md` copy filed in `research/intake/` (the §4 intake rule: nothing stays locked in a
binary the readers cannot see).

| source (Notion page / Slack channel / Drive doc / desktop path) | what it is | filed to | normalized extraction | original archived at |
|---|---|---|---|---|
| <source> | <doc type, date> | <path> | research/intake/<file>.md | _archive/<path> |

## 3. scope.yaml summary (what stage 2 depends on)

| field | value | source |
|---|---|---|
| business_model | <e.g. broker / clinic / d2c-brand> | <where confirmed> |
| market.home | <country/region> | <source> |
| market.competitor_scope | <geo scope for competitor work> | <source> |
| access.ads_manager | <yes/no/pending> | <source> |
| access.ad_library | <yes/no/pending> | <source> |
| access.pixel | <yes/no/pending> | <source> |
| prompt_gate | <gate skill or generic fail-safe> | <source> |
| required_docs overrides | <none / list> | <source> |

## 4. Site snapshot

Captured <URL> on <YYYY-MM-DD> → `research/site-snapshot/` · aesthetic references →
`brand-kit/site-aesthetic/`.

## 5. Open items (blocking questions)

Each question is self-contained and carries a recommendation (house-rules/rules.csv RULE-30/31).

1. <question — recommendation — consequence per option>

## Done when (all must hold before the gate closes)

- [ ] Structural check GREEN (`canon_check <client> --stage 0`)
- [ ] Intake normalized: zero unreadable client docs (every original has a readable extraction in `research/intake/`)
- [ ] Site snapshot exists
- [ ] `scope.yaml` complete, including business_model
- [ ] Sign-off recorded — reply exactly: `approve stage 0` (or `edit 0: <text>`)
