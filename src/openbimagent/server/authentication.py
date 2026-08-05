"""M2 pre-G7 provider-neutral 认证主体快照契约。

本模块不实现认证机制，不接收或保存 token、cookie、claims、issuer/subject 原文，
也不连接网络或中间件。正式认证 adapter 只可把已验证事实收敛为该不可变快照。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.orchestrator.actor import ActorRef, ActorType

M2_AUTHENTICATED_PRINCIPAL_PROTOCOL_VERSION = "0.1"


class M2ControlRole(StrEnum):
    """首个 M2 单用户模式的最小授权角色集合。"""

    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class M2AuthenticatedPrincipal(BaseModel):
    """认证 adapter 产出的无秘密、不可拆分身份与授权快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(
        default=M2_AUTHENTICATED_PRINCIPAL_PROTOCOL_VERSION,
        pattern=r"^0\.1$",
    )
    actor: ActorRef
    roles: tuple[M2ControlRole, ...] = Field(min_length=1, max_length=3)
    authentication_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_remote_principal(self) -> "M2AuthenticatedPrincipal":
        if self.actor.actor_type not in {ActorType.HUMAN, ActorType.SERVICE}:
            raise ValueError("远程认证主体类型只允许 human 或 service")
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("认证主体角色不得重复")
        return self


__all__ = [
    "M2_AUTHENTICATED_PRINCIPAL_PROTOCOL_VERSION",
    "M2AuthenticatedPrincipal",
    "M2ControlRole",
]
