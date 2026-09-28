<!--
TEMPLATE — Stage 1: Brand assets (Demo Agent). Fixed template: same sections, same order,
every run (SPEC.md §3.2, §5 stage 1; rules in uplane/house-rules/rules.csv, AS-nn rows).
HARD TEXT BUDGET: 250 words outside tables and TRACE (house-rules/rules.csv RULE-22).
BLANK SCAFFOLD: fill every <placeholder>. Never invent client data — an asset nobody
verified gets a status and a note, not a guess.
TRACE stays at the TOP (house-rules/rules.csv RULE-50).
-->

```
GATE  — stage <N>: <GREEN | YELLOW (warnings) | RED ⚠ OVERRIDDEN — what was missing · why>
TRACE — stage 1 · <client>
- read: <brand.json files, brand-assets.json, dirs actually inspected>
- checks: <mechanical checks run (AS-nn ids) + results>
- called: <skills/tools invoked, e.g. asset-hunt, product_images.py>
- chose: <decisions made + why, summarized>
- open: <unresolved items or questions>
```

# Stage 1 — Brand assets · <client>

Date: <YYYY-MM-DD> · In-scope products: <product-slug list>

## 1. Status ledger — every key carries exactly one status

Statuses (rule: house-rules/rules.csv AS-41; enum: uplane/asset-standards.md §5): `on-disk` (exists, passes checks) ·
`found-on-web` (source located, not fetched — needs your yes) · `owed-by-client` (asked,
not delivered — ask date recorded) · `empty-by-necessity` (documented as nonexistent).
Ledger is written to `brand-kit/brand-assets.json` under `_asset_status` on sign-off.

| key | status | location / source | note (verification result, ask date, URL) |
|---|---|---|---|
| logo-pair | <status> | <path or URL> | <note> |
| packshots/<product-slug> | <status> | <path or URL> | <note> |
| labels-hex | <status> | <path or URL> | <note> |
| fonts | <status> | <path or URL> | <note> |
| site-aesthetic | <status> | <path or URL> | <note> |

## 2. Logo pair — mechanical checks (house-rules/rules.csv AS-01..AS-07)

| check | rule | result |
|---|---|---|
| pair at brand-kit/logo/ | AS-01 | <pass/fail + found-at path> |
| naming <client-slug>-logo-{light,dark}-bg.png | AS-02 | <pass/fail> |
| PNG with real alpha channel | AS-03 | <pass/fail> |
| transparency verified via alpha extrema | AS-04 | <pass/fail + measured value> |
| ≥ 1000 px longest side | AS-05 | <pass/fail + dimensions> |
| no partner/third-party logos in logo folder | AS-06 | <pass/fail + offending files> |
| duplicates byte-identical to canonical | AS-07 | <pass/fail + copies found> |

## 3. Packshot registration (house-rules/rules.csv AS-10..AS-12)

The gate sees only images registered in `brand.json` — unregistered files on disk are
invisible to every downstream build.

| product | registered keys in brand.json | on-disk but UNREGISTERED | action |
|---|---|---|---|
| <product-slug> | <keys or none> | <files or none> | <register / status / none> |

## 4. Web-hunt proposals (fetch only after your yes)

| key | proposed source (URL) | what it is | risk / licence note |
|---|---|---|---|
| <key> | <URL> | <description> | <note> |

## 5. Thumbnails

Thumbnails card attached in this DM. Files at `clients/<client>/brand-kit/<...>`.

## Done when (all must hold before the gate closes)

- [ ] Zero keys without a status
- [ ] Every `on-disk` key passes its mechanical checks
- [ ] Every `owed-by-client` key mirrored to `_inbox/OPEN-ITEMS.md` with ask date (AS-43)
- [ ] Ledger written to `brand-assets.json` `_asset_status`
- [ ] Sign-off recorded — reply exactly: `approve stage 1` (or `edit 1: <text>`)
