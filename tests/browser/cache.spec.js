import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { extname, join, resolve, sep } from "node:path";

const LONG_TIMEOUT = 240_000;
let fixtureDirectory, firstBuild, currentBuild, firstManifest, currentManifest;

test.beforeAll(async () => {
  fixtureDirectory = await mkdtemp(join(tmpdir(), "folding-cache-regression-"));
  // Build both snapshots from isolated source/output paths. Only the first
  // synthetic example changes; no class measurements or source checkout edits.
  execFileSync("python3", ["-c", [
    "from pathlib import Path",
    "import shutil, sys",
    "from scripts import build_web",
    "repository = build_web.ROOT",
    "fixture = Path(sys.argv[1])",
    "old_source = fixture / 'source-12'",
    "for folder, names in [('web', build_web.ASSETS), ('folding_practical', build_web.CORE)]:",
    "    (old_source / folder).mkdir(parents=True)",
    "    for name in names:",
    "        shutil.copyfile(repository / folder / name, old_source / folder / name)",
    "analysis = old_source / 'folding_practical' / 'analysis.py'",
    "source = analysis.read_text()",
    "original = 'concentrations = [index * 4 / 10 for index in range(16)]'",
    "assert source.count(original) == 1, 'Update the synthetic cache fixture for the current example'",
    "analysis.write_text(source.replace(original, 'concentrations = [index * 4 / 10 for index in range(12)]'))",
    "build_web.ROOT = old_source",
    "build_web.OUT = fixture / 'first'",
    "build_web.build()",
    "build_web.ROOT = repository",
    "build_web.OUT = fixture / 'current'",
    "build_web.build()",
  ].join("\n"), fixtureDirectory], {cwd: process.cwd(), timeout: 120_000, stdio: "pipe"});
  firstBuild = join(fixtureDirectory, "first");
  currentBuild = join(fixtureDirectory, "current");
  firstManifest = JSON.parse(await readFile(join(firstBuild, "build-manifest.json"), "utf8"));
  currentManifest = JSON.parse(await readFile(join(currentBuild, "build-manifest.json"), "utf8"));
  expect(firstManifest.build_id).toMatch(/^[a-f0-9]{20}$/);
  expect(currentManifest.build_id).toMatch(/^[a-f0-9]{20}$/);
  expect(firstManifest.build_id).not.toBe(currentManifest.build_id);
});

test.afterAll(async () => {
  if (fixtureDirectory) await rm(fixtureDirectory, {recursive: true, force: true});
});

async function serveSnapshots({cacheHtml = false} = {}) {
  let activeBuild = firstBuild;
  const requests = [];
  const server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      requests.push(pathname);
      const relative = pathname.endsWith("/") ? pathname + "index.html" : pathname;
      const filename = resolve(activeBuild, "." + relative);
      if (!filename.startsWith(resolve(activeBuild) + sep)) {
        response.writeHead(403).end();
        return;
      }
      const extension = extname(filename);
      const contentType = {".html":"text/html; charset=utf-8", ".js":"text/javascript", ".css":"text/css", ".json":"application/json", ".svg":"image/svg+xml"}[extension] || "application/octet-stream";
      const cacheControl = extension === ".html" && !cacheHtml ? "no-store" : "public, max-age=3600";
      const body = await readFile(filename);
      response.writeHead(200, {"Content-Type": contentType, "Cache-Control": cacheControl, "Content-Length": body.length}).end(body);
    } catch {
      response.writeHead(404, {"Cache-Control":"no-store"}).end("Not found");
    }
  });
  await new Promise((resolveListen, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListen);
  });
  return {
    origin: "http://127.0.0.1:" + server.address().port,
    requests,
    deployCurrent() { activeBuild = currentBuild; },
    async close() {
      await new Promise((resolveClose, reject) => {
        server.close(error => error ? reject(error) : resolveClose());
        server.closeAllConnections();
      });
    },
  };
}

async function loadExample(page, expectedRows) {
  await page.locator("#example").click();
  await expect(page.locator("#cancel")).toBeHidden({timeout: LONG_TIMEOUT});
  await expect(page.locator("#error")).toBeHidden();
  await expect(page.locator("#row-count")).toContainText(expectedRows + " rows");
}

test("a normal reload uses the new example while previous release assets remain cached", async ({page}) => {
  const server = await serveSnapshots();
  try {
    await page.goto(server.origin + "/");
    await loadExample(page, 12);
    const firstApp = "/releases/" + firstManifest.build_id + "/app.js";
    expect(server.requests.filter(path => path === firstApp)).toHaveLength(1);
    // Real browser HTTP cache: no route interception (which disables caching).
    // Fetching the already-used app must not reach the server a second time.
    await page.evaluate(async url => {
      const response = await fetch(url);
      if (!response.ok) throw new Error("Could not read the cached application");
      await response.text();
    }, server.origin + firstApp);
    expect(server.requests.filter(path => path === firstApp)).toHaveLength(1);

    server.deployCurrent();
    await page.reload();
    await loadExample(page, 16);
    await expect(page.locator('script[type="module"]')).toHaveAttribute("src", new RegExp(currentManifest.build_id));
    expect(server.requests).toContain("/releases/" + currentManifest.build_id + "/worker.js");
    expect(server.requests).toContain("/releases/" + currentManifest.build_id + "/core/folding_practical/analysis.py");
    expect(server.requests.filter(path => path === "/")).toHaveLength(2);
  } finally {
    await page.goto("about:blank");
    await server.close();
  }
});

test("current entry links bypass an old index that is still in the HTTP cache", async ({page}) => {
  // This case deliberately caches HTML too, reproducing a hosting/browser
  // cache that cannot be changed by the already-deployed old application.
  const server = await serveSnapshots({cacheHtml:true});
  try {
    await page.goto(server.origin + "/index.html");
    await loadExample(page, 12);
    const rootRequests = server.requests.filter(path => path === "/index.html").length;
    server.deployCurrent();
    const cachedIndex = await page.evaluate(async url => (await fetch(url)).text(), server.origin + "/index.html");
    expect(cachedIndex).toContain(firstManifest.build_id);
    expect(cachedIndex).not.toContain(currentManifest.build_id);
    expect(server.requests.filter(path => path === "/index.html")).toHaveLength(rootRequests);

    // The canonical entry with a release query remains usable after later
    // deployments, while its unique URL bypasses previously cached HTML.
    await page.goto(server.origin + "/index.html?release=" + currentManifest.build_id);
    await loadExample(page, 16);
    await expect(page.locator('meta[name="application-build"]')).toHaveAttribute("content", currentManifest.build_id);
    expect(server.requests.filter(path => path === "/index.html")).toHaveLength(rootRequests + 1);

    await page.goto(server.origin + "/" + currentManifest.release_index);
    await loadExample(page, 16);
    await expect(page.locator('meta[name="application-build"]')).toHaveAttribute("content", currentManifest.build_id);
    expect(server.requests).toContain("/" + currentManifest.release_index);
    expect(server.requests).toContain("/releases/" + currentManifest.build_id + "/worker.js");
  } finally {
    await page.goto("about:blank");
    await server.close();
  }
});
