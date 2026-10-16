"""已知管理端页面路由与审核请求的确定性一致性校验。"""

from urllib.parse import unquote, urlsplit

from forj_review_assistant.businesses.page_catalog import identities_for_path
from forj_review_assistant.businesses.registry import BusinessProfileNotFound
from forj_review_assistant.contracts.review import ReviewRequest


class BusinessContextMismatch(BusinessProfileNotFound):
    """请求业务/地区与已知页面路由矛盾，禁止执行审核。"""


def validate_request_route(request: ReviewRequest, profiles=None) -> None:
    url = urlsplit(str(request.page_url))
    if url.hostname != "admin.forjtruck.com":
        return
    path = unquote(url.path)
    candidates = identities_for_path(path, profiles)
    if not candidates:
        return
    allowed = {(item.business_type, item.region) for item in candidates}
    if (request.business_type, request.region) in allowed:
        matching = [
            item
            for item in candidates
            if (item.business_type, item.region) == (request.business_type, request.region)
        ]
        if request.page_id and all(
            item.page_id and item.page_id != request.page_id for item in matching
        ):
            raise BusinessContextMismatch("页面路由与请求的 page_id 不一致")
        return
    # 一致性入口由车源、过户、一致性共用；这里只核对路由允许的候选。
    # 当前激活页签和关联面板由浏览器检查，报废置换仍按各自地区路径校验。
    expected = "、".join(sorted(f"{business}/{region}" for business, region in allowed))
    raise BusinessContextMismatch(f"页面路由只接受 {expected}，与请求业务或地区不一致")
