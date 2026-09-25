# Protein Folding Practical

[Open the browser practical](https://ben-shin.github.io/Protein-Folding-Practical/)

Analyze GFP denaturation data and prepare individual group CSV files from
CLARIOstar plate-reader exports. The browser app uses a minimal interface and
runs the shared Python analysis code locally in a Web Worker. Students do not
need to install Python. The desktop application and command-line batch exporter
remain available.

Developed for Imperial College London's Protein Folding Practical, led by
Dr. Ernesto Cota. For help or suggestions, contact Dr. Cota or
[Ben Shin](mailto:benwshin@gmail.com).

This README belongs to the source repository. **It is not included in the
GitHub Pages site.** The site build publishes only explicitly listed application
assets and Python modules; it excludes documentation and class data.

## Student analysis

1. Open the browser practical and select **Open CSV** to load your group's file,
   **Open project** to restore a saved project, or **Example** to try synthetic data.
2. Check the sample name, temperature, observations, and measurement information.
3. Enter an estimated midpoint and choose the two-state model, logistic model,
   or **Compare both**. Select **Fit**.
4. Inspect the curve, residuals, parameter uncertainty, and interpretation warnings.
   Record a reason for each excluded observation under **Data & exclusions**.
5. Export a **Project**, **CSV**, **Report**, or **Figure**. Project JSON preserves
   settings, exclusions, notes, and measurement metadata. Refit after reloading.

If an older open tab still shows 12 points, open the
[updated practical](https://ben-shin.github.io/Protein-Folding-Practical/index.html?release=0.6.1)
and select **Example · 16 points**. This reloads the app without clearing your
saved projects. Saved projects keep their original observations.

Both built-in examples use **16 concentration points: 0 to 6 M GuHCl in 0.4 M
steps**. The example plate assigns A1–A12 followed by B1–B4, with separate blanks
in H1–H4. Synthetic data are labeled as such. Original measurement examples are
not replaced or interpolated.

## Prepare CSV files for student groups

1. Expand **Plate data** and select **Open plate CSVs**. Select all relevant plate
   exports together; opening another selection replaces the currently loaded plates.
2. Select **Open group list CSV** and load the group assignment file. Use
   **Group list template** for the expected structure.
3. Check the signal, emission wavelength, repeated-read policy, and concentration
   series. The default practical series contains 16 points from 0 to 6 M.
4. Select **Prepare group list**. Review the summary: valid groups are prepared;
   invalid rows are listed with a reason so they can be corrected.
5. Select **Download group CSVs**. Extract the ZIP archive and give each student
   group its own CSV. Each file opens directly using the student's **Open CSV**.

The ZIP contains one denaturation CSV per prepared group. It does not contain
class rosters or an answer key. Use **Save all groups** for a reloadable practical
JSON file, or **Project** for an individual group's complete analysis state.
Only distribute the individual file intended for each student group.

For a manual assignment, select the plate and signal, click wells in concentration
order or enter their names, enter one concentration per well, name the group,
and select **Add group**. Blank correction applies to manual preparation; group-list exports use raw
fluorescence. Repeated-read choices apply to both preparation routes.
Missing observations and ambiguous repeated reads are not silently averaged.

### Group list format

The standard headers are `group name`, `plate number`, and `well ranges`:

```csv
group name,plate number,well ranges
Group 1,P1,A1-B4
Group 2,P1,B5-C8
Group 3,P2,A1-B4
```

Plate names match the uploaded file's name without `.csv` (for example, `P1.csv`
corresponds to `P1`). Well ranges follow row-major order: `A1-B4` means A1 through
A12, then B1 through B4. That is 16 wells, not a rectangular block. Preserve the
order in which the concentrations were plated.

The concentration series is:

```text
0, 0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8, 3.2, 3.6, 4.0, 4.4, 4.8, 5.2, 5.6, 6.0
```

Optional `concentrations`, `measurement`, and `wavelength` columns override the
shared settings for a row. Quote comma-separated lists in CSV cells:

```csv
group name,plate number,well ranges,concentrations
Group 1,P1,A1-B4,"0,0.4,0.8,1.2,1.6,2,2.4,2.8,3.2,3.6,4,4.4,4.8,5.2,5.6,6"
```

The browser also accepts optional `repeat_policy` and `acquisition_id` columns;
these override the shared repeated-read settings for each row.
Every group must have a unique name, exist on one loaded plate, and provide one
concentration per assigned well. Groups with different numbers of conditions
need explicit matching concentrations. See `examples/groups.csv` for the original
practical mapping.

### Individual output format

Each group CSV has exactly these columns, in the assigned concentration order:

```csv
GuHCl concentration (M),raw fluorescence values,normalized fluorescence values
```

Normalization is within-group min–max scaling: `(value - min) / (max - min)`.
It is not an estimate of fraction folded. For flat or entirely missing signals,
normalization is undefined: the cells stay blank and a warning is shown.
CSV exports contain raw fluorescence;
exclusions, blank correction, and metadata require project JSON.

Plate inputs may be CLARIOstar emission scans, long tables with a well column,
8-by-12 grids, or simple well/value tables. The browser limits each input file to
2 MB, a selection to 32 plates and 20 MB, and combined plate data to 100,000 rows.
Group lists accept up to 500 rows; prepared groups may contain up to 5,000
observations in total.
Use the desktop batch tool for larger datasets.

## Scientific interpretation

The two-state linear extrapolation model uses concentration-dependent folded
and unfolded baselines:

```text
ΔG_unfold([D]) = ΔG°H2O - m[D]
Cm = ΔG°H2O / m
```

It reports unfolding free energy in kJ/mol, m-value in kJ/mol/M, midpoint,
covariance-based standard errors, and fit diagnostics. Folding free energy has
the opposite sign. Thermodynamic interpretation requires experimental support
for equilibrium, reversibility, and two-state behavior. A logistic fit is
descriptive and does not establish thermodynamic free energies or mechanism.

AIC, AICc, and BIC compare fits to the same retained observations and response
scale. Warnings about flat signals, incomplete transitions, or poorly constrained
parameters matter even when the numerical optimization converges.

## Privacy and storage

Files and calculations stay in the browser. Application assets and pinned Python
packages are downloaded at startup; measurement files are not uploaded. Autosave
uses this browser's local storage. Download a project for durable storage and
use **Reset** on shared computers. Internet access is needed for the runtime;
offline startup is not guaranteed.

## Desktop and command-line use

Install Python 3.9 or newer, then:

```sh
git clone https://github.com/ben-shin/Protein-Folding-Practical.git
cd Protein-Folding-Practical
python -m pip install -e .
python run_app.py
```

Alternatively, create the Conda environment from `environment.yml` and run the
included Windows, macOS, or Linux launcher. In the desktop app, **File → Batch
export from files** (Ctrl+B) accepts the plate CSVs, group list, and output folder.

```sh
protein-folding-batch --plates examples/P*.csv --groups examples/groups.csv --out exported
```

The desktop batch exporter writes both `<group>.csv` curves and
`<group>_spectra.csv` emission scans. Useful options include:

| Option | Purpose |
| --- | --- |
| `--concentrations "0,0.4,0.8"` | An explicit series matching each group's well count |
| `--range 0 6` | An evenly spaced range, defaulting to 0–6 M |
| `--wavelength 508` | Emission wavelength for the denaturation curve |
| `--no-spectra` / `--no-curves` | Export only one file type |

## Development and deployment

```sh
python -m pip install -r requirements-browser-reference.txt matplotlib==3.8.4
python -m pytest -q
python scripts/generate_reference.py
python scripts/build_web.py
npm ci
npx playwright install --with-deps chromium
npm run test:browser
python -m http.server --directory dist 8765
```

The browser runtime pins Pyodide 0.27.7, Python 3.12.7, NumPy 2.0.2,
SciPy 1.14.1, and pandas 2.2.3. Native/browser parity tests check numerical
results, and browser tests exercise import, fitting, recovery, and downloads.

GitHub Actions tests changes before publishing `dist/` to
[GitHub Pages](https://ben-shin.github.io/Protein-Folding-Practical/). Pull requests
run checks without publishing. The Pages source is **GitHub Actions**. The build
allowlist and its regression test keep this README, other documentation,
measurement files, and group lists out of the published artifact.

See [browser release notes](docs/browser-release.md) for runtime details,
validation, and classroom checks.
