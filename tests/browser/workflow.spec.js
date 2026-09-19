import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

const LONG_TIMEOUT = 240_000;
const PRIVATE_MARKER = "PRIVATE_MEASUREMENT_MARKER_7f21c8";

async function waitForTask(page) {
  await expect(page.locator("#cancel")).toBeHidden({ timeout: LONG_TIMEOUT });
  await expect(page.locator("#error")).toBeHidden();
}

async function clickAndWait(page, selector) {
  await page.locator(selector).click();
  await waitForTask(page);
}

async function downloadFrom(page, selector) {
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: LONG_TIMEOUT }),
    page.locator(selector).click(),
  ]);
  const path = await download.path();
  expect(path).not.toBeNull();
  const buffer = await readFile(path);
  await expect(page.locator("#cancel")).toBeHidden({ timeout: LONG_TIMEOUT });
  return { buffer, filename: download.suggestedFilename() };
}

function addPrivateMarker(csv) {
  return csv
    .trimEnd()
    .split(/\r?\n/)
    .map((line, index) => `${line},${index === 0 ? "private_note" : PRIVATE_MARKER}`)
    .join("\n") + "\n";
}

test("analysis and plate workflows round-trip locally", async ({ page }) => {
  const requests = [];
  page.on("request", request => {
    requests.push({
      method: request.method(),
      url: request.url(),
      postData: request.postData() || "",
    });
  });

  await page.goto("/");
  await expect(page.locator("#fit-state")).toHaveText("No data");
  await clickAndWait(page, "#example");
  await expect(page.locator("#source-summary")).toContainText("synthetic_teaching_plate.csv");
  await expect(page.locator("#row-count")).toContainText("12 rows");

  await clickAndWait(page, "#fit");
  await expect(page.locator("#fit-state")).toHaveText("Fit complete");
  await expect(page.locator("#result-badge")).not.toHaveText("Ready to fit");
  await expect(page.locator("#residual-plot svg")).toHaveCount(1);
  await expect(page.locator("#save-report")).toBeEnabled();

  const projectDownload = await downloadFrom(page, "#save-project");
  expect(projectDownload.filename).toMatch(/\.folding\.json$/);
  const exportedProject = JSON.parse(projectDownload.buffer.toString("utf8"));
  expect(exportedProject.schema_version).toBe("1.0");
  expect(exportedProject.result?.fits?.length).toBeGreaterThan(0);

  const csvDownload = await downloadFrom(page, "#save-csv");
  expect(csvDownload.filename).toMatch(/\.csv$/);
  const markedCsv = addPrivateMarker(csvDownload.buffer.toString("utf8"));
  await page.locator("#series-file").setInputFiles({
    name: "private-marker-roundtrip.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(markedCsv),
  });
  await expect(page.locator("#source-summary")).toContainText("private-marker-roundtrip.csv", {
    timeout: LONG_TIMEOUT,
  });
  await expect(page.locator("#result-badge")).toHaveText("Ready to fit");
  await expect(page.locator("#row-count")).toContainText("12 rows · 0 excluded");

  await page.locator("#project-file").setInputFiles({
    name: projectDownload.filename,
    mimeType: "application/json",
    buffer: projectDownload.buffer,
  });
  await expect(page.locator("#notice")).toContainText("Project loaded", { timeout: LONG_TIMEOUT });
  await expect(page.locator("#result-badge")).toHaveText("Ready to fit");
  await expect(page.locator("#fit-state")).toHaveText("Ready to fit");

  await clickAndWait(page, "#fit");
  await expect(page.locator("#residual-plot svg")).toHaveCount(1);
  await page.locator("#temperature").fill("299.00");
  await expect(page.locator("#result-badge")).toHaveText("Ready to fit");
  await expect(page.locator("#residual-plot svg")).toHaveCount(0);
  await expect(page.locator("#save-report")).toBeDisabled();
  await expect(page.locator("#fit-state")).toHaveText("Settings changed. Refit required.");

  const recoveryNote = "Recovered synthetic session after invalidating a fit.";
  await page.locator("#notes").fill(recoveryNote);
  await expect.poll(
    () => page.evaluate(() => JSON.parse(localStorage.getItem("protein-folding-practical.session.v1"))?.project?.notes),
    { timeout: 10_000 },
  ).toBe(recoveryNote);
  await expect(page.locator("#autosave-state")).toContainText("Saved locally");
  await page.reload();
  await expect(page.locator("#recovery")).toBeVisible();
  await clickAndWait(page, "#recover");
  await expect(page.locator("#notes")).toHaveValue(recoveryNote);
  await expect(page.locator("#temperature")).toHaveValue("299");
  await expect(page.locator("#result-badge")).toHaveText("Ready to fit");

  await expect(page.locator("#instructor > summary")).toContainText("Plate data");
  await page.locator("#instructor > summary").click();
  await clickAndWait(page, "#example-plate");
  await expect(page.locator("#plate-source")).toContainText("synthetic_teaching_plate.csv");
  await expect(page.locator("#assigned-wells")).toHaveValue(/A1, A2, A3/);
  await expect(page.locator("#prepare")).toBeEnabled();
  await clickAndWait(page, "#prepare");
  await expect(page.locator("#prepared-count")).toHaveText("1");
  await expect(page.locator("#source-summary")).toContainText("synthetic_teaching_plate.csv");

  const firstGroupNote = "First prepared group: retain fit, note, and justified exclusion.";
  await page.locator("#notes").fill(firstGroupNote);
  await page.locator("#preview-details > summary").click();
  const firstObservation = page.locator("#preview tbody tr").first();
  await firstObservation.locator('input[type="checkbox"]').uncheck();
  await firstObservation.locator('input[type="text"]').fill("Synthetic outlier for round-trip test");
  await clickAndWait(page, "#fit");
  await expect(page.locator("#residual-plot svg")).toHaveCount(1);

  await page.locator("#prepared-name").fill("Synthetic teaching example B");
  await clickAndWait(page, "#prepare");
  await expect(page.locator("#prepared-count")).toHaveText("2");
  await expect(page.locator("#group-picker")).toBeVisible();
  await expect(page.locator("#group-name")).toHaveValue("Synthetic teaching example B");
  const secondGroupNote = "Second prepared group remains independently editable.";
  await page.locator("#notes").fill(secondGroupNote);

  await page.locator("#group-picker").selectOption("0");
  await expect(page.locator("#group-name")).toHaveValue("Synthetic teaching example");
  await expect(page.locator("#notes")).toHaveValue(firstGroupNote);
  await expect(page.locator("#residual-plot svg")).toHaveCount(1);
  await expect(firstObservation.locator('input[type="checkbox"]')).not.toBeChecked();
  await expect(firstObservation.locator('input[type="text"]')).toHaveValue(
    "Synthetic outlier for round-trip test",
  );

  const practicalDownload = await downloadFrom(page, "#save-practical");
  expect(practicalDownload.filename).toMatch(/\.practical\.json$/);
  const practical = JSON.parse(practicalDownload.buffer.toString("utf8"));
  expect(practical.schema_version).toBe("practical-1.0");
  expect(practical.projects).toHaveLength(2);
  expect(practical.selected_index).toBe(0);
  const firstSaved = practical.projects.find(item => item.settings.group_name === "Synthetic teaching example");
  const secondSaved = practical.projects.find(item => item.settings.group_name === "Synthetic teaching example B");
  expect(firstSaved.notes).toBe(firstGroupNote);
  expect(firstSaved.observations.filter(row => row.excluded)).toHaveLength(1);
  expect(firstSaved.result?.fits?.length).toBeGreaterThan(0);
  expect(secondSaved.notes).toBe(secondGroupNote);

  await page.locator("#project-file").setInputFiles({
    name: practicalDownload.filename,
    mimeType: "application/json",
    buffer: practicalDownload.buffer,
  });
  await expect(page.locator("#notice")).toContainText("Project loaded", { timeout: LONG_TIMEOUT });
  await expect(page.locator("#prepared-count")).toHaveText("2");
  await expect(page.locator("#group-picker")).toHaveValue("0");
  await expect(page.locator("#group-name")).toHaveValue("Synthetic teaching example");
  await expect(page.locator("#notes")).toHaveValue(firstGroupNote);
  await expect(page.locator("#row-count")).toContainText("1 excluded");
  await expect(page.locator("#result-badge")).toHaveText("Ready to fit");

  expect(requests.filter(request => request.method !== "GET"), "No uploaded data are sent by POST").toEqual([]);
  expect(
    requests.filter(request => request.url.includes(PRIVATE_MARKER) || request.postData.includes(PRIVATE_MARKER)),
    "The private CSV marker never enters a request URL or body",
  ).toEqual([]);
  const external = requests.filter(request => new URL(request.url).origin !== "http://127.0.0.1:8765");
  expect(external.length).toBeGreaterThan(0);
  for (const request of external) {
    const url = new URL(request.url);
    expect(url.hostname).toBe("cdn.jsdelivr.net");
    expect(url.pathname).toContain("/pyodide/");
    expect(request.method).toBe("GET");
  }
});

test("barebones shell omits slogans and audience framing", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.locator(".hero, .hero-note, .eyebrow, .brand-sub, .step")).toHaveCount(0);
  await expect(page.locator("#instructor > summary")).toContainText("Plate data");

  const headingCopy = await page.locator("h1, h2, h3, summary").allInnerTexts();
  expect(headingCopy.join(" ")).not.toMatch(/\b(?:student|instructor)\b/i);
  await expect(page.getByText("From fluorescence", { exact: false })).toHaveCount(0);
  await expect(page.getByText("A guided analysis", { exact: false })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("barebones-desktop.png"), fullPage: true });
});

test("mobile layout has no horizontal overflow and skip navigation is keyboard reachable", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.locator(".skip")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect.poll(() => page.evaluate(() => location.hash)).toBe("#workspace");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await expect(page.locator("#workspace")).toBeVisible();
  await expect(page.locator("#example")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("mobile-workspace.png"), fullPage: true });
});

test("a failed runtime download can be retried without reloading the page", async ({ page, context }) => {
  let attempts = 0;
  await context.route("https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.mjs", async route => {
    attempts += 1;
    if (attempts === 1) await route.abort("failed");
    else await route.continue();
  });
  await page.goto("/");
  await page.locator("#example").click();
  await expect(page.locator("#error")).toBeVisible({ timeout: LONG_TIMEOUT });
  await expect(page.locator("#runtime-status")).toContainText("The next action will restart it");
  await expect(page.locator("#example")).toBeEnabled();
  await clickAndWait(page, "#example");
  await expect(page.locator("#row-count")).toContainText("12 rows", { timeout: LONG_TIMEOUT });
  await expect(page.locator("#fit")).toBeEnabled();
  expect(attempts).toBe(2);
});
