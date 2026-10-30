"""应用用例的策略读取契约；配置来源和热更新由实现方负责。"""

from typing import Protocol

from scholar.domain.models.fields import ScholarPolicies


class ScholarPolicyProvider(Protocol):
    @property
    def current(self) -> ScholarPolicies:
        """获取完整快照；单次用例持有该快照直到执行完成。"""
        ...
