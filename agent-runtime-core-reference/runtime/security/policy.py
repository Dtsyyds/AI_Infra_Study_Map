"""教学身份与静态策略；身份只来自验证过的 API 凭据。"""

import hmac

from config.settings import Settings, settings
from core.contracts.identity import Principal
from core.contracts.policy import AdmissionPolicy
from core.errors import RuntimeFault

REVIEW_TOOLS = frozenset(
    {"document.fetch", "document.check", "report.render", "report.publish"}
)


def principal_from_token(token: str, config: Settings | None = None) -> Principal:
    config = config or settings
    identities = (
        (config.user_token.get_secret_value(), "demo-reviewer", "reviewer"),
        (config.restricted_token.get_secret_value(), "demo-restricted", "restricted"),
        (config.ops_token.get_secret_value(), "demo-operator", "operator"),
    )
    # 总是比较全部演示凭据；不把调用者传入的 principal/role 当成身份。
    matched: Principal | None = None
    for expected, principal_id, role in identities:
        if hmac.compare_digest(token.encode(), expected.encode()):
            matched = Principal(principal_id=principal_id, role=role)
    if matched is None:
        raise RuntimeFault("UNAUTHENTICATED", "invalid local demo credential")
    return matched


def authenticate(authorization: str | None) -> Principal:
    if authorization is None or not authorization.startswith("Bearer "):
        raise RuntimeFault("UNAUTHENTICATED", "Bearer credential required")
    return principal_from_token(authorization.removeprefix("Bearer "))


def authorize(principal: Principal, tool: str, task_type: str) -> None:
    if not (
        principal.principal_id == "demo-reviewer"
        and principal.role == "reviewer"
        and principal.scope == "fictional-procurement"
        and ((task_type == "document_review" and tool in REVIEW_TOOLS)
             or (task_type == "code_repair" and tool in {"repair.baseline", "repair.generate", "repair.verify", "repair.report"}))
    ):
        raise RuntimeFault("TOOL_FORBIDDEN", "static tool policy denied dispatch")


def authorize_reconcile(principal: Principal) -> None:
    if principal.principal_id != "demo-operator" or principal.role != "operator":
        raise RuntimeFault("OPERATOR_REQUIRED", "independent operator identity required")


def admission_policy(principal: Principal) -> AdmissionPolicy:
    if principal.role == "operator":
        raise RuntimeFault("ADMISSION_FORBIDDEN", "operator may only reconcile")
    return AdmissionPolicy(active_limit=3, rate_per_minute=30)
