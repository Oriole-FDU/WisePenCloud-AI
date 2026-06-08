from typing import Any, Dict, Optional, Tuple

from playwright.async_api import ElementHandle

from chat.application.tools.browser.services.browser_interact.enums import (
    InterventionSignalType,
)
from chat.application.tools.browser.services.browser_interact.models import (
    InterventionSignal,
)

_HIGH_RISK_ACTION_TERMS = (
    "delete",
    "remove",
    "destroy",
    "purchase",
    "buy now",
    "pay",
    "checkout",
    "place order",
    "transfer",
    "withdraw",
    "refund",
    "cancel subscription",
    "unsubscribe",
    "删除",
    "移除",
    "销毁",
    "支付",
    "付款",
    "购买",
    "结账",
    "下单",
    "转账",
    "提现",
    "退款",
    "取消订阅",
    "退订",
)

_SECRET_FIELD_TERMS = (
    "password",
    "passcode",
    "passwd",
    "pwd",
    "one-time",
    "otp",
    "totp",
    "mfa",
    "2fa",
    "verification code",
    "security code",
    "token",
    "secret",
    "api key",
    "apikey",
    "验证码",
    "动态码",
    "一次性密码",
    "密码",
    "密钥",
)


def _compact_text(value: Any) -> str:
    """把页面属性压缩成适合策略匹配的短文本。"""
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().lower().split())


async def _read_element_policy_data(target: ElementHandle) -> Dict[str, str]:
    """读取用于动作策略判断的 DOM 元数据。

    Args:
        target: 当前 ref 解析出来的 Playwright ElementHandle。

    Returns:
        Dict[str, str]: 已规范化的标签、属性、可见文本和表单上下文。
    """
    data = await target.evaluate(
        """el => {
            const attr = name => (el.getAttribute && el.getAttribute(name)) || '';
            const form = el.closest && el.closest('form');
            const formText = form ? (form.innerText || form.textContent || '') : '';
            const ownText = el.innerText || el.textContent || '';
            return {
                tag: (el.tagName || '').toLowerCase(),
                role: attr('role'),
                type: attr('type'),
                name: attr('name'),
                id: attr('id'),
                ariaLabel: attr('aria-label'),
                title: attr('title'),
                value: el.value || attr('value'),
                autocomplete: attr('autocomplete'),
                href: attr('href'),
                text: ownText,
                formText: formText,
                formAction: form ? (form.getAttribute('action') || '') : ''
            };
        }"""
    )

    return {key: _compact_text(value) for key, value in data.items()}


def _contains_term(text: str, terms: Tuple[str, ...]) -> Optional[str]:
    """返回命中的第一个策略词，未命中时返回 None。"""
    return next((term for term in terms if term in text), None)


async def detect_high_risk_click(target: ElementHandle) -> Optional[InterventionSignal]:
    """判断点击动作是否可能触发不可逆或付费类行为。

    Args:
        target: 即将点击的 ref 目标。

    Returns:
        Optional[InterventionSignal]: 需要用户介入时返回信号，否则返回 None。
    """
    try:
        data = await _read_element_policy_data(target)
    except Exception:
        return None

    # 只用触发控件自身和最近表单上下文判断，避免整页普通文本导致误拦截。
    action_text = " ".join(
        filter(
            None,
            (
                data.get("role"),
                data.get("type"),
                data.get("name"),
                data.get("id"),
                data.get("ariaLabel"),
                data.get("title"),
                data.get("value"),
                data.get("href"),
                data.get("text"),
                data.get("formAction"),
            ),
        )
    )
    matched_term = _contains_term(action_text, _HIGH_RISK_ACTION_TERMS)
    if not matched_term:
        return None

    return InterventionSignal(
        type=InterventionSignalType.HIGH_RISK_ACTION.value,
        confidence=0.86,
        reason=f"Click target matched high-risk action term: {matched_term}",
        evidence={"matched_term": matched_term},
    )


async def detect_secret_fill(target: ElementHandle) -> Optional[InterventionSignal]:
    """判断 fill_ref 是否试图写入密码、验证码或其他敏感凭据字段。

    Args:
        target: 即将填充的 ref 目标。

    Returns:
        Optional[InterventionSignal]: 需要用户手动输入敏感信息时返回信号，否则返回 None。
    """
    try:
        data = await _read_element_policy_data(target)
    except Exception:
        return None

    field_text = " ".join(
        filter(
            None,
            (
                data.get("type"),
                data.get("name"),
                data.get("id"),
                data.get("ariaLabel"),
                data.get("title"),
                data.get("autocomplete"),
                data.get("text"),
                data.get("formText"),
            ),
        )
    )

    if data.get("type") == "password":
        matched_term = "password"
    else:
        matched_term = _contains_term(field_text, _SECRET_FIELD_TERMS)

    if not matched_term:
        return None

    return InterventionSignal(
        type=InterventionSignalType.SECRET_INPUT.value,
        confidence=0.92,
        reason=f"Fill target matched sensitive credential field term: {matched_term}",
        evidence={"matched_term": matched_term},
    )
