"""Pure capped ordinary price calculation; never changes the ERP target or custom base."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING


def ordinary_price(daily, target, rate, cap, *, max_delta='2'):
    from campaign_generate_current_files import official_cut
    try:
        daily, target, rate, cap, limit = map(lambda v: Decimal(str(v)), (daily,target,rate,cap,max_delta))
        if (any(not n.is_finite() for n in (daily,target,rate,cap,limit))
                or min(daily,target,cap)<=0 or not 0<rate<1 or not 0<=limit<=2
                or any(n != n.quantize(Decimal('.01')) for n in (daily,target))):
            raise ValueError('invalid_cap_price_input')
        cut=official_cut(daily,rate)
        desired=min(target,cap)
        deduction=(daily-cut-desired).quantize(Decimal('.01'),rounding=ROUND_CEILING)
        final=daily-cut-deduction
        if deduction<0 or final<=0:raise ValueError('price_formula_cannot_meet_frozen_target')
        if final>cap:raise ValueError('final_exceeds_current_platform_cap')
        if abs(final-target)>limit:raise ValueError('platform_cap_exceeds_frozen_target_tolerance_requires_rotation')
        return dict(daily=str(daily), target=str(target), platform_cap=str(cap),
                    official_cut=str(cut), deduct=str(deduction), final=str(final),
                    delta=str(final-target), cap_tolerance=str(limit))
    except InvalidOperation as exc:
        raise ValueError('invalid_cap_price_input') from exc
