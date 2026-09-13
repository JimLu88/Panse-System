# Order recovery: master summary is not an extra child

## Verified incident

The September 14 password callback completed all three reports, then the fixed
child delivery pipeline sent 17 images for 11 September 13 parent orders.
Two images (factory numbers 484 and 500) used parent IDs as child IDs, with
concatenated product titles, no SKU, and amounts equal to the respective sums
of four and two real child lines. These are duplicate summary risks, not two
additional products. Historical rows, numbering and sent receipts are retained.
No cancellation, voiding, resending, or business rerun is part of this patch.

Four other paid orders are excluded by existing policy: two wood samples and
two low-value records (21/22 CNY) classified by the existing 400 CNY threshold.
The patch does not redefine that policy or claim their nature was independently
confirmed from customer instructions.

## Fixed behavior

- Import skips a parent-ID/no-SKU summary when distinct children are already
  present in the incoming batch or stored details, regardless of report order.
- Delivery also excludes preserved legacy summaries once real children exist.
  Single legacy rows without distinct children remain supported.
- Already sent summary rows remain visible as explicit review exceptions in
  the delivery count gate; they are not automatically voided or deleted.
- Closeout and password notification include parent plus child image counts,
  while retaining separate counters for audit.

## Verification and boundary

82 focused tests pass (summary regression, child delivery, password callback,
and catch-up). This is local verification, not deployment or authorization to
alter the two already sent factory instructions. Production activation and
readback must be recorded separately. No frozen campaign rule is changed.
