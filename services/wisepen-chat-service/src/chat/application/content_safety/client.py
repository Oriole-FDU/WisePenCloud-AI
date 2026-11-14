from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from chat.core.config.app_settings import settings
from chat.core.config.settings_models import ContentSafetySettings
from chat.domain.entities import ChatMessage, Role
from chat.domain.interfaces.llm import TextCompletionProvider
from common.logger import warn

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_HIGH_SEVERITY_CATEGORIES = frozenset({"politics", "extremism", "minors"})

_SYSTEM_PROMPT = """你是内容安全审核模型。判断用户文本是否包含违规内容，仅输出一个 JSON 对象，不要输出任何其他文字。

分类（category 取值）：
- politics 涉政敏感：攻击国家制度、政党、领导人及现行政策；分裂国家、颠覆政权言论；领土/民族宗教议题的越线煽动；敏感历史事件、群体性事件的越线传播
- illegal 违法犯罪：制贩毒品、枪支、爆炸物等违禁品；诈骗、传销、非法集资、洗钱、黑灰产；赌博及导流；黑客入侵/攻击教程；买卖公民个人信息
- porn 色情低俗：露骨色情描写、性行为细节；色情资源/链接导流；软色情、低俗挑逗；卖淫嫖娼信息
- violence 暴力血腥：血腥、虐杀、肢解、虐待等暴力内容；宣扬暴力、报复；残害动物等极端画面描述
- extremism 恐怖极端：宣扬恐怖主义、极端主义思想；暴恐组织宣传、招募；制作爆炸物等教程
- hate 仇恨歧视：针对种族、民族、地域、宗教、性别、性取向、残障等的侮辱、攻击、煽动仇恨
- minors 未成年有害：涉及未成年人的色情、性暗示、诱导、欺凌、泄露未成年人信息
- other 其他违规：明显违反公序良俗或平台规范、但不属于上述类别的敏感内容

输出格式（严格 JSON）：
{"blocked": true|false, "category": "上述分类之一或 none", "reason": "不超过20字的中文说明"}

判定规则：
- 无违规内容时 blocked=false, category="none"
- 有违规时 blocked=true, category 取最贴切的一类
- 拿不准的边界情况一律判 blocked=false（宁放过、不误杀）"""

@dataclass(frozen=True)
class ContentSafetyVerdict:
    blocked: bool
    category: str = "none"
    reason: str = ""
    severity: str = "medium"


class SafetyClassifier(ABC):
    """内容安全判定通道"""

    config: ContentSafetySettings | None
    enabled: bool

    @abstractmethod
    async def check(self, text: str, kind: str = "text") -> ContentSafetyVerdict | None:
        """判定一段文本；未配置或任何异常时返回 None（放行）"""


class ContentSafetyClient(SafetyClassifier):
    """用通用对话模型做文本内容安全判定；未配置或任何异常时返回 None（放行）"""

    def __init__(
        self,
        provider: TextCompletionProvider,
        config: ContentSafetySettings | None = None,
    ) -> None:
        self._provider = provider
        self.config = config if config is not None else settings.content_safety
        self.enabled = bool(self.config and self.config.enabled)

    async def check(self, text: str, kind: str = "text") -> ContentSafetyVerdict | None:
        if not self.enabled:
            return None
        text = (text or "").strip()
        if not text:
            return None
        try:
            result = await asyncio.wait_for(
                self._provider.chat_completion(
                    messages=[
                        ChatMessage(
                            session_id="content-safety", role=Role.SYSTEM, content=_SYSTEM_PROMPT
                        ),
                        ChatMessage(
                            session_id="content-safety", role=Role.USER, content=text
                        ),
                    ],
                    model_name=self.config.model,
                    temperature=0,
                    api_base=self.config.base_url,
                    api_key=self.config.api_key,
                ),
                timeout=self.config.timeout_seconds,
            )
            return self._parse(result.content)
        except Exception as exc:  # noqa: BLE001
            warn("content safety check failed.", kind=kind, exc=exc)
            return None

    @staticmethod
    def _parse(content: str) -> ContentSafetyVerdict | None:
        text = (content or "").strip()
        if not text:
            return None
        # 容忍模型偶尔包一层 markdown 代码块
        match = _JSON_BLOCK_RE.search(text)
        if match:
            text = match.group(1).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 退化为提取首个 JSON 对象
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        if not isinstance(data, dict):
            return None
        blocked = bool(data.get("blocked"))
        if not blocked:
            return ContentSafetyVerdict(blocked=False)
        category = str(data.get("category") or "other").strip() or "other"
        reason = str(data.get("reason") or "").strip()[:100]
        severity = "high" if category in _HIGH_SEVERITY_CATEGORIES else "medium"
        return ContentSafetyVerdict(
            blocked=True, category=category, reason=reason, severity=severity
        )


# 决策模型的类别选项，key 与对话模型的 category 保持一致
_DECISION_CATEGORIES: dict[str, str] = {
    "politics": "涉政敏感：攻击国家制度、政党、领导人及现行政策；分裂国家、颠覆政权言论",
    "illegal": "违法犯罪：毒品、枪支、爆炸物等违禁品；诈骗、传销、非法集资、洗钱、赌博；黑客攻击教程",
    "porn": "色情低俗：露骨色情描写、性行为细节；色情资源导流；软色情；卖淫嫖娼信息",
    "violence": "暴力血腥：血腥、虐杀、肢解、虐待等暴力内容；宣扬暴力、报复",
    "extremism": "恐怖极端：宣扬恐怖主义、极端主义；暴恐组织宣传；制作爆炸物教程",
    "hate": "仇恨歧视：针对种族、民族、地域、宗教、性别、性取向、残障等的侮辱、攻击、煽动仇恨",
    "minors": "未成年有害：涉及未成年人的色情、性暗示、诱导、欺凌、泄露未成年人信息",
    "other": "其他违规：明显违反公序良俗或平台规范、但不属于上述类别的敏感内容",
    "none": "无违规内容",
}


class DecisionModelClient(SafetyClassifier):
    """用基于 SystemOne 协议的决策模型做判定
    一次前向直接返回违规概率与类别，不生成文本
    """

    def __init__(self, config: ContentSafetySettings | None = None) -> None:
        self.config = config if config is not None else settings.content_safety
        self.enabled = bool(self.config and self.config.enabled)
        timeout = self.config.timeout_seconds if self.config else 5.0
        self._endpoint = f"{(self.config.base_url if self.config else '').rstrip('/')}/systemone"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))

    async def check(self, text: str, kind: str = "text") -> ContentSafetyVerdict | None:
        if not self.enabled:
            return None
        text = (text or "").strip()
        if not text:
            return None
        try:
            payload = await asyncio.wait_for(
                self._request(text), timeout=self.config.timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001
            warn("content safety check failed.", kind=kind, exc=exc)
            return None
        return self._parse(payload)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, text: str) -> Any:
        response = await self._client.post(
            self._endpoint,
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            json={"model": self.config.model, "state": text, "questions": _decision_questions()},
        )
        response.raise_for_status()
        return response.json()

    def _parse(self, payload: Any) -> ContentSafetyVerdict | None:
        """把 answers.violation.noul 的概率按 block_threshold 映射成二值判定"""
        if not isinstance(payload, dict):
            return None
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            return None
        try:
            probability = float((answers.get("violation") or {}).get("noul"))
        except (TypeError, ValueError):
            return None
        if probability < self.config.block_threshold:
            return ContentSafetyVerdict(blocked=False)
        category = str((answers.get("category") or {}).get("choice") or "other")
        # 模型偶尔在判定违规的同时选 none，统一归到 other，避免污染命中统计
        if category not in _DECISION_CATEGORIES or category == "none":
            category = "other"
        severity = "high" if category in _HIGH_SEVERITY_CATEGORIES else "medium"
        return ContentSafetyVerdict(
            blocked=True,
            category=category,
            reason=f"noul={probability:.2f}",
            severity=severity,
        )


def _decision_questions() -> dict[str, Any]:
    """SystemOne 的 typed questions
    一个是非判断定违规，一个多选定类别
    """
    return {
        "violation": {
            "type": "noul",
            "instructions": "判断待判定文本是否包含违规内容",
            "criteria": {
                "true": "包含违规内容：涉政敏感、违法犯罪、色情低俗、暴力血腥、恐怖极端、仇恨歧视、未成年有害等",
                "false": "正常内容，不包含违规内容",
            },
        },
        "category": {
            "type": "choice",
            "instructions": "若文本包含违规内容，最贴近哪一类？无违规内容时选 none",
            "criteria": _DECISION_CATEGORIES,
        },
    }


def create_content_safety_client(
    provider: TextCompletionProvider,
    config: ContentSafetySettings | None = None,
) -> SafetyClassifier:
    """按 content-safety.mode 选择判定通道
    decision 走决策模型，chat 走通用对话模型
    """
    resolved = config if config is not None else settings.content_safety
    if resolved is not None and resolved.mode == "decision":
        return DecisionModelClient(resolved)
    return ContentSafetyClient(provider, resolved)
