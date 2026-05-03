const { chromium } = require('rebrowser-playwright');
const TurndownService = require('turndown');

// ---------------------------------------------------------------------------
// 配置常量
// ---------------------------------------------------------------------------
const NAVIGATION_TIMEOUT_MS = 60000;
const SCROLL_STEPS = 3;
const SCROLL_DELAY_MS = 1000;
const POST_SCROLL_IDLE_MS = 2000;

const BROWSER_ARGS = [
  '--proxy-server=direct://',
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

// ---------------------------------------------------------------------------
// 主逻辑
// ---------------------------------------------------------------------------
const turndownService = new TurndownService({
  headingStyle: 'atx',
  codeBlockStyle: 'fenced',
});

turndownService.addRule('ignore-base64-images', {
  filter: node => node.nodeName === 'IMG' && node.getAttribute('src')?.startsWith('data:image/'),
  replacement: () => '',
});

async function fetchUrl(url) {
  process.stderr.write(`Fetching: ${url}\n`);
  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      args: BROWSER_ARGS,
    });

    const context = await browser.newContext({
      locale: 'zh-CN',
      timezoneId: 'Asia/Shanghai',
      viewport: {
        width: 1366 + Math.floor(Math.random() * 100),
        height: 768 + Math.floor(Math.random() * 100),
      },
    });

    const page = await context.newPage();
    page.setDefaultTimeout(NAVIGATION_TIMEOUT_MS);
    page.setDefaultNavigationTimeout(NAVIGATION_TIMEOUT_MS);

    await page.addInitScript(() => {
      const newProto = navigator.__proto__;
      delete newProto.webdriver;
      navigator.__proto__ = newProto;

      const originalQuery = window.navigator.permissions.query;
      window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : originalQuery(parameters)
      );

      if (!window.chrome) {
        window.chrome = {
          runtime: {},
          loadTimes: function () {},
          csi: function () {},
          app: {},
        };
      }

      if (window.__pwInitScripts !== undefined) {
        delete window.__pwInitScripts;
      }
      if (window.__playwright__binding__ !== undefined) {
        delete window.__playwright__binding__;
      }
    });

    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: NAVIGATION_TIMEOUT_MS });

    for (let step = 0; step < SCROLL_STEPS; step++) {
      await page.evaluate(() => {
        const scrollY = Math.floor(window.innerHeight * (0.8 + Math.random() * 0.4));
        window.scrollBy({ top: scrollY, behavior: 'smooth' });
      });
      await page.waitForTimeout(SCROLL_DELAY_MS + Math.floor(Math.random() * 500));
    }
    await page.waitForTimeout(POST_SCROLL_IDLE_MS);

    const pageData = await page.evaluate(() => {
      document.querySelectorAll(
        'script,style,noscript,iframe,ad,.ads,#ads,img[src^="data:image/"]'
      ).forEach(element => element.remove());
      return {
        title: document.title,
        html: document.body.innerHTML,
      };
    });

    const markdown = turndownService.turndown(pageData.html);
    if (!markdown || !markdown.trim()) {
      process.stderr.write('No readable content\n');
      process.exit(1);
    }

    process.stdout.write(markdown);
    process.stdout.end();
    await new Promise(resolve => setTimeout(resolve, 100));
  } catch (error) {
    process.stderr.write(`Error: ${error.message}\n`);
    try {
      if (browser && browser.contexts().length > 0) {
        const errorPage = browser.contexts()[0].pages()[0];
        if (errorPage) {
          const screenshot = await errorPage.screenshot({ type: 'jpeg', quality: 30 });
          process.stderr.write(`[screenshot] ${screenshot.toString('base64').slice(0, 200)}...\n`);
        }
      }
    } catch (screenshotError) {
      // best-effort
    }
    process.exit(1);
  } finally {
    if (browser) await browser.close();
  }
}

const url = process.argv[2];
if (!url) {
  process.stderr.write('Usage: node local_web_fetcher.js <url>\n');
  process.exit(1);
}
fetchUrl(url);
