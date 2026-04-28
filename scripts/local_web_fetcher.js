const { chromium } = require('playwright-extra');
const stealthPlugin = require('puppeteer-extra-plugin-stealth');
const TurndownService = require('turndown');

chromium.use(stealthPlugin());
const turndownService = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced' });
turndownService.addRule('ignore-base64-images', {
  filter: node => node.nodeName === 'IMG' && node.getAttribute('src')?.startsWith('data:image/'),
  replacement: () => ''
});

async function fetchUrl(url) {
  let browser;
  try {
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox'] });
    const page = await browser.newPage();
    page.setDefaultTimeout(60000);
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });

    for (let i = 0; i < 3; i++) {
      await page.evaluate(() => window.scrollBy(0, window.innerHeight));
      await page.waitForTimeout(1000);
    }
    await page.waitForTimeout(2000);

    const pageData = await page.evaluate(() => {
      document.querySelectorAll(
        'script,style,noscript,iframe,ad,.ads,#ads,img[src^="data:image/"]'
      ).forEach(el => el.remove());
      return { title: document.title, html: document.body.innerHTML };
    });
    const markdown = turndownService.turndown(pageData.html);
    if (!markdown || !markdown.trim()) {
      process.stderr.write('No readable content\n');
      process.exit(1);
    }
    
    process.stdout.write(markdown);
    process.stdout.end();
    await new Promise(resolve => setTimeout(resolve, 100));
  } catch (e) {
    process.stderr.write(`Error: ${e.message}\n`);
    process.exit(1);
  } finally {
    if (browser) await browser.close();
  }
}

const url = process.argv[2];
if (!url) {
  process.stderr.write('Usage: node fetch.js <url>\n');
  process.exit(1);
}
fetchUrl(url);
