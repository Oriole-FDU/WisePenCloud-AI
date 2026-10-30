"""Nacos 策略配置边界：校验完整快照，只发布有效更新。"""

from collections.abc import Collection
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from common.cloud.nacos_client import NacosClientManager
from common.logger import error, info
from scholar.domain.models.fields import ScholarPolicies
from scholar.domain.models.fields import AcademicField


@dataclass
class ScholarPolicyStore:
    """保存当前策略；用例读取一次，整体替换不会影响正在执行的请求。"""

    current: ScholarPolicies


class PolicySettings(BaseModel):
    """只读取策略配置；同一 YAML 中其他配置的热更新仍由各自生命周期决定。"""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    SCHOLAR_POLICIES: ScholarPolicies

    @field_validator("SCHOLAR_POLICIES", mode="before")
    @classmethod
    def parse_local_yaml(cls, value):
        # 本地环境变量是文本，Nacos YAML 的同名字段已经是映射。
        return yaml.safe_load(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def complete_fields(self):
        missing = set(AcademicField) - self.SCHOLAR_POLICIES.fields.keys()
        if missing:
            raise ValueError(
                "SCHOLAR_POLICIES.fields missing: "
                + ", ".join(sorted(field.value for field in missing))
            )
        return self


def validate_policy_sources(
    policies: ScholarPolicies,
    *,
    search_sources: Collection[str],
    lookup_sources: Collection[str],
    discovery_sources: Collection[str],
) -> None:
    """仅在配置接入处检查来源注册；不检查各领域是否应当具备发现能力。"""
    if any(source not in lookup_sources for source in policies.common_enrichers):
        raise ValueError("SCHOLAR_POLICIES.common_enrichers contains an unregistered source")
    for field, policy in policies.fields.items():
        for name, sources, registered in (
            ("searchers", policy.searchers, search_sources),
            ("field_enrichers", policy.field_enrichers, lookup_sources),
        ):
            if any(source not in registered for source in sources):
                raise ValueError(f"SCHOLAR_POLICIES.fields.{field}.{name} contains an unregistered source")
        if policy.discoverer is not None and policy.discoverer not in discovery_sources:
            raise ValueError(f"SCHOLAR_POLICIES.fields.{field}.discoverer contains an unregistered source")


@asynccontextmanager
async def watch_policies(
    store: ScholarPolicyStore,
    manager: NacosClientManager,
    *,
    search_sources: Collection[str],
    lookup_sources: Collection[str],
    discovery_sources: Collection[str],
):
    """在服务事件循环内同步初始策略并订阅更新；退出或启动失败都释放客户端。"""

    def parse(content: str) -> ScholarPolicies:
        policies = PolicySettings.model_validate(yaml.safe_load(content)).SCHOLAR_POLICIES
        validate_policy_sources(
            policies,
            search_sources=search_sources,
            lookup_sources=lookup_sources,
            discovery_sources=discovery_sources,
        )
        return policies

    async def update(namespace: str, group: str, data_id: str, content: str):
        try:
            policies = parse(content)
        except yaml.YAMLError:
            # YAML 异常含原文片段，同一文件可能带凭证，因此只记录错误类型。
            error("scholar policy update rejected: invalid YAML.", data_id=data_id)
            return
        except ValueError as exc:
            # 边界校验失败明确可见；Pydantic 已关闭错误中的输入回显。
            error("scholar policy update rejected.", data_id=data_id, reason=str(exc))
            return
        # 回调内没有 await；完整快照一次替换，已开始的请求仍持有旧快照。
        store.current = policies
        info("scholar policies updated.", data_id=data_id)

    async with AsyncExitStack() as lifecycle:
        lifecycle.push_async_callback(manager.close_config)
        # 先同步再订阅；SDK 会检测初始拉取之后发生的配置变更。
        store.current = parse(await manager.pull_config())
        await manager.watch_config(update)
        lifecycle.push_async_callback(manager.unwatch_config, update)
        yield
