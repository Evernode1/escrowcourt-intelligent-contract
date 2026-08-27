# EscrowCourt Deal — Milestone Escrow with Two-Round AI Arbitration

A standalone GenLayer Intelligent Contract: milestone-based freelance
escrow where GenLayer validator consensus — not a platform, not either
party — decides whether delivered work satisfies what was agreed, and
gives both sides a real, evidence-based path to contest that decision
before money moves.

## Why this exists

Freelance escrow has one hard problem: someone has to judge whether the
delivered work actually satisfies what was asked for. A platform doing
that judgment is a single point of trust and bias. Letting either party
self-certify is no judgment at all. `Deal` puts that judgment in the
hands of GenLayer's decentralized validators, who independently review
the actual submitted deliverable — not just a description of it — against
the milestone's stated requirement.

## Two separate consensus rounds, for two separate stages of judgment

**1. `review_milestone`** — the first-pass review. When a freelancer
submits a milestone, any party can trigger this. Validators fetch the
deliverable's live URL content directly (`gl.nondet.web.render`) and
weigh it against the freelancer's own description and the original
requirement, explicitly instructed to prioritize the fetched content
over the freelancer's self-report. Verdict: `approved` or `rejected`.

**2. `resolve_dispute`** — a separate, binding arbitration round. A
rejected milestone isn't final: both buyer and freelancer get a fixed
evidence window (`submit_dispute_evidence`) to make their case, and an
approved milestone can also be pulled back into this same path by the
buyer within a challenge window (`challenge_approved_milestone`) instead
of quietly becoming claimable. When `resolve_dispute` runs, validators
**re-fetch** the deliverable URL fresh (the original review's snapshot is
stale by dispute time) and weigh it alongside both parties' evidence and
the prior verdict's reasoning — explicitly told they may overturn it.
This verdict is final and moves funds directly: `approved` releases
payment, `refunded` returns funds to the buyer.

## How consensus is implemented

Both rounds use GenLayer's lower-level `gl.vm.run_nondet(leader_fn,
validator_fn)` primitive with a hand-written validator function, rather
than the higher-level `gl.eq_principle.prompt_non_comparative` wrapper:

```python
def leader_fn():
    # ... fetch the deliverable, prompt the LLM, parse its JSON verdict ...
    return parsed

def validator_fn(leader_result: gl.vm.Result) -> bool:
    if not isinstance(leader_result, gl.vm.Return):
        return False
    leader_data = leader_result.calldata
    validator_data = leader_fn()   # this validator runs the same process independently
    return leader_data["verdict"] == validator_data["verdict"]

result = gl.vm.run_nondet(leader_fn, validator_fn)
```

Each validator independently re-runs the full fetch-and-judge process and
consensus is reached on whether their verdict *category* (`approved`/
`rejected`, or `approved`/`refunded` in the dispute round) matches the
leader's — not on identical reasoning text, which real LLM output could
never produce byte-for-byte. This is the same non-comparative-equivalence
idea `eq_principle.prompt_non_comparative` provides, written out by hand
for full control over exactly what "equivalent" means here (verdict
category equality) and full control over the fetch-and-judge process
itself.

## What stops this from being a rubber stamp

- **Live evidence is mandatory, not optional.** `gl.nondet.web.render`
  pulls the actual live page for any verdict that can eventually release
  or refund funds. If no URL was submitted, or the fetch fails, the
  contract never asks the model to settle the milestone from the
  freelancer's self-reported description alone — it fails closed to
  `rejected` (in `review_milestone`) or `refunded` (in
  `resolve_dispute`) without invoking the model at all.
- **A contract-verifiable clock.** All time windows (`refund_delay_seconds`,
  `dispute_evidence_window_seconds`, `approval_challenge_window_seconds`)
  are measured against `gl.vm.get_timestamp()` — the transaction's own
  agreed-upon timestamp — not a value either party can supply.
- **A buyer challenge window on approvals.** An `approved` verdict isn't
  instantly payable; the buyer has a fixed window to force it into the
  same evidence-backed dispute path instead.
- **One binding round, not infinite re-rolling.** `resolve_dispute` only
  fires from `rejected` status and settles the milestone (`approved` or
  `refunded`) — there's no way to keep re-triggering it hoping for a
  different answer.
- **Mutual cancellation needs no verdict at all.** If both parties agree
  a milestone should just be called off (`propose_cancel` from each
  side), funds return to the buyer immediately — consensus is only spent
  on genuine disagreements, not on cases both sides already agree on.

## State design

Each milestone is tracked with parallel `DynArray` fields indexed by
milestone number (`milestone_status`, `milestone_deliverable_url`,
`milestone_reasoning`, `milestone_buyer_evidence`, etc.), so milestones
resolve independently — one disputed milestone never blocks payment on
an already-approved one in the same deal.

```
pending → submitted → approved → paid
                    ↘ rejected → (resubmit → submitted)
                              ↘ (evidence + resolve_dispute) → approved → paid
                                                              ↘ refunded → refund_claimed
pending | submitted | rejected → propose_cancel (both parties) → refund_claimed
pending (only)                 → claim_timeout_refund (if enabled) → refund_claimed
approved (challenge window open) → challenge_approved_milestone → rejected → (dispute path)
```

## Supporting contract: `registry.py`

`Deal` calls into a separate, permanent `Registry` contract
(`gl.get_contract_at(self.registry)`) to register itself once funded and
to look up the platform fee rate and treasury address. `Registry` is
included here because `Deal` depends on it to deploy meaningfully, but it
is deliberately plain, deterministic bookkeeping — an address-keyed
index with owner-gated admin settings — with no consensus logic of its
own; `Deal` is the Intelligent Contract this submission is about.

## Economics

An optional platform fee (basis points, capped at 10%, set by the
Registry owner) is locked in per-deal **at funding time** — a later fee
change never retroactively affects a deal already in flight — and is
deducted only from an approved payout, never from a refund or
cancellation.

## Tests

`tests/test.py` — 35 tests covering: deployment validation (milestone
count, window requirements), funding (exact-amount, buyer-only,
double-fund rejection, paused-registry blocking, registry sync), fee
lockup and admin controls, milestone submission and independence, the
mandatory-live-evidence fail-closed rule (missing URL and unfetchable
URL, in both `review_milestone` and `resolve_dispute`), the full
approve → claim → double-claim-rejected flow, the full dispute flow
(evidence windows, resolution, status transitions), the approval
challenge window (including its expiry), mutual cancellation, and timed
refunds (including respecting the delay and only applying pre-submission).

```
pip install -r tests/requirements.txt
# start GenLayer Studio locally first
pytest tests/test.py -v -s
```
