from __future__ import annotations

import pytest

from apps.core.strategy.jim_takeover_mode import resolve_price_photo_full_takeover


@pytest.mark.parametrize(
    ("jp", "jf", "pf", "ph", "want"),
    [
        (True, False, True, True, True),
        (False, True, True, True, True),
        (True, True, True, True, True),
        (True, True, False, True, False),
        (True, True, True, False, False),
        (True, True, False, False, False),
        (False, False, True, False, True),
    ],
)
def test_resolve_price_photo_full_takeover(
    jp: bool, jf: bool, pf: bool, ph: bool, want: bool
) -> None:
    assert (
        resolve_price_photo_full_takeover(
            jim_price=jp, jim_photo=jf, price_full=pf, photo_full=ph
        )
        == want
    )
