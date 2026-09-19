import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const reference = JSON.parse(
  readFileSync(new URL("../fixtures/browser_reference.json", import.meta.url), "utf8"),
);

function expectClose(actual, expected, relative, absolute) {
  expect(typeof actual).toBe("number");
  const tolerance = absolute + relative * Math.max(Math.abs(expected), 1);
  expect(Math.abs(actual - expected)).toBeLessThanOrEqual(tolerance);
}

function expectVectorClose(actual, expected, tolerance) {
  expect(actual).toHaveLength(expected.length);
  for (let index = 0; index < expected.length; index += 1) {
    if (expected[index] === null) expect(actual[index]).toBeNull();
    else expect(Math.abs(actual[index] - expected[index])).toBeLessThanOrEqual(tolerance);
  }
}

function assertFiniteJson(value, path = "response") {
  if (typeof value === "number") {
    expect(Number.isFinite(value), `${path} must be finite`).toBe(true);
  } else if (Array.isArray(value)) {
    value.forEach((item, index) => assertFiniteJson(item, `${path}[${index}]`));
  } else if (value && typeof value === "object") {
    Object.entries(value).forEach(([key, item]) => assertFiniteJson(item, `${path}.${key}`));
  }
}

async function runCasesInWorker(page, cases) {
  return page.evaluate(async entries => {
    const worker = new Worker("/worker.js", { type: "module" });
    let nextId = 0;
    const pending = new Map();
    worker.onmessage = ({ data }) => {
      if (data.type === "status") return;
      const job = pending.get(data.id);
      if (!job) return;
      pending.delete(data.id);
      job.resolve(data.response);
    };
    const call = request => new Promise((resolve, reject) => {
      const id = ++nextId;
      pending.set(id, { resolve, reject });
      worker.postMessage({ id, request });
    });
    worker.onerror = event => {
      for (const job of pending.values()) job.reject(new Error(event.message || "Worker failed"));
      pending.clear();
    };

    try {
      const initialized = await call({ action: "initialize" });
      if (!initialized.ok) throw new Error(initialized.error?.message || "Worker initialization failed");
      const output = {};
      for (const [name, fixture] of Object.entries(entries)) {
        const imported = await call(fixture.import_request);
        if (!imported.ok) throw new Error(`${name} import failed: ${imported.error?.message}`);
        const fitted = await call({
          action: "fit",
          project: imported.data.project,
          model: fixture.fit_model,
        });
        output[name] = { imported, fitted };
      }
      return output;
    } finally {
      worker.terminate();
    }
  }, cases);
}

test("Pyodide worker matches native fits across synthetic edge cases", async ({ page }) => {
  await page.goto("/");
  const actualCases = await runCasesInWorker(page, reference.cases);
  const tolerances = reference.tolerances;
  const invariantDiagnostics = [
    "input_count",
    "retained_count",
    "excluded_nonfinite_count",
    "distinct_concentration_count",
    "replicate_group_count",
    "replicate_observation_count",
    "low_denaturant_baseline_covered",
    "high_denaturant_baseline_covered",
    "transition_covered",
    "midpoint_within_observed_range",
    "bound_hit",
    "bound_hit_parameters",
    "parameter_bounds",
    "covariance_rank",
    "covariance_status",
    "covariance_well_conditioned",
    "multistart_consistent",
  ];

  for (const [name, fixture] of Object.entries(reference.cases)) {
    const actual = actualCases[name];
    const expectedImport = fixture.native_import;
    const expectedFitResponse = fixture.native_fit;
    assertFiniteJson(actual, name);

    expect(actual.imported.ok, `${name} import`).toBe(true);
    expect(actual.imported.action).toBe("import_series");
    const actualRows = actual.imported.data.project.observations;
    const expectedRows = expectedImport.data.project.observations;
    expect(actualRows.map(row => ({
      row_id: row.row_id,
      concentration_m: row.concentration_m,
      raw_signal: row.raw_signal,
      excluded: row.excluded,
      exclusion_reason: row.exclusion_reason,
    }))).toEqual(expectedRows.map(row => ({
      row_id: row.row_id,
      concentration_m: row.concentration_m,
      raw_signal: row.raw_signal,
      excluded: row.excluded,
      exclusion_reason: row.exclusion_reason,
    })));

    expect(actual.fitted.ok, `${name} fit`).toBe(true);
    expect(actual.fitted.action).toBe("fit");
    const actualResult = actual.fitted.data.result;
    const expectedResult = expectedFitResponse.data.result;
    expect(actualResult.preferred_model).toBe(expectedResult.preferred_model);
    expect(actualResult.fits).toHaveLength(expectedResult.fits.length);

    for (let index = 0; index < expectedResult.fits.length; index += 1) {
      const observed = actualResult.fits[index];
      const expected = expectedResult.fits[index];
      expect(observed.model_name).toBe(expected.model_name);
      expect(observed.success).toBe(expected.success);
      expect(observed.interpretation_status).toBe(expected.interpretation_status);
      expect(observed.warnings).toEqual(expected.warnings);
      expect(observed.metrics.observation_count).toBe(expected.metrics.observation_count);
      expect(observed.metrics.model_parameter_count).toBe(expected.metrics.model_parameter_count);
      expect(observed.metrics.information_parameter_count).toBe(
        expected.metrics.information_parameter_count,
      );
      expect(observed.observed.row_ids).toEqual(expected.observed.row_ids);
      expect(observed.observed.retained_indices).toEqual(expected.observed.retained_indices);
      expect(observed.observed.excluded_indices).toEqual(expected.observed.excluded_indices);

      expect(Object.keys(observed.parameters).sort()).toEqual(Object.keys(expected.parameters).sort());
      for (const parameter of Object.keys(expected.parameters)) {
        expectClose(
          observed.parameters[parameter],
          expected.parameters[parameter],
          tolerances.parameter_relative,
          tolerances.parameter_absolute,
        );
      }

      const scale = Math.max(1, ...expected.observed.y.map(value => Math.abs(value)));
      const predictionTolerance = Math.max(
        tolerances.prediction_absolute_floor,
        tolerances.prediction_scale_fraction * scale,
      );
      expectVectorClose(observed.observed.predicted, expected.observed.predicted, predictionTolerance);
      expectVectorClose(observed.observed.residuals, expected.observed.residuals, predictionTolerance);

      if (["noisy", "replicate"].includes(name)) {
        for (const metric of ["rss", "rmse", "aic", "bic", "aicc"]) {
          if (expected.metrics[metric] === null) expect(observed.metrics[metric]).toBeNull();
          else expectClose(observed.metrics[metric], expected.metrics[metric], 1e-4, 1e-5);
        }
      }

      for (const key of invariantDiagnostics) {
        expect(observed.diagnostics[key], `${name}.${observed.model_name}.${key}`).toEqual(
          expected.diagnostics[key],
        );
      }
    }
  }

  const missingRows = actualCases.missing.imported.data.project.observations;
  expect(missingRows.filter(row => row.excluded)).toHaveLength(2);
  expect(missingRows.filter(row => row.excluded).map(row => row.exclusion_reason)).toEqual([
    "missing concentration",
    "missing raw signal",
  ]);
  expect(actualCases.missing.fitted.data.result.fits[0].observed.row_ids).toEqual(
    missingRows.filter(row => !row.excluded).map(row => row.row_id),
  );

  const ideal = actualCases.ideal.fitted.data.result.fits[0];
  expect(ideal.interpretation_status).toBe("interpretable");
  expectClose(ideal.parameters.delta_g_h2o_kj_mol, 20.0, 1e-3, 1e-5);
  expectClose(ideal.parameters.m_value_kj_mol_m, 6.25, 1e-3, 1e-5);
  expectClose(ideal.parameters.cm_m, 3.2, 1e-3, 1e-5);
  expect(actualCases.partial.fitted.data.result.fits[0].interpretation_status).toBe(
    "insufficient_information",
  );
  expect(actualCases.flat.fitted.data.result.fits[0].interpretation_status).toBe(
    "insufficient_information",
  );

  const replicate = actualCases.replicate.fitted.data.result.fits[0].diagnostics;
  expect(replicate.retained_count).toBe(33);
  expect(replicate.distinct_concentration_count).toBe(11);
  expect(replicate.replicate_group_count).toBe(11);
  expect(replicate.replicate_observation_count).toBe(22);
});
