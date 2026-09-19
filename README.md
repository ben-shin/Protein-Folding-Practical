# Protein-Folding-Practical

## Open the browser practical

**[Launch the practical](https://ben-shin.github.io/Protein-Folding-Practical/)**

Students can use the synthetic example, open their existing three-column group
CSV, or load an instructor-prepared project without installing Python. The
browser interface runs the shared Python analysis core locally in a Web Worker,
shows observations, residuals and interpretation diagnostics, and exports data,
figures, reports and reloadable projects. The instructor workspace prepares
groups from plate data with explicit measurement and acquisition choices.

See [browser release and teaching notes](docs/browser-release.md) for the pinned
runtime, data handling, validation, deployment and classroom pilot checklist.
The desktop workflow described below remains available.

For Imperial College London's Protein Folding Practical lead by Dr. Ernesto Cota.

If you need any help, want to report a bug, or have suggestions on improving the software, email Dr. Cota or me at benwshin@gmail.com

Python based GUI software to process GFP folding practical data from 96 well CLARIOstar plate reader exports.

This is designed to make data management and analysis easier. It imports raw CSV files into a tidy table, lets the instructor assign wells and GuHCl concentrations to named practical groups, exports one clean CSV per group for distribution, and allows overlays of any number of groups. It can also fit descriptive and thermodynamics denaturation curves.

## The short version

If you have the plate files and a group map, you never have to touch the plate map. Press **Ctrl+B** (or **Batch export**), pick every plate CSV, pick the group map, pick a folder. Every group gets both of its CSVs, and the data stays loaded so you can go straight to fitting. Anything that could not be read is listed at the end instead of stopping the run.

## What this repo can do

### 1. Import plate reader CSV files

The importer should accept several common layouts:
1. A long table containing a 'Well', 'Well ID', 'Position', or equivalent column
2. An 8x12 plate grid with rows A-H
3. A simple well vs value table.

The read tables contain:
1. 'plate_id'
2. 'source_file'
3. 'well'
4. 'row'
5. 'column'
6. 'measurement'
7. 'value'

If the export contains a bunch of numerical readouts, the GUI exposes them through the **Signal** menu

### 2. Assign wells to practical groups

Wells are selected on an interactive 96 well map that shows what is going on while you work:

1. Every selected well is numbered with its place in the concentration series, so you can see the order you actually clicked
2. Wells that already belong to another group are tinted in that group's colour, so gaps and overlaps are obvious
3. Wells with no data for the current plate and signal are greyed out
4. Hovering a well reads out its value and its group

**Groups that did not plate where the map says.** Leave *One click selects a whole block of conditions* ticked, set the number of conditions, and click the first well of the group. That well and the next ones in reading order are taken, wherever the group happened to start. A block that would run past H12 is shortened rather than refused, and the status bar says so. Untick it to click wells one at a time or drag across a run of them. Right click removes a single well and **Undo last well** removes the most recent one.

A start well can also be typed into the **Start well** box if you prefer.

Double-clicking a saved group loads it back into the editor, so a bad assignment can be fixed instead of deleted and redone.

Each named group stores:
1. plate/file identity
2. selected signal
3. ordered wells
4. ordered GuHCl concentrations

The number and pattern of conditions have not been hardcoded. 12-16 conditions work naturally, but any group with at least 3 conditions can be stores. At least 8 observations are needed for the twostate thermodynamics fit.

### 3. Export clean group files

Each group is exported as '<group_name>.csv' with exactly these columns:
```text
GuHCl concentration (M)
raw fluorescence values
normalized fluorescence values
```

Normalization is within group min-max scaling:
```text
(value-min)/(max-min)
```

Each group also gets '<group_name>_spectra.csv', which is the whole emission scan laid out one row per wavelength and one column per condition:
```text
wavelength (nm),0M,0.4M,0.8M,1.2M, ... ,6M
500.0,16986.0,12970.0,17345.0,16725.0, ... ,124.0
501.0,17649.5,13405.4,18056.2,17360.9, ... ,126.1
```

The column order follows the order the wells were assigned in, so it matches the concentration series exactly. Repeated concentrations within one group are numbered, for example `0.4M (2)`.

The raw imported table and the group assignment mapping can also be saved.

### 3a. Export everything in one pass

**File -> Batch export from files** (Ctrl+B) asks for the plate CSVs, the group map, and an output folder, then writes both files for every group. The same thing runs from a terminal without opening the app:

```bash
protein-folding-batch --plates examples/P*.csv --groups examples/groups.csv --out exported
```

Useful options:

| Option | What it does |
| --- | --- |
| `--concentrations "0,0.4,0.8"` | Use this exact series for every group |
| `--range 0 6` | Spread this range evenly over each group's wells (the default) |
| `--no-spectra` / `--no-curves` | Write only one of the two file types |
| `--wavelength 508` | Emission wavelength for the denaturation curve |

A group whose row cannot be read — a plate that was not loaded, a well range with no data, a duplicate name — is reported at the end and every other group is still written.

### 4. Plot and fit any combination of groups

The analysis panel can display one group, several groups, or all groups on the same graph. Matplotlib assigns a different color to each group.

Available models:
1. 4PL logistic
2. 2 state linear extrapolation
3. Auto compare
4. Fit both

The thermodynamics model uses:
```text
ΔG_unfold([D]) = ΔG°H2O - m[D]
Cm = ΔG°H2O / m
```

and reports:
1. ΔG°unfolding,H2O in kJ/mol and the corresponding ΔG°folding,H2O = -ΔG°unfolding,H2O
2. m-value in kJ/mol/M
3. Cm in M GuHCl
4. covariance derived standard errors
5. RMSE, R2, AIC, AICc, and BIC

## Scientific interpretation

A logistic curve alone does NOT provide thermodynamic folding free energies. The reported free energies only come from the 2 state LEM fit. The fitted quantity is ΔG°unfolding,H2O and reports the folding energy, which is just a negative. You can only interpret this when the experiment is close to equilibrium and GFP behaves as a reversible 2 state system in the conditions.

## Installation

Python 3.9 or newer is recommended.

### Windows PowerShell
```bash
git clone https://github.com/ben-shin/Protein-Folding-Practical.git
cd Protein-Folding-Practical
conda env create -f .\environment.yml
conda activate proteinfoldingpractical
python -m pip install -e . --no-deps
.\launch_windows.ps1
```
### Linux
```bash
git clone https://github.com/ben-shin/Protein-Folding-Practical.git
cd Protein-Folding-Practical
conda env create -f environment.yml
conda activate proteinfoldingpractical
python -m pip install -e . --no-deps
chmod +x launch_linux.sh
./launch_linux.sh
```
### macOS
```bash
git clone https://github.com/ben-shin/Protein-Folding-Practical.git
cd Protein-Folding-Practical
conda env create -f environment.yml
conda activate proteinfoldingpractical
python -m pip install -e . --no-deps
chmod +x launch_macos.command
./launch_macos.command
```


## Practical workflow

If you have a group map, use **Batch export** (Ctrl+B) and skip to step 9.

1. Export the CLARIOstar data as CSV files.
2. Open the application and load the CSV files.
3. Select the plate and measurement signal.
4. Click the first well of a group with block selection on, or click wells one at a time in increasing or decreasing GuHCl order.
5. Check the numbers on the selected wells — that is the concentration order.
6. Enter or generate the concentration list. The panel says whether the well count and the concentration count agree.
7. Name and add the group. The name is advanced for you, so the next group is one click and one Enter away.
8. Repeat for all practical groups, then export the group CSVs and the spectra CSVs.
9. In the analysis tab, select any of the groups, choose **Auto compare** and run the fit.
10. Check the graph, model comps, param uncertainty, and residual plausibility.

### Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+O | Load plate CSV files |
| Ctrl+G | Load group map CSV |
| Ctrl+B | Batch export from files |
| Ctrl+E | Export all group CSVs |
| Ctrl+S | Save group mapping |
| F1 | Quick start |

## The group map

The group map CSV assigns wells and concentrations to groups automatically. It must have three columns with headers
```text
group name, plate number, well ranges
```
The plate number should match the name of the plate reading CSV file. See `examples/groups.csv`.

Optional extra columns are picked up if present: `concentrations`, `measurement`, and `wavelength`. Give a group its own `concentrations` list when it does not run the same series as everyone else — that is also the only way to mix groups with different numbers of conditions in one map.

A group's conditions must all be on a single plate. A row that breaks any of these rules is now skipped and reported rather than stopping the whole import, so the rest of the cohort still loads and exports; fix those groups by hand on the plate map.

## Tests
```bash
python -m pytest -q
```
