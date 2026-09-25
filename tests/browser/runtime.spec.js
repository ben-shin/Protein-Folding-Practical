import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";

const runtimeURL = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.mjs";
const modules = {"__init__.py": "# package", "analysis.py": "# analysis", "models.py": "# models"};
const manifest = {python: "test", software_version: "test", core_sha256: Object.fromEntries(
  Object.entries(modules).map(([name, body]) => [name, createHash("sha256").update(body).digest("hex")]),
)};
function fakeRuntime(packageWork = "") {
  return `export async function loadPyodide() { return {
    async loadPackage(packages) { ${packageWork} },
    FS: {mkdirTree() {}, writeFile() {}},
    globals: {set() {}, delete() {}},
    runPython() { return JSON.stringify({ok: true, data: {}}); }
  }; }`;
}
async function startInitialization(page, release = false) {
  await page.goto("/");
  await page.evaluate(release => {
    const workerURL = release
      ? new URL("./worker.js", document.querySelector('script[type="module"]').src).href
      : "/worker.js";
    window.testWorker = new Worker(workerURL, {type: "module"});
    window.initialization = new Promise((resolve, reject) => {
      window.testWorker.onmessage = ({data}) => { if (data.id === 1) resolve(data.response); };
      window.testWorker.onerror = event => reject(new Error(event.message));
    });
    window.testWorker.postMessage({id: 1, request: {action: "initialize"}});
  }, release);
}

test("runtime packages and all verified core assets download concurrently", async ({page, context}) => {
  const started = new Set();
  const coreVersions = new Map();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await context.route("**/build-manifest.json", route => route.fulfill({json: manifest}));
  await context.route(runtimeURL, route => route.fulfill({
    contentType: "text/javascript", body: fakeRuntime('await fetch("/package-download-marker");'),
  }));
  await context.route("**/package-download-marker", async route => {
    started.add("packages");
    await gate;
    await route.fulfill({body: "ready"});
  });
  await context.route("**/core/folding_practical/*.py*", async route => {
    const name = new URL(route.request().url()).pathname.split("/").pop();
    started.add(name);
    coreVersions.set(name, new URL(route.request().url()).searchParams.get("v"));
    await gate;
    await route.fulfill({body: modules[name]});
  });
  try {
    await startInitialization(page);
    // None of these responses completes until every independent download starts.
    await expect.poll(() => [...started].sort()).toEqual(["packages", ...Object.keys(modules)].sort());
    expect(Object.fromEntries(coreVersions)).toEqual(manifest.core_sha256);
    release();
    expect(await page.evaluate(() => window.initialization)).toEqual({ok: true, data: {ready: true}});
  } finally {
    release();
    await page.evaluate(() => window.testWorker?.terminate());
  }
});

test("parallel asset loading still rejects a mismatched module digest", async ({page, context}) => {
  await context.route("**/build-manifest.json", route => route.fulfill({json: manifest}));
  await context.route(runtimeURL, route => route.fulfill({contentType: "text/javascript", body: fakeRuntime()}));
  await context.route("**/core/folding_practical/*.py*", route => route.fulfill({body: "incorrect module"}));
  try {
    await startInitialization(page);
    const response = await page.evaluate(() => window.initialization);
    expect(response.ok).toBe(false);
    expect(response.error.code).toBe("runtime_initialization_failed");
    expect(response.error.message).toContain("Reload the page before analyzing data");
  } finally {
    await page.evaluate(() => window.testWorker?.terminate());
  }
});


test("CSV operations avoid SciPy and the first fit loads it only once", async ({page, context}) => {
  const loaded = [];
  await context.route("**/build-manifest.json", route => route.fulfill({json: manifest}));
  await context.route(runtimeURL, route => route.fulfill({
    contentType: "text/javascript", body: fakeRuntime('await fetch("/package-list?names=" + encodeURIComponent(JSON.stringify(packages)));'),
  }));
  await context.route("**/package-list?*", route => {
    loaded.push(JSON.parse(new URL(route.request().url()).searchParams.get("names")));
    return route.fulfill({body: "ready"});
  });
  await context.route("**/core/folding_practical/*.py*", route => route.fulfill({
    body: modules[new URL(route.request().url()).pathname.split("/").pop()],
  }));
  async function call(id, action) {
    return page.evaluate(({id, action}) => new Promise(resolve => {
      window.testWorker.onmessage = ({data}) => { if (data.id === id) resolve(data.response); };
      window.testWorker.postMessage({id, request: {action}});
    }), {id, action});
  }
  try {
    await startInitialization(page);
    expect((await page.evaluate(() => window.initialization)).ok).toBe(true);
    expect((await call(2, "import_series")).ok).toBe(true);
    expect(loaded).toEqual([["numpy", "pandas"]]);
    expect((await call(3, "fit")).ok).toBe(true);
    expect((await call(4, "fit")).ok).toBe(true);
    expect(loaded).toEqual([["numpy", "pandas"], "scipy"]);
  } finally {
    await page.evaluate(() => window.testWorker?.terminate());
  }
});


test("a release worker refuses a manifest from a different deployment", async ({page, context}) => {
  let coreRequests = 0;
  await context.route("**/build-manifest.json", route => route.fulfill({
    json: {...manifest, build_id: "old-deployment"},
  }));
  await context.route(runtimeURL, route => route.fulfill({contentType: "text/javascript", body: fakeRuntime()}));
  await context.route("**/core/folding_practical/*.py*", route => {
    coreRequests++;
    return route.fulfill({body: modules[new URL(route.request().url()).pathname.split("/").pop()]});
  });
  try {
    await startInitialization(page, true);
    const scriptURL = await page.locator('script[type="module"]').getAttribute("src");
    expect(scriptURL).toMatch(/releases\/[a-f0-9]{20}\/app\.js$/);
    const response = await page.evaluate(() => window.initialization);
    expect(response.ok).toBe(false);
    expect(response.error.code).toBe("runtime_initialization_failed");
    expect(response.error.message).toContain("Application version mismatch");
    expect(coreRequests).toBe(0);
  } finally {
    await page.evaluate(() => window.testWorker?.terminate());
  }
});
