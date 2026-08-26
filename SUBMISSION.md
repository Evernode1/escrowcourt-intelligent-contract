# EscrowCourt Deal — Intelligent Contract Submission

## Purpose

`Deal` is milestone-based freelance escrow arbitrated by GenLayer consensus. A buyer funds work in
discrete milestones; a freelancer delivers and submits proof of each one. GenLayer validators
independently review the actual submitted deliverable against the agreed requirement — and if
either side disagrees with that review, a single evidence-backed, binding round settles it. One
contract instance handles exactly one deal between one buyer and one freelancer, with each
milestone resolving independently.

## Why this needs GenLayer consensus

Whether a delivered piece of work "satisfies" a plain-language requirement is not something a hash
comparison, a keyword match, or a fixed rubric can decide — it's a judgment call a person would
make by actually reading the deliverable against what was asked for. `Deal` puts that judgment call
in front of independent validators instead of a platform or either interested party: each validator
fetches the deliverable's live URL content itself (`gl.nondet.web.render`) and must reach the same
verdict category as the leader before it's accepted. That's exactly the class of problem GenLayer's
non-deterministic consensus exists for — no two validators will ever produce byte-identical review
text, but they can, and must, agree on `approved` vs `rejected`.

## State design

- Each milestone is a set of parallel `DynArray` fields indexed by milestone number
  (`milestone_status`, `milestone_deliverable_url`, `milestone_reasoning`,
  `milestone_buyer_evidence`, `milestone_freelancer_evidence`, cancellation votes, and both
  `_rejected_at` / `_approved_at` timestamps) rather than one global deal status — so one disputed
  milestone never blocks payment on an already-approved one in the same deal.
- Explicit terminal/intermediate statuses per milestone (`pending → submitted → approved/rejected →
  paid/refunded/refund_claimed`) rather than inferring state from which fields are populated.
- Both time-window durations (`dispute_evidence_window_seconds`, `approval_challenge_window_seconds`)
  and the refund delay are fixed at deal creation, agreed by both parties implicitly by proceeding —
  never a value either party can supply at call time — and are measured against
  `gl.vm.get_timestamp()`, the transaction's own consensus-agreed clock.

## Validators and equivalence checks

- **Two separate consensus rounds for two separate stages of judgment.** `review_milestone` is the
  first-pass check; `resolve_dispute` is a second, binding round that only fires from a `rejected`
  milestone, re-fetches the deliverable fresh (the first round's snapshot is stale by dispute time),
  and weighs both parties' submitted evidence alongside the prior verdict's own reasoning — with
  explicit permission to overturn it.
- **A hand-written validator function**, not the higher-level `eq_principle` wrapper:
  `gl.vm.run_nondet(leader_fn, validator_fn)`, where `validator_fn` independently re-runs the full
  fetch-and-judge process and checks only that its verdict *category* matches the leader's
  (`approved`/`rejected`, or `approved`/`refunded` in the dispute round) — never that the reasoning
  text matches, which real LLM output could never produce identically.
- **Real artifact fetching, not self-report.** If the fetch fails, the contract falls back to
  judging the freelancer's own description alone rather than hard-failing, and says so plainly in
  the prompt so the model discounts an unverifiable claim accordingly.
- **A buyer challenge window on approvals.** An `approved` verdict isn't instantly payable — the
  buyer has a fixed window to force it into the same evidence-backed dispute path
  (`challenge_approved_milestone`) instead of it quietly becoming claimable.
- **Mutual cancellation needs no verdict at all.** If both parties call `propose_cancel` on the same
  milestone, funds return to the buyer immediately — consensus is spent only on genuine
  disagreements, never on a case both sides already agree on.

## Use case beyond a one-off demo

This is a reusable escrow primitive for any milestone-based engagement where "did the work satisfy
the brief" is a real judgment call, not a fixed check — freelance contracts, bounty payouts, or
commissioned deliverables generally. The registry/deal split (one permanent index contract, one
deal contract per engagement, deployed directly from the buyer's own wallet) generalizes to any
platform that wants many independent, self-custodied escrows discoverable from one place, without a
server-side wallet anywhere in the architecture.

## Documentation and tests

- `DECISION.md` — design rationale: why two rounds instead of one, why the deliverable is fetched
  rather than trusted, why the buyer gets an approval challenge window, and why mutual cancellation
  and fee lock-in work the way they do.
- `README.md` — contract purpose, consensus usage, and how to run the tests.
- `tests/test.py` — 37 `gltest` cases covering deployment validation, funding, independent
  milestone resolution, the full approve → claim flow, the full dispute flow, the approval
  challenge window (including expiry), mutual cancellation, and timed refunds.

## Repository contents

```
contracts/deal.py       — the Intelligent Contract (this submission)
contracts/registry.py   — supporting deterministic index Deal registers into (no consensus logic)
tests/test.py            — gltest suite (37 tests)
tests/requirements.txt
DECISION.md               — design rationale
README.md                 — contract overview + how to run tests
```
