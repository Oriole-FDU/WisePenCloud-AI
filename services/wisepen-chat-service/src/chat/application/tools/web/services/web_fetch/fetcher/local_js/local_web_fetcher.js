const { fetchUrl } = require('./local_web_fetcher_core');

async function main() {
  const url = process.argv[2];

  if (!url) {
    process.stderr.write('Usage: node local_web_fetcher.js <url>\n');
    process.exitCode = 1;
    return;
  }

  try {
    const markdown = await fetchUrl(url);

    if (!markdown || !markdown.trim()) {
      process.stderr.write('empty result\n');
      process.exitCode = 1;
      return;
    }

    process.stdout.write(markdown.trim());
  } catch (error) {
    process.stderr.write(`${error && error.stack ? error.stack : String(error)}\n`);
    process.exitCode = 1;
  }
}

main();
