from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

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

class ContentSafetyClient:
    """用独立小模型做文本内容安全判定；未配置或任何异常时返回 None（放行）"""

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
