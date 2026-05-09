const { chromium } = require('rebrowser-playwright');
const TurndownService = require('turndown');

// ---------------------------------------------------------------------------
// 配置常量
// ---------------------------------------------------------------------------
const NAVIGATION_TIMEOUT_MS = 60000;
const NETWORK_IDLE_TIMEOUT_MS = 8000;

const MAX_SCROLL_STEPS = 8;
const SCROLL_DELAY_MS = 700;
const POST_SCROLL_IDLE_MS = 1200;

const MIN_CONTENT_TEXT_LENGTH = 200;

const BLOCKED_RESOURCE_TYPES = new Set([
  'image',
  'media',
  'font',
  'stylesheet',
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

function writeStdout(content) {
  return new Promise((resolve, reject) => {
    process.stdout.write(content, error => {
      if (error) reject(error);
      else resolve();
    });
  });
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
      '[class*="banner"]',
      '[id*="banner"]',
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

// ---------------------------------------------------------------------------
// 主抓取逻辑
// ---------------------------------------------------------------------------
async function fetchUrl(url) {
  process.stderr.write(`Fetching: ${url}\n`);

  let browser;
  let context;

  try {
    const launchOptions = {
      headless: true,
      args: BROWSER_ARGS,
    };

    const proxy = envProxyConfig();
    if (proxy) {
      launchOptions.proxy = proxy;
    }

    browser = await chromium.launch(launchOptions);

    context = await browser.newContext({
      locale: 'zh-CN',
      timezoneId: 'Asia/Shanghai',
      viewport: {
        width: 1366 + Math.floor(Math.random() * 100),
        height: 768 + Math.floor(Math.random() * 100),
      },
    });

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
              ? Promise.resolve({ state: Notification.permission })
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
            loadTimes() { },
            csi() { },
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

    const page = await context.newPage();

    page.setDefaultTimeout(NAVIGATION_TIMEOUT_MS);
    page.setDefaultNavigationTimeout(NAVIGATION_TIMEOUT_MS);

    await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: NAVIGATION_TIMEOUT_MS,
    });

    await page
      .waitForLoadState('networkidle', {
        timeout: NETWORK_IDLE_TIMEOUT_MS,
      })
      .catch(() => {
        // 现代页面常有长连接、埋点、轮询。
        // networkidle 超时不代表失败。
      });

    await autoScroll(page);

    const pageData = await extractPageData(page);

    process.stderr.write(
      `Extracted text length: ${pageData.selectedTextLength}, fallbackToBody: ${pageData.usedBodyFallback}\n`
    );

    const markdown = normalizeMarkdown(
      turndownService.turndown(pageData.html)
    );

    if (!markdown) {
      throw new Error('No readable content');
    }

    await writeStdout(markdown);
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
      await context.close().catch(() => { });
    }

    if (browser) {
      await browser.close().catch(() => { });
    }
  }
}

// ---------------------------------------------------------------------------
// CLI 入口
// ---------------------------------------------------------------------------
async function main() {
  const url = process.argv[2];

  if (!url) {
    process.stderr.write('Usage: node local_web_fetcher.js <url>\n');
    process.exitCode = 1;
    return;
  }

  try {
    await fetchUrl(url);
  } catch (_) {
    process.exitCode = 1;
  }
}

main();
