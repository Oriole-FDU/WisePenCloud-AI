from typing import Optional

from playwright.async_api import Page

from chat.application.tools.browser.services.browser_interact.enums import InterventionSignalType
from chat.application.tools.browser.services.browser_interact.models import InterventionSignal

AUTH_PAGE_URL_INDICATORS = (
    "/login",
    "/signin",
    "/sign-in",
    "/auth",
    "/oauth",
    "/sso",
    "/passport",
    "/verify",
    "/captcha",
)

_INTERVENTION_SIGNAL_SCRIPT = r"""() => {
    const lower = value => String(value || '').toLowerCase();

    const captchaSelectors = [
        'iframe[src*="recaptcha"]',
        'iframe[src*="hcaptcha"]',
        'iframe[src*="turnstile"]',
        '[class*="captcha" i]',
        '[id*="captcha" i]',
        '[name*="captcha" i]',
        '.cf-turnstile',
        '[data-sitekey]'
    ];

    for (const selector of captchaSelectors) {
        if (document.querySelector(selector)) {
            return {
                type: 'captcha',
                confidence: 0.95,
                reason: `DOM matched CAPTCHA selector: ${selector}`,
                evidence: { selector }
            };
        }
    }

    const passwordInput = document.querySelector(
        'input[type="password"], input[name*="password" i], input[autocomplete="current-password"]'
    );
    if (passwordInput) {
        const form = passwordInput.closest('form');
        const submit = form
            ? form.querySelector('button, input[type="submit"], [role="button"]')
            : document.querySelector('button, input[type="submit"], [role="button"]');
        return {
            type: 'auth_page',
            confidence: 0.9,
            reason: 'DOM contains a password field.',
            evidence: {
                has_form: Boolean(form),
                submit_text: submit ? lower(submit.innerText || submit.value || submit.getAttribute('aria-label')) : ''
            }
        };
    }

    const bodyText = lower(document.body ? document.body.innerText : '');
    const verificationTerms = [
        'verify you are human',
        'verification code',
        'two-factor',
        'two factor',
        'multi-factor',
        'mfa code',
        'one-time code',
        'security code',
        '验证码',
        '人机验证',
        '二次验证'
    ];
    const matchedTerm = verificationTerms.find(term => bodyText.includes(term));
    if (matchedTerm) {
        return {
            type: 'auth_page',
            confidence: 0.8,
            reason: `Page text contains verification cue: ${matchedTerm}`,
            evidence: { matched_term: matchedTerm }
        };
    }

    return null;
}"""


class UserInterventionDetector:
    async def detect(self, page: Page) -> Optional[InterventionSignal]:
        """检测当前页面是否需要用户介入。

        Args:
            page: 当前浏览器页面。

        Returns:
            Optional[InterventionSignal]: 命中登录、验证或 CAPTCHA 等场景时返回信号。
        """
        url = page.url.lower()

        for indicator in AUTH_PAGE_URL_INDICATORS:
            if indicator in url:
                return InterventionSignal(
                    type=InterventionSignalType.AUTH_PAGE.value,
                    confidence=0.9,
                    reason=f"URL contains auth indicator: {indicator}",
                    evidence={"url": page.url},
                )

        try:
            signal = await page.evaluate(_INTERVENTION_SIGNAL_SCRIPT)
        except Exception:
            return None

        if not isinstance(signal, dict):
            return None

        signal_type = signal.get("type")
        if signal_type not in {
            InterventionSignalType.AUTH_PAGE.value,
            InterventionSignalType.CAPTCHA.value,
        }:
            return None

        evidence = signal.get("evidence")
        return InterventionSignal(
            type=signal_type,
            confidence=float(signal.get("confidence") or 0.8),
            reason=str(signal.get("reason") or "User intervention required."),
            evidence=evidence if isinstance(evidence, dict) else {},
        )

        return None
