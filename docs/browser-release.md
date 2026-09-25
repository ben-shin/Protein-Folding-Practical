# Browser practical: release and teaching notes

Target: <https://ben-shin.github.io/Protein-Folding-Practical/>.

The browser application uses the same `folding_practical` Python model and
import modules as the desktop application. JavaScript handles the interface,
SVG plots, file downloads and local storage. A dedicated Web Worker runs all
Python parsing and fitting. The desktop Tkinter and enhancement layer are not
imported into the browser.

## Pinned numerical environments

The browser pins **Pyodide 0.27.7**, Python 3.12.7, NumPy 2.0.2, SciPy 1.14.1,
and pandas 2.2.3. This is a deliberately selected classroom compatibility
target, not an unpinned reference to the latest Pyodide release. The exact
runtime/package set is checked against a native Python 3.12 reference stack in
`requirements-browser-reference.txt`. Python 3.9 and the original NumPy/SciPy
stack remain separately tested.

Native/browser parity tests compare shared synthetic fixtures using explicit
numerical tolerances. Optimizer trajectories need not agree bit for bit. Fit
diagnostics distinguish successful calculation from information sufficient to
estimate transition parameters.

## Student route

1. Open the public link, choose the synthetic example, a group CSV, or a
   prepared project/practical JSON file.
2. Confirm group, temperature, measurement metadata and retained observations.
   Legacy three-column CSV files do not encode excitation/emission settings;
   the application reports these as unrecorded rather than inventing values.
3. Estimate the midpoint, fit raw (or explicitly blank-corrected) fluorescence,
   and inspect residuals, warnings, covariance errors and coverage.
4. Record interpretation notes and any exclusion rationale.
5. Download a reloadable project, compatible group CSV, fit report and SVG
   figure. A practical containing several groups offers a group selector.

The browser autosaves its current session to local storage. Recovery is offered
on return; storage limits or restrictions are shown as a recoverable message.
Use a downloaded project for portable, durable storage. Reset clears the app's
saved session. Startup requires internet access; offline availability is not
guaranteed by this release.

## Instructor route

Expand **Plate data**, use **Open plate CSVs** to select the plate exports, then
**Open group list CSV** to load the group assignments. Confirm the shared signal,
emission, repeated-read policy, and concentrations before **Prepare group list**.
Review any row errors, then use **Download group CSVs** to download a ZIP containing
one three-column denaturation CSV per valid group. Give each group only its file;
students open it using **Open CSV**. A downloadable group-list template and the
repository README describe the accepted headers and optional overrides.

The default series and both built-in synthetic examples have 16 points, from
0 to 6 M GuHCl in 0.4 M steps. A1–B4 is a row-major run of 16 wells.
For manual preparation, select the plate, signal, excitation/emission, and
acquisition policy, then assign ordered wells and concentrations on the map.
Optional blank correction subtracts the mean of selected blank wells, with
that choice recorded in the project. Repeated reads require an explicit
selection or a declaration that averaging is appropriate for technical
replicates. Missing observations remain visible.

Prepare named groups, then export a versioned practical configuration. Optional
temperature/model locks guide students; they are not authentication and do not
protect secrets. Student identifiers and answer keys must be distributed
through appropriate course systems rather than public source or build assets.

## Scientific interpretation

The two-state model retains concentration-dependent folded and unfolded
baselines and the original unfolding-free-energy convention. Midpoint errors
retain the covariance between free energy and m-value. Local covariance errors
are approximate, and equilibrium, reversibility and two-state behavior still
require experimental support. Insufficient/flat/partial data can produce an
explicit insufficient-information result.

The logistic comparison concerns statistical fit among the compared shapes.
It does not establish mechanism. AIC/BIC/AICc use a Gaussian independent-error
likelihood with one common residual variance estimated from the observations;
the variance counts as a parameter. AICc is unavailable when n ≤ k + 1. Model
comparisons require the same retained observation pairs and response scale.

## Data handling and build boundary

Measurements are read as local text and sent to the in-browser worker.
The application requests fixed application assets and pinned runtime packages
from jsDelivr. It contains no measurement-upload endpoint, analytics or account
service. Browser tests inspect requests while importing/fitting marker data.
Local autosave stores measurements in this browser profile, so download and
reset when working on a shared classroom machine.

`scripts/build_web.py` includes only an explicit list of web assets and eight
Python modules. It never copies `README.md`, `docs/`, `examples/`, uploads, cohort exports, repository
metadata or desktop code. A generated manifest records Python module hashes and
runtime versions; the worker verifies these before importing the core.

## Performance changes

The worker downloads and verifies independent application modules while Python
and its initial packages load. SciPy is downloaded only for the first fit, so
plate preparation and CSV export need only NumPy and pandas. Requests retain
strict JSON handling, module digest verification, and cancellation/retry behavior.

Both solvers now supply exact derivatives to the same bounded multistart
optimization. The existing start points, bounds, equations, and interpretation
checks remain in place. On a local Python 3.12 / NumPy 2.0.2 / SciPy 1.14.1 run,
16-point fits needed about 80% fewer model evaluations for logistic fitting and
85% fewer for two-state fitting. Median timings in repeated runs were roughly
1.6 times faster for logistic and 1.8–2.1 times faster for two-state fitting.
These are native benchmark measurements, not a browser/device latency guarantee.
Predictions differed by at most about 1.1e-6 signal units in the benchmark cases,
with matching interpretation statuses; separate tests verify browser/native parity.

Reproduce the comparison against numerical differentiation:

```sh
OPENBLAS_NUM_THREADS=1 python scripts/benchmark_models.py
```

Large imports use a per-measurement row index; plate buttons are reused instead
of rebuilt on each click. Observation-table inputs are created only when opened.
Batch preparation validates the full plate collection once and prepares each
group from the relevant subset. Repeated source filenames are deduplicated when
resolving plate aliases. On the original 10-plate, 55-group dataset, deduplicating
these names reduced native preparation from 6.38 to 2.21 seconds (about 2.9 times
faster, averaging two runs), with identical preparation responses. Exported
concentrations, raw values, and normalized values match the original group files.

## Reproduce and deploy

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

The Pages workflow runs native and browser tests before publishing the static
`dist/` artifact. Pull requests run tests without deployment. The repository's
Pages source must be set to GitHub Actions. Test screenshots/logs are retained
as workflow artifacts, separate from the public site.

## Classroom pilot still required

Automated tests and a successful deployment do not certify classroom readiness.
Before a class, test cold startup on institutional Wi-Fi and managed browsers,
typical student devices, recovery after closing a tab, screen readers and
keyboard use with novice students. Record startup times and any blocked CDN
requests. Browser fitting removes a shared Python queue, but download bandwidth
and device speed still matter. No claim about a tested cohort size is made.

## Platform references

- [Pyodide 0.27.7 package lock](https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide-lock.json)
- [Pyodide worker guidance](https://pyodide.org/en/stable/usage/webworker.html)
- [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [SciPy curve fitting and covariance](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.curve_fit.html)
