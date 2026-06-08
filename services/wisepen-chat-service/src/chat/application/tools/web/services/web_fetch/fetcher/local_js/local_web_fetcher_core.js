const {chromium} = require('rebrowser-playwright');
const TurndownService = require('turndown');
const {Readability} = require('@mozilla/readability');
const {JSDOM} = require('jsdom');

// ---------------------------------------------------------------------------
// 配置常量
// ---------------------------------------------------------------------------
const NAVIGATION_TIMEOUT_MS = 60000;
const NETWORK_IDLE_TIMEOUT_MS = 8000;

const MAX_SCROLL_STEPS = 8;
const SCROLL_DELAY_MS = 700;
const POST_SCROLL_IDLE_MS = 1200;

const MIN_CONTENT_TEXT_LENGTH = 200;

const TEXT_STABILITY_INTERVAL_MS = 500;
const TEXT_STABILITY_STABLE_ROUNDS = 3;
const TEXT_STABILITY_TIMEOUT_MS = 8000;
const MAX_DISCOVERED_LINKS = 400;

const BLOCKED_RESOURCE_TYPES = new Set([
    'image',
    'media',
    'font',
]);

const BROWSER_ARGS = [
    '--disable-blink-features=AutomationControlled',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--disable-infobars',
    '--disable-background-networking',
    '--disable-component-extensions-with-background-pages',
    '--disable-default-apps',
    '--disable-extensions-http-throttling',
    '--disable-sync',
    '--disable-translate',
    '--metrics-recording-only',
    '--mute-audio',
    '--no-first-run',
    '--safebrowsing-disable-auto-update',
    '--lang=zh-CN',
];

const PROXY_ENV_KEYS = [
    'HTTPS_PROXY',
    'https_proxy',
    'HTTP_PROXY',
    'http_proxy',
    'ALL_PROXY',
    'all_proxy',
];

const NO_PROXY_ENV_KEYS = ['NO_PROXY', 'no_proxy'];

// ---------------------------------------------------------------------------
// Markdown 转换器
// ---------------------------------------------------------------------------
const turndownService = new TurndownService({
    headingStyle: 'atx',
    codeBlockStyle: 'fenced',
    bulletListMarker: '-',
});

turndownService.addRule('ignore-base64-images', {
    filter: node =>
        node.nodeName === 'IMG' &&
        node.getAttribute('src')?.startsWith('data:image/'),
    replacement: () => '',
});

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function normalizeMarkdown(markdown) {
    return markdown
        .replace(/\r\n/g, '\n')
        .replace(/[ \t]+\n/g, '\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

function envProxyConfig() {
    const proxyValue = PROXY_ENV_KEYS
        .map(key => process.env[key])
        .find(value => value && value.trim());

    if (!proxyValue) return undefined;

    const raw = proxyValue.includes('://') ? proxyValue : `http://${proxyValue}`;
    let parsed;

    try {
        parsed = new URL(raw);
    } catch {
        return undefined;
    }

    if (!parsed.hostname) return undefined;

    const proxy = {
        server: `${parsed.protocol}//${parsed.hostname}${parsed.port ? `:${parsed.port}` : ''}`,
    };

    if (parsed.username) proxy.username = decodeURIComponent(parsed.username);
    if (parsed.password) proxy.password = decodeURIComponent(parsed.password);

    const bypass = NO_PROXY_ENV_KEYS
        .map(key => process.env[key])
        .filter(Boolean)
        .join(',');

    if (bypass) proxy.bypass = bypass;

    return proxy;
}

class BrowserRuntime {
    constructor() {
        this.browser = null;
        this.launchPromise = null;
        this.launchCount = 0;
    }

    async getBrowser() {
        if (this.browser && this.browser.isConnected()) {
            return this.browser;
        }

        if (!this.launchPromise) {
            this.launchPromise = this._launchBrowser();
        }

        try {
            return await this.launchPromise;
        } finally {
            this.launchPromise = null;
        }
    }

    async newContext(options) {
        const browser = await this.getBrowser();

        try {
            return await browser.newContext(options);
        } catch (error) {
            if (this._isBrowserDisconnected(error)) {
                process.stderr.write('Browser disconnected while creating context, restarting\n');
                const browser = await this.restart();
                return browser.newContext(options);
            }

            throw error;
        }
    }

    async restart() {
        await this.close();
        return this.getBrowser();
    }

    async close() {
        const launchPromise = this.launchPromise;
        this.launchPromise = null;

        if (launchPromise) {
            await launchPromise.catch(() => null);
        }

        const browser = this.browser;
        this.browser = null;

        if (browser) {
            await browser.close().catch(() => {
            });
        }
    }

    async _launchBrowser() {
        const launchOptions = {
            headless: true,
            args: BROWSER_ARGS,
        };

        const proxy = envProxyConfig();
        if (proxy) {
            launchOptions.proxy = proxy;
        }

        const browser = await chromium.launch(launchOptions);
        this.browser = browser;
        this.launchCount += 1;

        browser.on('disconnected', () => {
            if (this.browser === browser) {
                this.browser = null;
            }
            process.stderr.write('Browser disconnected\n');
        });

        process.stderr.write(`Browser launched, count: ${this.launchCount}\n`);
        return browser;
    }

    _isBrowserDisconnected(error) {
        const message = error && error.message ? error.message : String(error);
        return /browser.*(closed|disconnected)|target.*closed/i.test(message);
    }
}

async function setupContext(context) {
    await context.route('**/*', route => {
        const resourceType = route.request().resourceType();

        if (BLOCKED_RESOURCE_TYPES.has(resourceType)) {
            return route.abort();
        }

        return route.continue();
    });

    await context.addInitScript(() => {
        try {
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
                configurable: true,
            });
        } catch (_) {
            // best-effort
        }

        try {
            const permissions = window.navigator.permissions;
            const originalQuery = permissions?.query;

            if (originalQuery) {
                permissions.query = function query(parameters) {
                    return parameters?.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : originalQuery.call(this, parameters);
                };
            }
        } catch (_) {
            // best-effort
        }

        try {
            if (!window.chrome) {
                window.chrome = {
                    runtime: {},
                    loadTimes() {
                    },
                    csi() {
                    },
                    app: {},
                };
            }
        } catch (_) {
            // best-effort
        }

        try {
            delete window.__pwInitScripts;
            delete window.__playwright__binding__;
        } catch (_) {
            // best-effort
        }
    });
}

async function autoScroll(page) {
    let previousHeight = 0;
    let stableCount = 0;

    for (let step = 0; step < MAX_SCROLL_STEPS; step++) {
        const state = await page.evaluate(() => {
            const documentElement = document.documentElement;
            const body = document.body;

            const scrollHeight = Math.max(
                documentElement?.scrollHeight || 0,
                body?.scrollHeight || 0
            );

            const viewportHeight = window.innerHeight || 768;
            const currentY = window.scrollY || window.pageYOffset || 0;

            const nearBottom =
                currentY + viewportHeight >= scrollHeight - 16;

            if (!nearBottom) {
                const delta = Math.floor(viewportHeight * (0.85 + Math.random() * 0.25));
                window.scrollBy({
                    top: delta,
                    behavior: 'auto',
                });
            }

            return {
                scrollHeight,
                currentY,
                nearBottom,
            };
        });

        if (state.scrollHeight === previousHeight) {
            stableCount += 1;
        } else {
            stableCount = 0;
            previousHeight = state.scrollHeight;
        }

        if (state.nearBottom && stableCount >= 1) {
            break;
        }

        await sleep(SCROLL_DELAY_MS + Math.floor(Math.random() * 300));
    }

    await sleep(POST_SCROLL_IDLE_MS);
}

async function extractLinkCandidates(page) {
    return page.evaluate((maxLinks) => {
        const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
        const candidates = [];
        const seen = new Set();
        const elements = Array.from(document.querySelectorAll('a[href], area[href]'));

        for (const element of elements) {
            if (candidates.length >= maxLinks) break;

            const rawHref = element.getAttribute('href');
            if (!rawHref || !rawHref.trim()) continue;

            let absoluteUrl = '';
            try {
                absoluteUrl = new URL(rawHref, document.baseURI || window.location.href).href;
            } catch (_) {
                continue;
            }

            if (seen.has(absoluteUrl)) continue;
            seen.add(absoluteUrl);

            const anchorText = normalize(element.innerText || element.textContent || element.getAttribute('aria-label') || '');
            const contextElement =
                element.closest('p,li,article,section,main,nav,header,footer,td,th,div') ||
                element.parentElement;
            const surroundingText = normalize(contextElement?.innerText || contextElement?.textContent || '').slice(0, 500);

            candidates.push({
                url: absoluteUrl,
                anchorText,
                surroundingText,
            });
        }

        return candidates;
    }, MAX_DISCOVERED_LINKS).catch(error => {
        process.stderr.write(`Link extraction failed: ${error.message}\n`);
        return [];
    });
}

async function waitForTextStability(page) {
    const startedAt = Date.now();
    let previousLength = -1;
    let stableRounds = 0;

    while (Date.now() - startedAt < TEXT_STABILITY_TIMEOUT_MS) {
        const length = await page.evaluate(() => {
            const text = document.body?.innerText || '';
            return text.replace(/\s+/g, '').length;
        }).catch(() => 0);

        if (length >= MIN_CONTENT_TEXT_LENGTH && length === previousLength) {
            stableRounds += 1;
        } else {
            stableRounds = 0;
            previousLength = length;
        }

        if (stableRounds >= TEXT_STABILITY_STABLE_ROUNDS) {
            process.stderr.write(`Text stable length: ${length}\n`);
            return;
        }

        await sleep(TEXT_STABILITY_INTERVAL_MS);
    }

    process.stderr.write(`Text stability timeout, lastLength: ${previousLength}\n`);
}

async function extractPageData(page) {
    return page.evaluate((minTextLength) => {
        const removeSelectors = [
            'script',
            'style',
            'noscript',
            'iframe',
            'template',
            'canvas',
            'svg',
            'ad',
            '.ad',
            '.ads',
            '#ad',
            '#ads',
            '[class*="advert"]',
            '[id*="advert"]',
            'img[src^="data:image/"]',
        ];

        document
            .querySelectorAll(removeSelectors.join(','))
            .forEach(element => element.remove());

        const contentSelectors = [
            'article',
            'main',
            '[role="main"]',
            '.article',
            '.post',
            '.entry-content',
            '.post-content',
            '.article-content',
            '.content',
            '#content',
            '#main',
        ];

        let bestElement = null;
        let bestTextLength = 0;

        for (const selector of contentSelectors) {
            const elements = Array.from(document.querySelectorAll(selector));

            for (const element of elements) {
                const textLength = element.innerText
                    ? element.innerText.trim().length
                    : 0;

                if (textLength > bestTextLength) {
                    bestElement = element;
                    bestTextLength = textLength;
                }
            }
        }

        const body = document.body;
        const selectedElement =
            bestElement && bestTextLength >= minTextLength
                ? bestElement
                : body;

        return {
            title: document.title || '',
            html: selectedElement ? selectedElement.innerHTML : '',
            usedBodyFallback: selectedElement === body,
            selectedTextLength: selectedElement?.innerText?.trim().length || 0,
        };
    }, MIN_CONTENT_TEXT_LENGTH);
}

async function extractReadabilityData(page) {
    try {
        const html = await page.content();
        const dom = new JSDOM(html, {
            url: page.url(),
        });

        const article = new Readability(dom.window.document).parse();
        if (!article || !article.content) {
            return null;
        }

        const textLength = article.textContent
            ? article.textContent.trim().length
            : 0;

        if (textLength < MIN_CONTENT_TEXT_LENGTH) {
            return null;
        }

        return {
            title: article.title || '',
            html: article.content,
            usedBodyFallback: false,
            selectedTextLength: textLength,
            extractor: 'readability',
        };
    } catch (error) {
        process.stderr.write(`Readability failed: ${error.message}\n`);
        return null;
    }
}

function scoreCandidate(candidate) {
    if (!candidate) return -1;

    let score = candidate.selectedTextLength || 0;

    if (candidate.usedBodyFallback) {
        score -= 500;
    }

    if (candidate.extractor === 'readability') {
        score += 150;
    }

    return score;
}

function chooseBestPageData(candidates) {
    return candidates
        .filter(Boolean)
        .sort((a, b) => scoreCandidate(b) - scoreCandidate(a))[0] || null;
}

// ---------------------------------------------------------------------------
// 主抓取逻辑
// ---------------------------------------------------------------------------
async function fetchPage(url, options = {}) {
    process.stderr.write(`Fetching: ${url}\n`);

    let ownedRuntime;
    const runtime = options.runtime || new BrowserRuntime();
    let context;

    if (!options.runtime) {
        ownedRuntime = runtime;
    }

    try {
        context = await runtime.newContext({
            locale: 'zh-CN',
            timezoneId: 'Asia/Shanghai',
            viewport: {
                width: 1366 + Math.floor(Math.random() * 100),
                height: 768 + Math.floor(Math.random() * 100),
            },
        });

        await setupContext(context);

        const page = await context.newPage();

        page.setDefaultTimeout(NAVIGATION_TIMEOUT_MS);
        page.setDefaultNavigationTimeout(NAVIGATION_TIMEOUT_MS);

        const response = await page.goto(url, {
            waitUntil: 'domcontentloaded',
            timeout: NAVIGATION_TIMEOUT_MS,
        });

        const statusCode = response ? response.status() : null;
        const finalUrl = page.url();
        process.stderr.write(`StatusCode: ${statusCode}, finalUrl: ${finalUrl}\n`);

        if (statusCode && statusCode >= 400) {
            throw new Error(`HTTP ${statusCode}`);
        }

        await page
            .waitForLoadState('networkidle', {
                timeout: NETWORK_IDLE_TIMEOUT_MS,
            })
            .catch(() => {
                process.stderr.write('networkidle timeout ignored\n');
            });

        await waitForTextStability(page);
        await autoScroll(page);
        await waitForTextStability(page);

        const links = await extractLinkCandidates(page);
        const selectorPageData = await extractPageData(page);
        const readabilityPageData = await extractReadabilityData(page);

        const pageData = chooseBestPageData([
            readabilityPageData,
            {
                ...selectorPageData,
                extractor: 'selector',
            },
        ]);

        if (!pageData) {
            throw new Error('No readable content');
        }

        process.stderr.write(
            `Extractor: ${pageData.extractor}, textLength: ${pageData.selectedTextLength}, fallbackToBody: ${pageData.usedBodyFallback}\n`
        );

        const bodyMarkdown = normalizeMarkdown(
            turndownService.turndown(pageData.html)
        );

        const title = normalizeMarkdown(pageData.title || '');
        const markdown = normalizeMarkdown(
            title ? `# ${title}\n\n${bodyMarkdown}` : bodyMarkdown
        );

        if (!markdown) {
            throw new Error('No readable content');
        }

        process.stderr.write(`markdownLength: ${markdown.length}\n`);

        return {
            markdown,
            links,
            title,
            finalUrl,
            statusCode,
        };
    } catch (error) {
        process.stderr.write(`Error: ${error.message}\n`);

        try {
            const errorPage = context?.pages?.()[0];

            if (errorPage) {
                const screenshot = await errorPage.screenshot({
                    type: 'jpeg',
                    quality: 30,
                    fullPage: false,
                });

                process.stderr.write(
                    `[screenshot] ${screenshot.toString('base64').slice(0, 200)}...\n`
                );
            }
        } catch (_) {
            // best-effort
        }

        throw error;
    } finally {
        if (context) {
            await context.close().catch(() => {
            });
        }

        if (ownedRuntime) {
            await ownedRuntime.close();
        }
    }
}

async function fetchUrl(url, options = {}) {
    const result = await fetchPage(url, options);
    return result.markdown;
}

module.exports = {
    BrowserRuntime,
    fetchPage,
    fetchUrl,
};
