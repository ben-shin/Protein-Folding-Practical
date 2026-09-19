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

Open the instructor workspace, load a plate CSV (or the synthetic plate), and
select the plate, signal, excitation/emission and acquisition policy. Assign
ordered wells to explicit concentrations using the plate map or text fields.
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
are approximate, and equilibrium, reversibility and two-state behaviour still
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

`scripts/build_web.py` includes only an explicit list of web assets and seven
Python modules. It never copies `examples/`, uploads, cohort exports, repository
metadata or desktop code. A generated manifest records Python module hashes and
runtime versions; the worker verifies these before importing the core.

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
