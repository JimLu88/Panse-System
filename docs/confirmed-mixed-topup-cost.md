# Confirmed mixed top-up accessory cost

`Order.parts_override` may record a confirmed socket quantity, unit cost,
total, freight and primary order number. `physical_cost_breakdown` validates
positive integral quantity and exact multiplication before honoring that cost
ahead of the generic customization-to-primary-order rule. Unmarked orders
retain existing behavior. The primary order reference is not a transfer of
money, an inventory movement or a new factory bill.

The confirmed accessory cost remains on the top-up order. Platform fees and
tax continue through the existing independent accounting functions. No order
amount, refund, procurement, stock or external factory communication changes.

Relationship review: finance.actual -> finance.profit and settlement.expense
consume the same cost breakdown; source node already references this service.
The generic changed-file review also lists stock and marketing downstream
candidates; this read-only calculation does not call those writers. Existing
unrelated source fingerprint drift is not re-frozen by this scoped change.

Validation: three sockets at 55 with zero freight = 165; two sockets at 55
with eight freight = 118. The production order must also be read back through
the same function after a unified API/Web release.
