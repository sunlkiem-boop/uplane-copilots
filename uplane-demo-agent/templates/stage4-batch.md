<!--
TEMPLATE — Stage 4 Part 2: Prompt batch (Demo Agent). Fixed template: same sections, same
order, every run (SPEC.md §5 stage 4 part 2, §8). One row per generated prompt file; the
full per-check verdict block lives INSIDE each prompt .md — this card summarizes it.
HARD TEXT BUDGET: 200 words outside tables and TRACE (house-rules/rules.csv RULE-22).
BLANK SCAFFOLD: fill every <placeholder>. Never invent client data.
creative_id = <client>-<batch>-<NN>; it is also the prompt filename, the front-matter id,
and (by convention only — ad naming in Ads Manager is NOT controllable) the suggested ad
name.
TRACE stays at the TOP (house-rules/rules.csv RULE-50).
-->

```
GATE  — stage <N>: <GREEN | YELLOW (warnings) | RED ⚠ OVERRIDDEN — what was missing · why>
TRACE — stage 4 · prompt batch · <client>
- read: <distillates, signed copy deck, brand gate inputs, learnings read>
- checks: <verification chain results: brand gate, fidelity, plausibility, contradiction, length>
- called: <skills invoked, e.g. static-prompt-gen (prompt half), <client> brand gate>
- chose: <decisions made + why, summarized>
- open: <unresolved items or questions>
```

# Stage 4 · Part 2 — Prompt batch · <client> · batch <batch-id>

Date: <YYYY-MM-DD> · Basis: copy deck signed <YYYY-MM-DD> · Prompts: <n>

## Batch facts

| field | value |
|---|---|
| batch id | <batch-id> |
| prompt count | <n> |
| source copy deck | <path>, signed <YYYY-MM-DD> |
| manifest rows appended | <path to manifest + row count> |
| learnings row tagged with this batch id | <learning id / pending> |

## Prompts

Verdict block summary = the five checks from the verification chain, each `pass` or
`flagged`: brand gate · fidelity clause (reference-image products) · plausibility ·
contradiction audit · length cap. Full verdict block inside each prompt .md.

| creative_id | concept | verdict block summary (gate · fidelity · plausibility · contradiction · length) | file path |
|---|---|---|---|
| <client>-<batch>-01 | C-01 | <pass · pass · pass · pass · pass> | clients/<client>/creative/prompts/<creative_id>.md |

## Flagged prompts (reason + proposed fix, copy-pastable)

| creative_id | check | reason | proposed fix |
|---|---|---|---|
| <creative_id or none> | <which check> | <why it flagged> | <exact fix> |

## Open items

1. <question — recommendation — consequence per option>

## Done when (all must hold before the gate closes)

- [ ] Every prompt is all-pass or flagged-with-reason (no silent failures)
- [ ] Batch filed: prompt .md files + batch.json on disk at the paths above
- [ ] Manifest rows appended (creative_id chain per SPEC.md §8)
- [ ] At least one shared-learnings row tagged with this batch id
- [ ] Sign-off recorded — reply exactly: `approve stage 4` (or `edit 4: <text>`)
