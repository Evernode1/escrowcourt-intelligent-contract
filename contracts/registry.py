# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import json


@allow_storage
@dataclass
class DealEntry:
    buyer: Address
    freelancer: Address
    contract: Address
    title: str
    total_amount: u256
    created_at: str
    status: str  # "active" | "completed" | "cancelled"

    def to_dict(self):
        return {
            "buyer": self.buyer.as_hex,
            "freelancer": self.freelancer.as_hex,
            "contract": self.contract.as_hex,
            "title": self.title,
            "total_amount": str(self.total_amount),
            "created_at": self.created_at,
            "status": self.status,
        }


class Registry(gl.Contract):
    deals: TreeMap[Address, DealEntry]
    deals_by_party: TreeMap[Address, TreeMap[Address, DealEntry]]  # party address -> {deal contract -> entry}
    deal_order: DynArray[Address]  # insertion order, oldest first

    owner: Address
    fee_bps: u256       # platform fee in basis points, deducted only on an approved payout claim
    treasury: Address   # where the fee goes
    paused: bool         # when true, new deals cannot be funded (existing ones are unaffected)

    def __init__(self):
        self.owner = gl.message.sender_address
        self.treasury = gl.message.sender_address
        self.fee_bps = u256(0)
        self.paused = False

    def _only_owner(self):
        if gl.message.sender_address.as_hex.lower() != self.owner.as_hex.lower():
            raise Exception("Only the contract owner can do this")

    @gl.public.write
    def set_fee_bps(self, new_fee_bps: int):
        self._only_owner()
        if new_fee_bps < 0 or new_fee_bps > 1000:
            raise Exception("Fee cannot exceed 1000 bps (10%)")
        self.fee_bps = u256(new_fee_bps)

    @gl.public.write
    def set_treasury(self, new_treasury: str):
        self._only_owner()
        self.treasury = Address(new_treasury)

    @gl.public.write
    def set_paused(self, is_paused: bool):
        self._only_owner()
        self.paused = is_paused

    @gl.public.write
    def transfer_ownership(self, new_owner: str):
        self._only_owner()
        self.owner = Address(new_owner)

    @gl.public.view
    def get_fee_bps(self) -> str:
        return str(self.fee_bps)

    @gl.public.view
    def get_treasury(self) -> str:
        return self.treasury.as_hex

    @gl.public.view
    def get_paused(self) -> bool:
        return self.paused

    @gl.public.view
    def get_owner(self) -> str:
        return self.owner.as_hex

    @gl.public.view
    def get_deal_count(self) -> str:
        return str(len(self.deal_order))

    @gl.public.write
    def register_deal(
        self,
        buyer: str,
        freelancer: str,
        title: str,
        total_amount: int,
        created_at: int,
    ):
        # Called by a Deal contract itself, once it has been funded by the buyer.
        deal_contract = gl.message.sender_address
        entry = DealEntry(
            buyer=Address(buyer),
            freelancer=Address(freelancer),
            contract=deal_contract,
            title=title,
            total_amount=u256(total_amount),
            created_at=str(created_at),
            status="active",
        )
        self.deals[deal_contract] = entry
        self.deal_order.append(deal_contract)
        self.deals_by_party.get_or_insert_default(Address(buyer))[deal_contract] = entry
        self.deals_by_party.get_or_insert_default(Address(freelancer))[deal_contract] = entry

    @gl.public.write
    def update_status(self, status: str):
        # Called by the Deal contract itself when the deal fully resolves.
        deal_contract = gl.message.sender_address
        entry = self.deals.get(deal_contract, None)
        if entry is None:
            raise Exception("Unknown deal contract")
        entry.status = status

        # deals_by_party holds separate copies of the same entry (one under
        # the buyer's map, one under the freelancer's), so they must be
        # updated explicitly or they'd stay stuck at their original status.
        buyer_deals = self.deals_by_party.get(entry.buyer, None)
        if buyer_deals is not None:
            buyer_entry = buyer_deals.get(deal_contract, None)
            if buyer_entry is not None:
                buyer_entry.status = status

        freelancer_deals = self.deals_by_party.get(entry.freelancer, None)
        if freelancer_deals is not None:
            freelancer_entry = freelancer_deals.get(deal_contract, None)
            if freelancer_entry is not None:
                freelancer_entry.status = status

    @gl.public.view
    def get_deals(self, limit: int) -> str:
        addrs = list(self.deal_order)[-limit:]
        addrs.reverse()
        result = []
        for a in addrs:
            entry = self.deals.get(a, None)
            if entry is not None:
                result.append(entry.to_dict())
        return json.dumps(result)

    @gl.public.view
    def get_deals_by_status(self, status: str, limit: int) -> str:
        result = []
        for a in reversed(list(self.deal_order)):
            entry = self.deals.get(a, None)
            if entry is not None and entry.status == status:
                result.append(entry.to_dict())
            if len(result) >= limit:
                break
        return json.dumps(result)

    @gl.public.view
    def get_deals_for_address(self, address: str, limit: int) -> str:
        deals = self.deals_by_party.get(Address(address), None)
        result = []
        if deals is not None:
            for a, entry in list(deals.items())[-limit:]:
                result.append(entry.to_dict())
        result.reverse()
        return json.dumps(result)
      
