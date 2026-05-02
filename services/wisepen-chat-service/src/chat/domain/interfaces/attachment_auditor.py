from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AttachmentAuditResult:
    """附件审计结果"""

    passed: bool
    reason: str = ""


class AttachmentAuditor(ABC):
    """附件审计器接口"""

    @abstractmethod
    async def audit(
        self,
        object_key: str,
        extension: str,
        extracted_text: str = "",
    ) -> AttachmentAuditResult:
        pass
