# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json

MILESTONE_STATUSES = ("pending", "submitted", "approved", "rejected", "paid", "refund_claimed")


def _extract_json_from_string(raw: str) -> str:
    """
    LLM output is supposed to be a bare JSON object, but models sometimes wrap
    it in markdown code fences or add stray whitespace/preamble. Pull out the
    outermost {...} span so json.loads gets a clean object either way.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in model output")
    return text[start:end + 1]


class Deal(gl.Contract):
    registry: Address
    buyer: Address
    freelancer: Address
    title: str
    created_at: str
    funded: bool
    fee_bps_at_funding: u256

    refund_enabled: bool
    refund_delay_seconds: u256

    # Both windows are contract-enforced, buyer/freelancer-agreed durations
    # (set once at deal creation, like refund_delay_seconds) rather than a
    # value any single party can supply at call time.
    dispute_evidence_window_seconds: u256
    approval_challenge_window_seconds: u256

    milestone_descriptions: DynArray[str]
    milestone_amounts: DynArray[u256]
    milestone_status: DynArray[str]
    milestone_deliverable_url: DynArray[str]
    milestone_deliverable_description: DynArray[str]
    milestone_reasoning: DynArray[str]
    milestone_last_raw_response: DynArray[str]
    milestone_buyer_evidence: DynArray[str]
    milestone_freelancer_evidence: DynArray[str]
    milestone_buyer_cancel_vote: DynArray[bool]
    milestone_freelancer_cancel_vote: DynArray[bool]
    milestone_rejected_at: DynArray[str]
    milestone_approved_at: DynArray[str]

    def __init__(
        self,
        registry_value: str,
        buyer_value: str,
        freelancer_value: str,
        title_value: str,
        milestone_descriptions_value: list[str],
        milestone_amounts_value: list[int],
        created_at_value: int,
        refund_enabled_value: bool,
        refund_delay_seconds_value: int,
        dispute_evidence_window_seconds_value: int,
        approval_challenge_window_seconds_value: int,
    ):
        if len(milestone_descriptions_value) == 0:
            raise Exception("A deal must have at least one milestone")
        if len(milestone_descriptions_value) != len(milestone_amounts_value):
            raise Exception("Milestone descriptions and amounts must match in length")
        if dispute_evidence_window_seconds_value <= 0:
            raise Exception("dispute_evidence_window_seconds must be a positive number of seconds")
        if approval_challenge_window_seconds_value <= 0:
            raise Exception("approval_challenge_window_seconds must be a positive number of seconds")

        self.registry = Address(registry_value)
        self.buyer = Address(buyer_value)
        self.freelancer = Address(freelancer_value)
        self.title = title_value
        self.created_at = str(created_at_value)
        self.funded = False
        self.fee_bps_at_funding = u256(0)
        self.refund_enabled = refund_enabled_value
        self.refund_delay_seconds = u256(max(0, refund_delay_seconds_value))
        self.dispute_evidence_window_seconds = u256(dispute_evidence_window_seconds_value)
        self.approval_challenge_window_seconds = u256(approval_challenge_window_seconds_value)

        for i in range(len(milestone_descriptions_value)):
            self.milestone_descriptions.append(milestone_descriptions_value[i])
            self.milestone_amounts.append(u256(milestone_amounts_value[i]))
            self.milestone_status.append("pending")
            self.milestone_deliverable_url.append("")
            self.milestone_deliverable_description.append("")
            self.milestone_reasoning.append("")
            self.milestone_last_raw_response.append("")
            self.milestone_buyer_evidence.append("")
            self.milestone_freelancer_evidence.append("")
            self.milestone_buyer_cancel_vote.append(False)
            self.milestone_freelancer_cancel_vote.append(False)
            self.milestone_rejected_at.append("")
            self.milestone_approved_at.append("")

    def _total_amount(self) -> u256:
        total = u256(0)
        for amount in self.milestone_amounts:
            total = u256(total + amount)
        return total

    def _now_ms(self) -> int:
        # Contract-verifiable clock: in deterministic execution this resolves
        # to the transaction's own timestamp (agreed upon by consensus), so
        # no caller — buyer or freelancer — can supply an arbitrary value to
        # fast-forward or rewind any of the windows below.
        return int(gl.vm.get_timestamp().timestamp() * 1000)

    def _check_index(self, index: int):
        if index < 0 or index >= len(self.milestone_descriptions):
            raise Exception("Invalid milestone index")

    def _maybe_close_deal(self):
        # Once every milestone has reached a terminal, funds-settled state,
        # tell the Registry this deal is done so it stops showing as active.
        terminal_statuses = ("paid", "refund_claimed")
        for status in self.milestone_status:
            if status not in terminal_statuses:
                return
        registry_contract = gl.get_contract_at(self.registry)
        registry_contract.emit().update_status("completed")

    @gl.public.write.payable
    def fund_escrow(self):
        if gl.message.sender_address.as_hex.lower() != self.buyer.as_hex.lower():
            raise Exception("Only the buyer can fund this deal")
        if self.funded:
            raise Exception("Deal is already funded")
        if gl.message.value != self._total_amount():
            raise Exception("Sent amount must exactly match the sum of all milestone amounts")

        registry_contract = gl.get_contract_at(self.registry)
        if registry_contract.view().get_paused():
            raise Exception("EscrowCourt is currently paused for new deals")

        # Lock in the fee rate at funding time, so a later platform fee change
        # never retroactively affects a deal already in flight.
        self.fee_bps_at_funding = u256(int(registry_contract.view().get_fee_bps()))
        self.funded = True
        registry_contract.emit().register_deal(
            self.buyer.as_hex,
            self.freelancer.as_hex,
            self.title,
            int(self._total_amount()),
            int(self.created_at),
        )

    @gl.public.write
    def submit_milestone(self, index: int, deliverable_url: str, deliverable_description: str):
        if not self.funded:
            raise Exception("Deal is not funded yet")
        if gl.message.sender_address.as_hex.lower() != self.freelancer.as_hex.lower():
            raise Exception("Only the freelancer can submit milestone work")
        self._check_index(index)
        status = self.milestone_status[index]
        if status not in ("pending", "rejected"):
            raise Exception(f"Milestone cannot be submitted from status '{status}'")
        if not deliverable_url.strip() and not deliverable_description.strip():
            raise Exception("Provide a deliverable URL, a description, or both")
        self.milestone_deliverable_url[index] = deliverable_url.strip()
        self.milestone_deliverable_description[index] = deliverable_description.strip()
        self.milestone_status[index] = "submitted"
        self.milestone_buyer_cancel_vote[index] = False
        self.milestone_freelancer_cancel_vote[index] = False
        # A fresh submission starts a fresh review cycle — clear out any
        # evidence/timestamps left over from a prior rejection.
        self.milestone_buyer_evidence[index] = ""
        self.milestone_freelancer_evidence[index] = ""
        self.milestone_rejected_at[index] = ""
        self.milestone_approved_at[index] = ""

    @gl.public.write
    def review_milestone(self, index: int):
        self._check_index(index)
        if self.milestone_status[index] != "submitted":
            raise Exception("Milestone is not awaiting review")

        description = self.milestone_descriptions[index]
        url = self.milestone_deliverable_url[index]
        deliverable_description = self.milestone_deliverable_description[index]

        def leader_fn():
            # Live evidence is mandatory for any verdict that can eventually
            # release funds. A missing URL or a failed fetch means there is
            # nothing verifiable to judge, so this fails closed to "rejected"
            # rather than letting the LLM settle the milestone from the
            # freelancer's self-reported description alone.
            if not url:
                return {
                    "verdict": "rejected",
                    "reasoning": "No deliverable URL was submitted, so there is no live evidence to verify. Rejected closed rather than judged from the self-reported description alone.",
                    "_raw": "",
                }
            try:
                page_content = gl.nondet.web.render(url, mode="text")
            except Exception as fetch_error:
                return {
                    "verdict": "rejected",
                    "reasoning": f"The submitted URL could not be fetched ({fetch_error}), so there is no live evidence to verify. Rejected closed rather than judged from the self-reported description alone.",
                    "_raw": "",
                }
            fetch_note = "The live page content below was fetched directly from the submitted URL — judge the ACTUAL content, not just the freelancer's description of it."

            prompt = f"""You are an impartial escrow reviewer on a decentralized freelance platform. Multiple independent validators will review this same milestone and must reach consensus.

MILESTONE REQUIREMENT (agreed upon by both parties beforehand):
\"\"\"{description}\"\"\"

FREELANCER'S OWN DESCRIPTION OF THE DELIVERED WORK:
\"\"\"{deliverable_description or "(no description given)"}\"\"\"

DELIVERABLE URL: {url or "(none provided)"}
{fetch_note}

LIVE FETCHED PAGE CONTENT (if available):
\"\"\"{page_content[:4000] if page_content else "(not available)"}\"\"\"

Decide whether the actual deliverable reasonably satisfies the milestone requirement. Prioritize the live fetched content over the freelancer's own description when both are available — the freelancer's description alone is not sufficient proof. Be fair to both sides: don't reject over trivial gaps, but don't approve work that clearly misses the requirement's substance or that you could not verify at all.

Respond ONLY with a JSON object in this exact format:
{{
    "verdict": "approved" | "rejected",
    "reasoning": str (one to two concise sentences explaining the decision, at least 10 words)
}}
It is mandatory that you respond only using the JSON format above, nothing else.
Don't include any other words, characters, or markdown formatting.
Your output must be perfectly parsable by a JSON parser without errors.
"""
            raw = gl.nondet.exec_prompt(prompt)
            parsed = json.loads(_extract_json_from_string(raw))
            parsed["verdict"] = str(parsed["verdict"]).strip().lower()
            if parsed["verdict"] not in ("approved", "rejected"):
                parsed["verdict"] = "rejected"
            parsed["reasoning"] = str(parsed["reasoning"])
            parsed["_raw"] = raw
            return parsed

        def validator_fn(leader_result: gl.vm.Result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            leader_data = leader_result.calldata
            validator_data = leader_fn()
            return leader_data["verdict"] == validator_data["verdict"]

        result = gl.vm.run_nondet(leader_fn, validator_fn)
        self.milestone_status[index] = result["verdict"]
        self.milestone_reasoning[index] = result["reasoning"]
        self.milestone_last_raw_response[index] = str(result.get("_raw", ""))[:2000]

        now = str(self._now_ms())
        if result["verdict"] == "rejected":
            self.milestone_rejected_at[index] = now
            self.milestone_approved_at[index] = ""
        else:
            self.milestone_approved_at[index] = now
            self.milestone_rejected_at[index] = ""

    @gl.public.write
    def submit_dispute_evidence(self, index: int, evidence: str):
        self._check_index(index)
        if self.milestone_status[index] != "rejected":
            raise Exception("Disputes can only be raised on a rejected milestone")
        sender = gl.message.sender_address.as_hex.lower()
        if sender == self.buyer.as_hex.lower():
            self.milestone_buyer_evidence[index] = evidence.strip()
        elif sender == self.freelancer.as_hex.lower():
            self.milestone_freelancer_evidence[index] = evidence.strip()
        else:
            raise Exception("Only the buyer or freelancer can submit evidence")

    @gl.public.write
    def resolve_dispute(self, index: int):
        self._check_index(index)
        if self.milestone_status[index] != "rejected":
            raise Exception("Only a rejected milestone can be escalated to a binding dispute")

        # Fair evidence window: both sides need a real chance to call
        # submit_dispute_evidence before the binding verdict is locked in.
        rejected_at = self.milestone_rejected_at[index]
        if rejected_at:
            elapsed_seconds = (self._now_ms() - int(rejected_at)) / 1000
            remaining = int(self.dispute_evidence_window_seconds) - int(elapsed_seconds)
            if remaining > 0:
                raise Exception(f"Evidence window is still open — wait {remaining} more second(s) before resolving")

        buyer_evidence = self.milestone_buyer_evidence[index] or "(the buyer did not submit evidence)"
        freelancer_evidence = self.milestone_freelancer_evidence[index] or "(the freelancer did not submit evidence)"
        description = self.milestone_descriptions[index]
        deliverable = self.milestone_deliverable_description[index]
        url = self.milestone_deliverable_url[index]
        prior_reasoning = self.milestone_reasoning[index]

        def leader_fn():
            # Re-fetch (rather than trust) the deliverable: the earlier
            # rejection's page snapshot is stale by the time a binding
            # dispute is decided, and either party's "evidence" text is
            # unauthenticated self-reporting, not proof. This verdict moves
            # funds immediately in one direction or the other, so live
            # evidence is mandatory: a missing URL or a failed fetch fails
            # closed to "refunded" instead of settling from self-reported
            # descriptions and evidence alone.
            if not url:
                return {
                    "verdict": "refunded",
                    "reasoning": "No deliverable URL was submitted, so there is no live evidence to verify at dispute time. Refunded closed rather than settled from self-reported descriptions and evidence alone.",
                    "_raw": "",
                }
            try:
                page_content = gl.nondet.web.render(url, mode="text")
            except Exception as fetch_error:
                return {
                    "verdict": "refunded",
                    "reasoning": f"The submitted URL could not be fetched ({fetch_error}) at dispute time, so there is no live evidence to verify. Refunded closed rather than settled from self-reported descriptions and evidence alone.",
                    "_raw": "",
                }
            fetch_note = "The live page content below was re-fetched directly from the submitted URL at dispute time — judge the ACTUAL current content, not just either party's description of it."

            prompt = f"""You are an impartial arbitrator making a FINAL, BINDING decision in an escrow dispute. Multiple independent validators will review this same dispute and must reach consensus.

MILESTONE REQUIREMENT:
\"\"\"{description}\"\"\"

FREELANCER'S DELIVERABLE DESCRIPTION:
\"\"\"{deliverable}\"\"\"

DELIVERABLE URL: {url or "(none provided)"}
{fetch_note}

LIVE FETCHED PAGE CONTENT AT DISPUTE TIME (if available):
\"\"\"{page_content[:4000] if page_content else "(not available)"}\"\"\"

AN EARLIER AUTOMATED REVIEW REJECTED THIS DELIVERABLE, REASONING:
\"\"\"{prior_reasoning}\"\"\"

BUYER'S DISPUTE EVIDENCE / STATEMENT:
\"\"\"{buyer_evidence}\"\"\"

FREELANCER'S DISPUTE EVIDENCE / STATEMENT:
\"\"\"{freelancer_evidence}\"\"\"

Weigh both sides' evidence fairly and independently of the earlier automated rejection — you may overturn it if the live fetched content and evidence justify that. This decision is final: the milestone payment will be released to the freelancer if you rule "approved", or refunded to the buyer if you rule "refunded".

Respond ONLY with a JSON object in this exact format:
{{
    "verdict": "approved" | "refunded",
    "reasoning": str (two to three concise sentences explaining the final decision)
}}
It is mandatory that you respond only using the JSON format above, nothing else.
Don't include any other words, characters, or markdown formatting.
Your output must be perfectly parsable by a JSON parser without errors.
"""
            raw = gl.nondet.exec_prompt(prompt)
            parsed = json.loads(_extract_json_from_string(raw))
            parsed["verdict"] = str(parsed["verdict"]).strip().lower()
            if parsed["verdict"] not in ("approved", "refunded"):
                parsed["verdict"] = "refunded"
            parsed["reasoning"] = str(parsed["reasoning"])
            parsed["_raw"] = raw
            return parsed

        def validator_fn(leader_result: gl.vm.Result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            leader_data = leader_result.calldata
            validator_data = leader_fn()
            return leader_data["verdict"] == validator_data["verdict"]

        result = gl.vm.run_nondet(leader_fn, validator_fn)
        self.milestone_reasoning[index] = result["reasoning"]
        self.milestone_last_raw_response[index] = str(result.get("_raw", ""))[:2000]

        # This round is final and binding: unlike review_milestone's
        # "approved" (which still waits out a challenge window before it's
        # claimable), a dispute verdict moves funds immediately and leaves
        # no further escalation path in either direction.
        amount = self.milestone_amounts[index]
        if result["verdict"] == "approved":
            self.milestone_approved_at[index] = str(self._now_ms())
            fee = u256((int(amount) * int(self.fee_bps_at_funding)) // 10000)
            payout = u256(int(amount) - int(fee))
            self.milestone_status[index] = "paid"
            if int(fee) > 0:
                registry_contract = gl.get_contract_at(self.registry)
                treasury = Address(registry_contract.view().get_treasury())
                gl.emit_transfer(treasury, fee)
            gl.emit_transfer(self.freelancer, payout)
        else:
            self.milestone_status[index] = "refund_claimed"
            gl.emit_transfer(self.buyer, amount)
        self._maybe_close_deal()

    @gl.public.write
    def challenge_approved_milestone(self, index: int):
        """
        While an approved milestone's challenge window is still open, the
        buyer can force it back into the dispute path instead of it quietly
        becoming claimable. This reuses the existing evidence + binding
        resolve_dispute flow rather than a separate, unappealable auto-payout.
        """
        self._check_index(index)
        if self.milestone_status[index] != "approved":
            raise Exception("Only an approved milestone can be challenged")
        if gl.message.sender_address.as_hex.lower() != self.buyer.as_hex.lower():
            raise Exception("Only the buyer can challenge an approved milestone")

        approved_at = self.milestone_approved_at[index]
        if approved_at:
            elapsed_seconds = (self._now_ms() - int(approved_at)) / 1000
            if elapsed_seconds >= int(self.approval_challenge_window_seconds):
                raise Exception("The challenge window has already elapsed")

        self.milestone_status[index] = "rejected"
        self.milestone_rejected_at[index] = str(self._now_ms())
        self.milestone_approved_at[index] = ""
        self.milestone_reasoning[index] = "Approval challenged by the buyer within the challenge window; escalated back to dispute."

    @gl.public.write
    def propose_cancel(self, index: int):
        """
        Mutual-consent cancellation: once both buyer and freelancer have
        called this for the same milestone, the full amount refunds to the
        buyer immediately — no verdict, no fee, no dispute needed.
        """
        self._check_index(index)
        status = self.milestone_status[index]
        if status not in ("pending", "submitted", "rejected"):
            raise Exception(f"Milestone in status '{status}' cannot be cancelled")

        sender = gl.message.sender_address.as_hex.lower()
        if sender == self.buyer.as_hex.lower():
            self.milestone_buyer_cancel_vote[index] = True
        elif sender == self.freelancer.as_hex.lower():
            self.milestone_freelancer_cancel_vote[index] = True
        else:
            raise Exception("Only the buyer or freelancer can propose cancellation")

        if self.milestone_buyer_cancel_vote[index] and self.milestone_freelancer_cancel_vote[index]:
            amount = self.milestone_amounts[index]
            self.milestone_status[index] = "refund_claimed"
            self.milestone_reasoning[index] = "Cancelled by mutual agreement between buyer and freelancer."
            gl.emit_transfer(self.buyer, amount)
            self._maybe_close_deal()

    @gl.public.write
    def claim_timeout_refund(self, index: int):
        """
        If the deal was created with a refund window and the freelancer
        never submitted anything before it elapsed, the buyer can reclaim
        that milestone's funds unilaterally. Elapsed time is measured
        against the contract-verifiable transaction clock (see `_now_ms`),
        not a value supplied by the caller.
        """
        self._check_index(index)
        if not self.refund_enabled:
            raise Exception("Timed refunds are not enabled on this deal")
        if gl.message.sender_address.as_hex.lower() != self.buyer.as_hex.lower():
            raise Exception("Only the buyer can claim a timeout refund")
        if self.milestone_status[index] != "pending":
            raise Exception("Timeout refund only applies before the freelancer has submitted anything")
        elapsed_seconds = (self._now_ms() - int(self.created_at)) / 1000
        if elapsed_seconds < int(self.refund_delay_seconds):
            raise Exception("The refund delay has not elapsed yet")
        amount = self.milestone_amounts[index]
        self.milestone_status[index] = "refund_claimed"
        self.milestone_reasoning[index] = "Refunded to the buyer after the freelancer did not submit within the refund delay window."
        gl.emit_transfer(self.buyer, amount)
        self._maybe_close_deal()

    @gl.public.write
    def claim_payment(self, index: int):
        """
        Pays out an approved milestone to the freelancer, minus the platform
        fee locked in at funding time. Only callable once the buyer's
        approval challenge window has fully elapsed without a challenge —
        so an approval from review_milestone is never instantly payable.
        """
        self._check_index(index)
        if gl.message.sender_address.as_hex.lower() != self.freelancer.as_hex.lower():
            raise Exception("Only the freelancer can claim payment")
        if self.milestone_status[index] != "approved":
            raise Exception("Milestone is not in an approved, claimable state")

        approved_at = self.milestone_approved_at[index]
        if approved_at:
            elapsed_seconds = (self._now_ms() - int(approved_at)) / 1000
            remaining = int(self.approval_challenge_window_seconds) - int(elapsed_seconds)
            if remaining > 0:
                raise Exception(f"Buyer's challenge window is still open — wait {remaining} more second(s) before claiming")

        amount = self.milestone_amounts[index]
        fee = u256((int(amount) * int(self.fee_bps_at_funding)) // 10000)
        payout = u256(int(amount) - int(fee))
        self.milestone_status[index] = "paid"
        if int(fee) > 0:
            registry_contract = gl.get_contract_at(self.registry)
            treasury = Address(registry_contract.view().get_treasury())
            gl.emit_transfer(treasury, fee)
        gl.emit_transfer(self.freelancer, payout)
        self._maybe_close_deal()

    @gl.public.view
    def get_last_raw_response(self, index: int) -> str:
        self._check_index(index)
        return self.milestone_last_raw_response[index]

    @gl.public.view
    def get_deal_details(self) -> str:
        milestones = []
        for i in range(len(self.milestone_descriptions)):
            milestones.append({
                "index": i,
                "description": self.milestone_descriptions[i],
                "amount": str(self.milestone_amounts[i]),
                "status": self.milestone_status[i],
                "deliverable_url": self.milestone_deliverable_url[i],
                "deliverable_description": self.milestone_deliverable_description[i],
                "reasoning": self.milestone_reasoning[i],
                "buyer_evidence": self.milestone_buyer_evidence[i],
                "freelancer_evidence": self.milestone_freelancer_evidence[i],
                "buyer_cancel_vote": self.milestone_buyer_cancel_vote[i],
                "freelancer_cancel_vote": self.milestone_freelancer_cancel_vote[i],
                "rejected_at": self.milestone_rejected_at[i],
                "approved_at": self.milestone_approved_at[i],
            })
        return json.dumps({
            "registry": self.registry.as_hex,
            "buyer": self.buyer.as_hex,
            "freelancer": self.freelancer.as_hex,
            "title": self.title,
            "created_at": self.created_at,
            "funded": str(self.funded),
            "fee_bps_at_funding": str(self.fee_bps_at_funding),
            "refund_enabled": str(self.refund_enabled),
            "refund_delay_seconds": str(self.refund_delay_seconds),
            "dispute_evidence_window_seconds": str(self.dispute_evidence_window_seconds),
            "approval_challenge_window_seconds": str(self.approval_challenge_window_seconds),
            "milestones": milestones,
        })

