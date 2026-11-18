"""Pure receipt validation. Aggregate import counts never prove a shipment.

shipping_readbacks is a normalized adapter contract, NOT a known Taobao JSON
shape. A site adapter must supply real per-order logistics evidence; missing
adapter/evidence stays unknown. No HTTP, credentials or database access here.
"""
from datetime import datetime, timezone


def batch_state(results):
    states = [v['status'] for v in results.values()]
    if states and all(s == 'confirmed' for s in states):
        return 'confirmed'
    if 'confirmed' in states:
        return 'part_failed'
    if 'unknown' in states or not states:
        return 'unknown'
    return 'failed'


def reconcile(rows, task, errors, readbacks=None, *, expected_task_id, started_at=None):
    results = {r['order_no']: {'status': 'unknown', 'detail': '缺少该订单的官方物流回读，不能确认发货'} for r in rows}
    if not rows or len(results) != len(rows) or str(task.get('taskId', '')) != expected_task_id:
        return results, 'unknown'
    status = task.get('taskStatus')
    successes, failures = task.get('successCount'), task.get('failCount')
    if (status not in ('FINISHED', 'PART_FAILED', 'ERROR')
            or type(successes) is not int or type(failures) is not int
            or min(successes, failures) < 0 or successes + failures != len(rows)):
        return results, 'unknown'
    error_rows = errors.get('data') if isinstance(errors, dict) and errors.get('success') is True else None
    failure_map = {}
    if isinstance(error_rows, list):
        for item in error_rows:
            if not isinstance(item, dict):
                failure_map = {}; break
            tid = str(item.get('orderNo') or item.get('tradeId') or '')
            if tid not in results or tid in failure_map:
                failure_map = {}; break
            failure_map[tid] = str(item.get('errorMessage') or item.get('errorMsg') or '平台明确拒绝该订单')[:220]
    if len(failure_map) != failures:
        failure_map = {}
    candidates = {}
    for proof in readbacks if isinstance(readbacks, list) else []:
        if isinstance(proof, dict) and isinstance(proof.get('order_no'), str):
            candidates.setdefault(proof.get('order_no'), []).append(proof)
    for row in rows:
        tid = row['order_no']
        if tid in failure_map:
            results[tid] = {'status': 'failed', 'detail': failure_map[tid]}
            continue
        proofs = candidates.get(tid, [])
        if len(proofs) != 1:
            continue
        proof = proofs[0]
        try:
            observed = datetime.fromisoformat(proof['observed_at'])
            start = datetime.fromisoformat(started_at) if started_at else None
            valid_time = (observed.tzinfo is not None and observed <= datetime.now(timezone.utc)
                          and (not start or observed >= start))
        except (KeyError, TypeError, ValueError):
            valid_time = False
        expected_children = row.get('sub_order_nos')
        if (valid_time and proof.get('source') == 'taobao_logistics_readback'
                and isinstance(proof.get('evidence_ref'), str) and proof['evidence_ref'].strip()
                and proof.get('shop') == row.get('shop') and row.get('shop')
                and proof.get('carrier') == row['carrier']
                and proof.get('tracking_no') == row['tracking_no']
                and proof.get('seller_confirm') is True
                and proof.get('trade_status') in ('WAIT_BUYER_CONFIRM_GOODS', 'TRADE_FINISHED')
                and isinstance(expected_children, list) and expected_children
                and isinstance(proof.get('sub_order_nos'), list)
                and all(isinstance(s, str) for s in proof['sub_order_nos'])
                and sorted(proof['sub_order_nos']) == sorted(expected_children)):
            results[tid] = {'status': 'confirmed', 'detail': '官方逐单物流、店铺及全部子单已核对'}
    # Contradictory counts are not repaired by guessing which proof belongs here.
    if sum(v['status'] == 'confirmed' for v in results.values()) > successes:
        return {r['order_no']: {'status': 'unknown', 'detail': '官方汇总与逐单证据冲突'} for r in rows}, 'unknown'
    return results, batch_state(results)
