// All measurement parsing and fitting takes place inside this worker.
// Only fixed runtime/core assets are requested; measurements never enter a URL.
const RUNTIME = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const RELEASE_ID = new URL(import.meta.url).pathname.match(/\/releases\/([a-f0-9]{20})\/worker\.js$/)?.[1];
let runtimePromise;
let runtimeReady = false;
let fittingReady = false;
function status(message) { self.postMessage({type: "status", message}); }
async function loadRuntime() {
  const {loadPyodide} = await import(`${RUNTIME}pyodide.mjs`);
  const py = await loadPyodide({indexURL: RUNTIME});
  status("Loading NumPy and pandas…");
  await py.loadPackage(["numpy", "pandas"]);
  return py;
}
async function loadCore() {
  const manifestResponse = await fetch("./build-manifest.json", {
    cache: RELEASE_ID ? "default" : "no-store"
  });
  if (!manifestResponse.ok) throw new Error("Could not load the application manifest. Reload this page.");
  const manifest = await manifestResponse.json();
  if (RELEASE_ID && manifest.build_id !== RELEASE_ID) {
    throw new Error("Application version mismatch. Reload the latest release before analyzing data.");
  }
  // Fetch and verify independent modules together, while Python downloads.
  const modules = await Promise.all(Object.entries(manifest.core_sha256).map(async ([name, expected]) => {
    // Release paths are immutable. Legacy root entry points must revalidate
    // against the current manifest instead of reusing a previous deployment.
    const coreURL = new URL(`./core/folding_practical/${name}`, import.meta.url);
    if (!RELEASE_ID) coreURL.searchParams.set("v", expected);
    const response = await fetch(coreURL, {cache: RELEASE_ID ? "default" : "no-cache"});
    if (!response.ok) throw new Error(`Missing analysis module: ${name}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    const sha = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2,"0")).join("");
    if (sha !== expected) throw new Error("Application assets are out of date. Reload the page before analyzing data.");
    return {name, bytes};
  }));
  return {manifest, modules};
}
async function initialize() {
  status("Downloading the Python runtime…");
  const [py, {manifest, modules}] = await Promise.all([loadRuntime(), loadCore()]);
  py.FS.mkdirTree("/app/folding_practical");
  for (const {name, bytes} of modules) py.FS.writeFile(`/app/folding_practical/${name}`, bytes);
  py.runPython("import sys, json\nsys.path.insert(0, '/app')\nfrom folding_practical.analysis import dispatch\n");
  status(`Ready · Python ${manifest.python} · analysis ${manifest.software_version}`);
  return py;
}
let queue = Promise.resolve();
self.onmessage = ({data}) => {
  queue = queue.then(async () => {
    try {
      runtimePromise ??= initialize();
      const py = await runtimePromise;
      runtimeReady = true;
      if (data.request.action === "initialize") {
        self.postMessage({id: data.id, response: {ok: true, data: {ready:true}}});
        return;
      }
      if (data.request.action === "fit" && !fittingReady) {
        status("Loading SciPy for fitting…");
        await py.loadPackage("scipy");
        fittingReady = true;
        status("Ready to fit");
      }
      py.globals.set("_request_json", JSON.stringify(data.request));
      let text;
      try {
        text = py.runPython("json.dumps(dispatch(json.loads(_request_json)), allow_nan=False, separators=(',', ':'))");
      } finally {
        py.globals.delete("_request_json");
      }
      self.postMessage({id: data.id, response: JSON.parse(text)});
    } catch (error) {
      if (!runtimeReady) runtimePromise = undefined;
      self.postMessage({id: data.id, response: {ok:false, error:{
        code: runtimeReady ? "analysis_failed" : "runtime_initialization_failed",
        message: String(error.message || error)
      }}});
    }
  });
};
