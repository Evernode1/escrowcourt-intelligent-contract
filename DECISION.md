# EscrowCourt Deal — Decision Record

## The product

A buyer funds a deal in discrete milestones. A freelancer submits proof of each one — a URL, a
description, or both. Any party can then trigger `review_milestone`, where GenLayer validators
fetch the actual deliverable and judge it against the milestone's agreed requirement. A rejected
milestone isn't final: both sides get a fixed evidence window, and a single binding
`resolve_dispute` round — re-fetching the deliverable fresh and weighing both parties' evidence —
settles it either way. An approved milestone isn't instantly payable either: the buyer gets a fixed
challenge window to pull it back into that same dispute path before the freelancer can claim
payment.

## Counterfactual: why not a platform arbiter or pure self-report

Two conventional shapes exist for freelance escrow, and both have a structural problem:

- **A platform-run dispute process.** Whoever runs the platform is deciding whose money it is.
  Even a well-intentioned platform is a single point of trust and a single point of bias — and an
  unscrupulous one has a direct incentive to rule in whichever direction keeps more users paying
  fees.
- **Pure self-report.** If the freelancer's own claim that a milestone is done is sufficient to
  release funds, there is no actual judgment happening — the check exists in name only.

`Deal` puts the judgment in front of independent GenLayer validators instead, each of whom fetches
the real deliverable themselves rather than trusting either party's account of it, and must agree
with the leader at the verdict-category level before a review is accepted.

## Why the review is irreducibly a judgment call

There is no deterministic rule for "does this piece of work satisfy this plain-language
requirement." The milestone requirement is free text agreed upon between two people; the
deliverable is a live web page or a written description. Reading the actual page content against
the actual requirement — noticing a claimed deliverable that doesn't match what was fetched, or a
requirement that's been substantively (if not perfectly) met — requires the kind of reading a
person does, not a hash or keyword check. That's why `review_milestone` and `resolve_dispute` both
route through `gl.vm.run_nondet`: the leader fetches and judges, and every validator must
independently reach the same verdict category, never byte-identical reasoning text, which no two
independent LLM calls would ever produce.

## Why two rounds, and why the second one is bounded

A single review round means a bad or borderline call has no recourse beyond redoing the same round
hoping for a different answer — which isn't a check at all if repeated indefinitely. `resolve_dispute`
exists as a genuinely different, escalated round: it only fires from a `rejected` milestone (or an
`approved` one the buyer actively challenges), gives both sides a real evidence window first, and
re-fetches the deliverable rather than reusing the first round's now-stale snapshot. Once it runs,
the milestone settles — `approved` or `refunded` — with no further escalation path. That
boundedness is deliberate: a dispute mechanism that can be re-triggered indefinitely is not a
safeguard, it's a way to keep re-rolling until a favorable answer appears.

## Why an approval isn't instantly payable

Without `challenge_approved_milestone`, an `approved` verdict would become claimable the instant it
was issued, with no recourse for a buyer who genuinely believes that specific call was wrong. The
fixed challenge window gives the buyer a real, bounded chance to force a second look through the
same evidence-backed dispute path already built for rejections — rather than inventing a separate,
unappealable payout mechanism for the approval side alone.

## Why fetch the deliverable instead of trusting either party's account of it

A freelancer's own description of their work is not proof of it, and a rejected freelancer's
"evidence" in a dispute is equally self-interested. Both `review_milestone` and `resolve_dispute`
fetch the deliverable's live URL directly (`gl.nondet.web.render`) and are explicitly instructed to
prioritize that fetched content over either party's own account of it when both are available. The
dispute round re-fetches rather than reusing the original review's snapshot, because a deliverable
behind a URL can change between the first review and a dispute resolved potentially much later —
judging a stale snapshot at binding-decision time would be judging a moment that may no longer
reflect what's actually there. If the fetch itself fails, the contract falls back to judging the
description alone rather than hard-failing the milestone outright, and says so plainly in the
prompt so the model can discount an unverifiable claim rather than treating a fetch failure as
silent proof of anything.

## Why the clock is contract-verifiable, not caller-supplied

Every timing rule in this contract — the dispute evidence window, the approval challenge window,
the refund delay — is measured against `gl.vm.get_timestamp()`, the transaction's own
consensus-agreed timestamp, rather than any value a buyer or freelancer could pass as an argument.
A caller-suppliable clock would let either party manufacture "the window already closed" or "the
window hasn't started yet" on demand; a contract-verifiable one means every window means what it
says regardless of who's calling.

## Why mutual cancellation bypasses consensus entirely

`propose_cancel` requires both buyer and freelancer to independently agree a milestone should be
called off before anything happens — and when they do, funds return to the buyer immediately, with
no AI review at all. This is a deliberate cost-of-consensus decision: a validator round exists to
resolve genuine disagreement about whether work satisfies a requirement. When both parties already
agree there's nothing to adjudicate, spending a consensus round on that agreement would be pure
overhead with no judgment actually being exercised.

## Why the platform fee locks in at funding time

`fee_bps_at_funding` is read from the Registry once, at the moment a deal is funded, and stored on
the Deal itself rather than read fresh from the Registry every time a payout is claimed. This means
a Registry owner changing the platform-wide fee rate can never retroactively change the economics of
a deal that's already in flight — every deal's fee is exactly what it was funded under, for its
entire lifetime, regardless of what the Registry's fee setting becomes later.

## Why Registry is a separate, plain contract

`Registry` intentionally contains no consensus logic — it's an address-keyed index (which deals
exist, their status, per-party lookups) plus owner-gated admin settings (fee rate, treasury,
pause). Keeping it deterministic means the one piece of this system that many different Deal
instances all depend on is also the simplest, most auditable piece, with the actual judgment logic
concentrated entirely in Deal, where it's this submission's focus.
