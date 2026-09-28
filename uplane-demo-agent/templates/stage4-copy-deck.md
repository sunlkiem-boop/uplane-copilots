<!--
TEMPLATE, Stage 4 Part 1: Copy deck (Demo Agent). Fixed template: same sections, same
order, every run (SPEC.md section 5, stage 4 part 1). Copy is approvable BEFORE any
prompt exists; every deck is reviewed, Part 2 (prompt generation) never starts without
this deck's sign-off.
HARD TEXT BUDGET: 100 words of deck intro plus 60 words per concept block, both counted
outside the ad copy itself, tables, and TRACE (house-rules/rules.csv RULE-22).
BLANK SCAFFOLD: fill every <placeholder>. Never invent client data, copy uses only
approved claims; anything unverifiable is flagged, not shipped.
No em dashes in the ad copy fields (house-rules/rules.csv AS-75 clause 1;
RULE-12 was superseded by AS-75 on 2026-08-31). No em dashes and no middle dot
separators anywhere in the deck itself either (RULE-66).
2026-09-15: the operator card carries all seven platform fields, not three. Beneath
each concept's headline/subtext/button block, a Platform fields table carries
primary_text, meta_headline, meta_description and footnote, each with its own
presence-check verdict (house-rules/rules.csv RULE-70: every text field the generation
platform could otherwise fill is supplied by the pipeline, and a missing one fails the
deck closed rather than shipping a blank for the platform to fill on its own).
TRACE stays at the TOP (house-rules/rules.csv RULE-50).
-->

```
GATE  stage <N>: <GREEN | YELLOW (warnings) | RED, OVERRIDDEN: what was missing, why>
TRACE, stage 4, copy deck, <client>
- read: <distillates, approved-copy exemplars, learnings, compliance dials read>
- checks: <copy-logic + compliance gate + cold-read + house-style results per concept>
- called: <skills invoked, e.g. static-prompt-gen (copywriter half), message-select>
- chose: <decisions made and why, summarized>
- open: <unresolved items or questions>
```

# Stage 4, Part 1, Copy deck, <client>, batch <batch-id>

Date: <YYYY-MM-DD>. Basis: strategy brief signed <YYYY-MM-DD>, concepts <approved ids>.

## Deck summary

The argument column is the strategy row's own `argument` sentence, in English, set beside
the delivered line it became. Read the two columns against each other: on 2026-09-18
eighteen of nineteen delivered headlines were word-for-word translations of the argument,
and the ad Sun picked himself was the one that was not. A headline that only restates its
argument is the defect that produced "what does this even mean".

Give every ad a PLAIN NAME from its picture, and use that name everywhere a person
reads, including chat. This column was added 2026-09-21 because Sun asked, in as many
words, how he was supposed to use the code names: he is looking at nineteen images and
the review sheet offered him nineteen row ids. The name is what the picture shows, two
or three words, the way anyone would say it out loud: the swimmer, the drawer of old
glasses, the departure gate, the empty theatre. The id stays in the file for the tools.
It does not go in a sentence addressed to a person.

| ad | argument (English, from the strategy row) | headline (verbatim) | words on image / cap | status |
|---|---|---|---|---|
| <the swimmer> | <the strategy row's argument sentence> | <the delivered headline> | <n / cap> | pending |

## Per-concept copy

<!-- Repeat this block once per approved concept. -->

### <the swimmer>, <SEG-x>, <TOF/MOF/BOF>, message: <USP-nn>, concept <C-nn>

| field | text (verbatim, as it will appear on the ad) |
|---|---|
| headline | <headline> |
| subtext | <subtext> |
| button | <button text> OR no CTA (reason from strategy row: <reason>) |

Platform fields (typed into the ad platform's own boxes, shown around the image in
feed; never baked into the image itself, unlike headline, subtext and button above):

| field | text (verbatim) | presence check |
|---|---|---|
| primary_text | <primary text> | <present / missing> |
| meta_headline | <meta headline> | <present / missing> |
| meta_description | <meta description> | <present / missing> |
| footnote | <footnote, the batch invariant verbatim if one applies> | <present / missing> |

Extra on-image elements (only those the concept's `elements` line declares; each is on-image
copy and passes the same gates as the seven fields; a declared element without words fails closed):

| element | words (verbatim) | presence check |
|---|---|---|
| eyebrow | <words or "not declared"> | <present / missing / not declared> |
| chips, list lines, band lines, stamp | <words, one row each, or "not declared"> | <present / missing / not declared> |

NOTHING BELOW READS WHAT THE LINE MEANS. Added 2026-09-19 after the eye-general batch.
Every check in this table counts words, matches strings against a list, or looks a claim
up in the fact table. A line can pass all of them and still say nothing, and on
2026-09-18 four did. The deck used to print "all checks pass" here; that sentence is how
a meaningless line ships, so it is gone. The only meaning read on this page is the
writer's own, in the row beneath the table, and the only meaning verdict is Sun's.

| check | verdict |
|---|---|
| text budget | <pass / red + the issue sentence> |
| reader vocabulary | <pass / red + the term and the reader's word for it> |
| scope gate | <pass / blocking + the unlicensed claim> |
| house style | <pass / red + which clause and which line / unchecked + why it could not run> |
| model judges | <advisory since 2026-09-17, run or not run; they propose and never change a delivered line> |

The writer's own read (no model call; written while drafting, printed here because a read
nobody sees becomes a form: on 2026-09-18 nineteen of nineteen recorded the identical null
and the deck showed none of them):

| question | answer |
|---|---|
| what this ad is about, one sentence | <the writer's own sentence; if it does not name the thing being sold, the line is about nothing> |
| catchy score | <n of 4, naming the questions it FAILS: assumption overturned / object or moment from the reader's life / the specific that carries it / a second beat that adds a fact. Question 1 is mandatory in catchy mode and waived in factual mode (Sun, 2026-09-19)> |
| named defect this pass | <one sentence quoting the words that carry it, or "none found" plus what was checked> |

## Open items

1. <question, recommendation, consequence per option>

## Sign-off (typed, per item, Part 2 starts only after this deck is approved)

Reply exactly, one of: `approve <ids>`, `kill <id>`, or `edit <id>: <text>`. An edit
resets that concept's copy to pending and the deck re-renders.

A concept may also arrive here already marked `returned` (added 2026-09-19). That is the
writer refusing to write a concept rather than writing it badly: the argument cannot be
made into news for this reader, and no wording fixes that, so no more passes are spent on
it. A returned concept has no render brief and no image. It needs `kill` or `edit`, and an
edit goes back through the strategy stage, not back to the writer.

## Done when (all must hold before Part 2 starts)

- [ ] Every concept's copy carries all seven platform fields and their presence checks,
      plus the mechanical check verdicts and the writer's own read
- [ ] Zero copy blocks with status `pending` (`returned` is a finished state, not a pending one,
      and it blocks Part 2 for that concept until Sun types a verdict on it)
- [ ] Sign-off recorded to data/state/<client>.json
