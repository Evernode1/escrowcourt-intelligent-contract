# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json

MILESTONE_STATUSES = ("pending", "submitted", "approved", "rejected", "paid", "refunded")


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
            page_content = ""
            fetch_note = "No deliverable URL was submitted — judge from the description alone."
            if url:
                try:
                    page_content = gl.nondet.web.render(url, mode="text")
                    fetch_note = "The live page content below was fetched directly from the submitted URL — judge the ACTUAL content, not just the freelancer's description of it."
                except Exception as fetch_error:
                    fetch_note = f"The submitted URL could not be fetched ({fetch_error}). Judge from the description alone, and note the unverifiable link in your reasoning."

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
            # unauthenticated self-reporting, not proof.
            page_content = ""
            fetch_note = "No deliverable URL was submitted — judge from the description and evidence alone."
            if url:
                try:
                    page_content = gl.nondet.web.render(url, mode="text")
                    fetch_note = "The live page content below was re-fetched directly from the submitted URL at dispute time — judge the ACTUAL current content, not just either party's description of it."
                except Exception as fetch_error:
                    fetch_note = f"The submitted URL could not be fetched ({fetch_error}). Judge from the description and evidence alone, and note the unverifiable link in your reasoning."

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
        self.milestone_status[index] = result["verdict"]
        self.milestone_reasoning[index] = result["reasoning"]
        self.milestone_last_raw_response[index] = str(result.get("_raw", ""))[:2000]
        if result["verdict"] == "approved":
            self.milestone_approved_at[index] = str(self._now_ms())
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
        self.milestone_
