"""已知管理端页面路由与审核请求的确定性一致性校验。"""

from urllib.parse import unquote, urlsplit

from forj_review_assistant.businesses.page_catalog import identities_for_path
from forj_review_assistant.businesses.registry import BusinessProfileNotFound
from forj_review_assistant.contracts.review import ReviewRequest


class BusinessContextMismatch(BusinessProfileNotFound):
    """请求业务/地区与已知页面路由矛盾，禁止执行审核。"""


def validate_request_route(request: ReviewRequest) -> None:
    url = urlsplit(str(request.page_url))
    if url.hostname != "admin.forjtruck.com":
        return
    path = unquote(url.path)
    candidates = identities_for_path(path)
    if not candidates:
        return
    allowed = {(item.business_type, item.region) for item in candidates}
    if (request.business_type, request.region) in allowed:
        return
    # 地址可以被多个业务共用（一致性审核与过户审核同址），因此这里只能校验
    # 「请求的业务/地区在该地址的候选集合里」，不能要求唯一匹配。地区仍然
    # 是确定的：同址的两个业务属于同一个地区，拿长春的业务去审青岛的单子
    # 照样被挡住。
    expected = "、".join(sorted(f"{business.value}/{region.value}" for business, region in allowed))
    raise BusinessContextMismatch(
        f"页面路由只接受 {expected}，与请求业务或地区不一致"
    )
