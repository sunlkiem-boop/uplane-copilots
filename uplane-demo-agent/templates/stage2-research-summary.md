<!--
TEMPLATE — Stage 2: Research summary (Demo Agent). Fixed template: same sections, same
order, every run (SPEC.md §5 stage 2). This is the ONE-PAGE summary Sun reads and signs,
organized by segment x funnel; canon_check stays the internal gate. NOT a scorecard.
HARD TEXT BUDGET: 600 words outside tables and TRACE (house-rules/rules.csv RULE-22).
BLANK SCAFFOLD: fill every <placeholder>. Never invent client data — "not yet derived"
is a normal state and gets a research-pass proposal, never a guess.
TRACE stays at the TOP (house-rules/rules.csv RULE-50).
Funnel shorthand, glossed once: TOF/MOF/BOF = top/middle/bottom of funnel
(awareness / consideration / purchase).
-->

```
GATE  — stage <N>: <GREEN | YELLOW (warnings) | RED ⚠ OVERRIDDEN — what was missing · why>
TRACE — stage 2 · <client>
- read: <canon docs, KB, scrape reports actually read>
- checks: <canon_check --stage 2 result; per-doc GREEN/YELLOW/RED>
- called: <skills invoked, e.g. brand-research-sprint, scrape-meta-ads, brain-distill>
- chose: <decisions made + why, summarized>
- open: <unresolved items or questions>
```

# Stage 2 — Research summary · <client>

Date: <YYYY-MM-DD> · Scope: <market.home / competitor_scope from scope.yaml>

## 1. What the brand is (facts)

| fact | value | source |
|---|---|---|
| geography | <home market + operating markets> | <source> |
| business model | <business_model from scope.yaml> | <source> |
| what it sells | <products/services, one line> | <source> |
| in-scope products | <slug list> | scope.yaml |
| price position | <e.g. premium / value / unknown> | <source> |

## 2. USPs (from research/usp-inventory.md — ids are append-only)

| id | USP (one line, plain words) | evidence source | confidence |
|---|---|---|---|
| USP-01 | <claim> | <where it is proven> | <verified / client-stated / inferred> |

## 3. ICPs derived so far (ICP = ideal customer profile; SEG ids from research/avatars/)

"not yet derived" is a normal state: the row then names the research pass that would fill
it plus its cost estimate — never an invented profile.

| id | who (one line) | share of documented volume | primary barrier | source | state |
|---|---|---|---|---|---|
| SEG-A | <profile> | <% or unknown> | <barrier> | <source> | <derived / not yet derived → proposed pass + cost> |

## 4. Competitor field by tier (from research/market-map.md)

Tiers: T1 = same business model, same geo (direct) · T2 = value-chain adjacent (captures
the same end user) · T3 = geographic expansion candidates.

| tier | competitor | why it qualifies (model + geo, not vibes) | segments contested |
|---|---|---|---|
| T1 | <name> | <reason> | <SEG ids> |
| T2 | <name> | <reason> | <SEG ids> |
| T3 | <name> | <reason> | <SEG ids> |

## 5. Ad landscape — saturated vs open, per segment x funnel

From competitor-ad tagging against `uplane/creative-dimensions.yaml`. Each cell:
`saturated: <dominant message/format + share-of-voice leader>` or
`open: <unoccupied message/format lane>`.

| segment | TOF (awareness) | MOF (consideration) | BOF (purchase) |
|---|---|---|---|
| SEG-A | <cell> | <cell> | <cell> |
| SEG-B | <cell> | <cell> | <cell> |

## 6. Open items (blocking questions)

Each self-contained, with a recommendation (house-rules/rules.csv RULE-30/31).

1. <question — recommendation — consequence per option>

## Done when (all must hold before the gate closes)

- [ ] Canon GREEN for this client's stage-2 required_docs (per scope.yaml), incl. brand-brain distillates
- [ ] Every SEG row is either derived or carries its research-pass proposal
- [ ] Sign-off recorded — reply exactly: `approve stage 2` (or `edit 2: <text>`)
