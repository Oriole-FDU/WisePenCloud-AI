const readline = require('node:readline');
const {BrowserRuntime, fetchPage} = require('./local_web_fetcher_core');

const DEFAULT_CONCURRENCY = 2;
const DEFAULT_RESTART_AFTER = 200;
const DEFAULT_CONTEXT_RESTART_AFTER = 75;
const MAX_ERROR_LENGTH = 500;

const runtime = new BrowserRuntime();

const concurrency = readPositiveInt(
    process.env.WEB_FETCH_JS_WORKER_CONCURRENCY,
    DEFAULT_CONCURRENCY
);
const restartAfter = readPositiveInt(
    process.env.WEB_FETCH_JS_BROWSER_RESTART_AFTER,
    DEFAULT_RESTART_AFTER
);
const contextRestartAfter = readPositiveInt(
    process.env.WEB_FETCH_JS_CONTEXT_RESTART_AFTER,
    DEFAULT_CONTEXT_RESTART_AFTER
);

let activeCount = 0;
let closed = false;
let processedCount = 0;
let contextCount = 0;
let pendingRuntimeRestart = false;
let draining = false;
const pending = [];

const rl = readline.createInterface({
    input: process.stdin,
    terminal: false,
});

function writeResponse(response) {
    process.stdout.write(`${JSON.stringify(response)}\n`);
}

function readPositiveInt(value, fallback) {
    const parsed = Number.parseInt(value || '', 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

async function handleLine(line) {
    let req;

    try {
        req = JSON.parse(line);
    } catch (_) {
        writeResponse({
            id: null,
            ok: false,
            error: 'invalid_json',
        });
        return;
    }

    if (!req || typeof req.id !== 'string' || typeof req.url !== 'string' || !req.url.trim()) {
        writeResponse({
            id: req && req.id ? req.id : null,
            ok: false,
            error: 'invalid_request',
        });
        return;
    }

    try {
        const result = await fetchPage(req.url.trim(), {runtime});
        processedCount += 1;
        contextCount += 1;

        writeResponse({
            id: req.id,
            ok: true,
            markdown: result.markdown || '',
            title: result.title || '',
            links: Array.isArray(result.links) ? result.links : [],
            finalUrl: result.finalUrl || '',
            statusCode: result.statusCode || null,
        });

        if (contextCount >= contextRestartAfter || processedCount >= restartAfter) {
            process.stderr.write(
                `Scheduling browser restart after processed=${processedCount}, contexts=${contextCount}\n`
            );
            pendingRuntimeRestart = true;
        }
    } catch (error) {
        process.stderr.write(`${error && error.stack ? error.stack : String(error)}\n`);

        writeResponse({
            id: req.id,
            ok: false,
            error: (error && error.message ? error.message : String(error)).slice(0, MAX_ERROR_LENGTH),
        });
    }
}

function schedule(line) {
    pending.push(line);
    void drainQueue();
}

async function drainQueue() {
    if (draining) return;
    draining = true;

    try {
        if (pendingRuntimeRestart) {
            if (activeCount > 0) {
                return;
            }

            process.stderr.write('Restarting browser runtime while idle\n');
            pendingRuntimeRestart = false;
            processedCount = 0;
            contextCount = 0;
            await runtime.restart().catch(error => {
                process.stderr.write(
                    `Browser runtime restart failed: ${error && error.stack ? error.stack : String(error)}\n`
                );
            });
        }

        while (!pendingRuntimeRestart && activeCount < concurrency && pending.length > 0) {
            const line = pending.shift();
            activeCount += 1;

            handleLine(line)
                .catch(error => {
                    process.stderr.write(`${error && error.stack ? error.stack : String(error)}\n`);
                })
                .finally(() => {
                    activeCount -= 1;
                    void drainQueue();
                    void maybeExit();
                });
        }
    } finally {
        draining = false;
    }
}

rl.on('line', line => {
    schedule(line);
});

rl.on('close', () => {
    closed = true;
    void maybeExit();
});

async function maybeExit() {
    if (!closed || activeCount > 0 || pending.length > 0) {
        return;
    }

    await runtime.close();
    process.exit(0);
}

async function shutdown(signal) {
    closed = true;
    pending.length = 0;
    process.stderr.write(`Received ${signal}, closing browser runtime\n`);
    await runtime.close();
    process.exit(0);
}

process.on('SIGINT', () => {
    void shutdown('SIGINT');
});

process.on('SIGTERM', () => {
    void shutdown('SIGTERM');
});
