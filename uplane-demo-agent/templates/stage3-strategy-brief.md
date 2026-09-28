<!--
TEMPLATE, Stage 3: Strategy brief (Demo Agent). Fixed template: same sections, same
order, every run (SPEC.md section 5, stage 3). One page: counts first, then the concept
table. Rendered as a card and mirrored to strategy.yaml.
HARD TEXT BUDGET: 250 words outside tables and TRACE (house-rules/rules.csv RULE-22).
BLANK SCAFFOLD: fill every <placeholder>. Never invent client data, a concept without
canon evidence behind its hypothesis does not go in the table.
TRACE stays at the TOP (house-rules/rules.csv RULE-50).
Funnel shorthand, glossed once: TOF/MOF/BOF = top/middle/bottom of funnel.
2026-09-15: strategy decides tier, moment, the argument and the hypothesis; it never
decides the format, the words or the image (format follows from the argument's type at
concept stage, words belong to the copywriter, the image to the render brief). The
concept table below carries the eight strategy.yaml row fields (id, product, tier,
moment, message basis, hypothesis, destination, status), each tagged FIXED, DEFAULT or
OPEN (house-rules/rules.csv RULE-70: a later stage may not change a FIXED field or leave
an OPEN one undecided). format, ratio, text load, elements, CTA, hook,
compliance_precheck and cold_read are retired from this table: format/ratio/text
load/elements are concept and render decisions made downstream (static-prompt-gen), CTA
and hook are words (the copywriter, all seven platform fields), and compliance_precheck/
cold_read are TRACE lines from the checks already run in step 5, not columns a reader has
to parse here.
No em dashes and no middle dot separators anywhere in the brief (house-rules/rules.csv
RULE-66).
-->

```
GATE  stage <N>: <GREEN | YELLOW (warnings) | RED, OVERRIDDEN: what was missing, why>
TRACE, stage 3, <client>
- read: <canon docs, tag queries, learnings actually read>
- checks: <canon_check --stage 3 result; compliance pre-pass; cold-read pass>
- called: <demo-strategy fishbone branches taken; message-select rankings>
- chose: <decisions made and why, summarized>
- open: <unresolved items or questions>
```

# Stage 3, Strategy brief, <client>

Date: <YYYY-MM-DD>. Products: <slug list>. Basis: stage-2 summary signed <YYYY-MM-DD>.

## 1. Counts

| total statics | TOF (top of funnel) | MOF (middle) | BOF (bottom) |
|---|---|---|---|
| <n> | <n> | <n> | <n> |

## 2. Concept table

One row per ad concept. One ad, one job: each row carries exactly ONE segment x ONE
moment from the reader's life x ONE message basis (a single USP-nn or BELIEF-nn), picked
by `message-select`, ranking logged in TRACE. Exclusions are recorded choices, not
oversights.

Column gloss (contractor-readable):
`id` C-01 and so on. `segment` SEG id, who this ad targets. `funnel` TOF/MOF/BOF.
`tier` category (the general kind of thing, named without the procedure) or procedure
(named), tagged FIXED once approved. `moment` one line, a scene from the reader's own
life the concept meets them in, not a theme and not an audience, tagged FIXED. For a business
reader it is a STANDING STATE of their company that is true on any working day, never a
scheduled event or a rare conversation (Sun, 2026-09-23: the ad will not arrive at the right time). `message
(USP-nn)` the single USP-nn or BELIEF-nn this ad carries (resolves via canon_reader),
tagged FIXED. `argument` the one sentence this ad argues, in plain English, tagged FIXED:
this is what the headline will be made from, so write the thing the reader gets and never
a fact about our own paperwork or process. `hypothesis` MANDATORY, now a footnote beneath
the table: an if-then-because statement, "IF we meet <segment> in <this moment> with <this
argument> THEN <expected response> BECAUSE <canon evidence>," the BECAUSE naming real
evidence (a VOC count, a saturation query, a learnings row), tagged FIXED. `destination`
where the ad sends people and why for this
funnel job, tagged DEFAULT (see section 3 for the full table). `excluded (-> covered by)`
what was deliberately left out and which concept covers it, or "none." `status` pending,
approved, killed or edited.

Format, ratio, text load, elements, CTA and hook are not columns here: they are
concept-stage and copywriter decisions, made only after this brief is approved
(static-prompt-gen reads the moment and the argument's type from this row and picks the
shape that follows from it; the copywriter writes the words from the moment). A run
asked to put any of them back on this row refuses and points here.

| id | segment | funnel | tier | moment | argument | message (USP-nn) | excluded (-> covered by) | status |
|---|---|---|---|---|---|---|---|---|
| C-01 | <SEG-x> | <TOF/MOF/BOF> | <category / procedure (<name>)> | <a scene from the reader's life> | <the one sentence this ad argues> | <USP-nn or BELIEF-nn> | <what -> C-nn / none> | pending |

The `argument` column was added on 2026-09-19 and the `hypothesis` moved to a numbered
footnote beneath the table. The reason is on the record: the argument is the sentence the
delivered headline becomes, in eighteen of nineteen cases word for word, and it was the one
field this table never showed. Twenty-five rows were approved on 2026-09-18 without those
twenty-five sentences ever appearing as sentences, and four of them turned out to say
nothing. Read this column down the page before approving anything: two rows that teach the
reader the same thing are the same ad twice, however different their moments.

Hypotheses, one numbered footnote per row, in the same order (the full if-then-because with
its evidence; it stays mandatory and stays FIXED, it just stops crowding the table):

1. C-01: IF we meet <segment> in <this moment> with <this argument> THEN <expected response>
   BECAUSE <canon evidence>.

<!-- RELAYING THIS TABLE (chat, Slack, anywhere): carry the FULL core column set every
     time: id, segment, funnel, tier, moment, message (USP-nn), hypothesis. Do NOT drop
     columns to save width; manage width by keeping cells terse. Dropping a column
     silently removes a decision step from review, and every column here IS a decision:
     who it targets, where in the funnel, what kind of argument, which moment, which
     single USP, and why it holds. (Sun, 2026-08-19: "the agent should not skip any
     steps.") excluded and status may live in the file alone when the relay is a
     progress note rather than a request for a verdict, but never when asking for
     approval. -->

## 2b. Thin rows (printed before the verdict, added 2026-09-19)

Two free checks, no model call, run by `strategy_lint.py` over this table. Both compare two
fields of the same row against each other, so neither is a judgement and neither can ask
for a rewrite. A flagged row's only outcomes are `approve <id>` anyway or `kill <id>`.

| row | flag | what the file itself says |
|---|---|---|
| <C-nn> | <bars its own subject / says what a sibling already says> | <the row's own words> |

`bars its own subject`: the row's exclusion list rules out the substance of the argument it
was assigned, and the reason it gives is that the material is unavailable or would misstate
something, rather than that a lawyer barred it or the client has not answered yet. This
fired on exactly one row of twenty-five on 2026-09-18 and it was the ad Sun read as "doesn't
make sense at all". Exclusions for legal reasons, for a pending client answer, or for a
format's own limits are NOT flagged: they are decisions, not emptiness.

`says what a sibling already says`: the three most similar pairs of arguments carrying the
same message basis, printed as a ranking with no threshold and no verdict. A ranking cannot
raise a false alarm, because it asserts nothing. On 2026-09-18 the top pair was two rows
both saying that the instalments carry no interest, which is why a writer three rows later
reached for a picture to fill a headline.

If a kill leaves the batch short, nothing backfills. The honest output is a shorter table
plus a named ask, in Open items, addressed to the client or to Sun.

## 3. Destinations (where each concept sends people)

| concept | destination / connection | note |
|---|---|---|
| C-01 | <LP / quiz / PDP / article> | <why this destination for this funnel stage> |

## 4. Open items

1. <question, recommendation, consequence per option>

## Sign-off (typed, per item)

Reply exactly: `approve <ids>` (for example `approve 1,3-5`), `kill <id>`, `edit <id>:
<text>` (an edit resets that concept to pending).

## Done when (all must hold before the gate closes)

- [ ] Zero concepts with status `pending`
- [ ] At least one concept `approved`
- [ ] Every concept: tier, moment, message basis and hypothesis tagged and filled,
      hypothesis cites canon evidence
- [ ] strategy.yaml written; verdicts persisted to data/state/<client>.json
