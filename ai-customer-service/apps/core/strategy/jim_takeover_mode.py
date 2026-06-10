"""询价/拍下 vs 实拍：各自「仅推送」或「完整 Jim」组合规则。"""


def resolve_price_photo_full_takeover(
    *,
    jim_price: bool,
    jim_photo: bool,
    price_full: bool,
    photo_full: bool,
) -> bool:
    """
    当询价/拍下与实拍同时命中时，仅当**所有**命中分支都要求完整接管时，才走完整 Jim；
    任一分支为「仅推送」则整体为仅推送（避免半锁会话）。
    """
    branches: list[bool] = []
    if jim_price:
        branches.append(bool(price_full))
    if jim_photo:
        branches.append(bool(photo_full))
    return all(branches) if branches else True
