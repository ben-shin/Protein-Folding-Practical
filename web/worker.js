// All measurement parsing and fitting takes place inside this worker.
// Only fixed runtime/core assets are requested; measurements never enter a URL.
const RUNTIME = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
let runtimePromise;
let runtimeReady = false;
function status(message) { self.postMessage({type: "status", message}); }
async function initialize() {
  status("Downloading the Python runtime…");
  const {loadPyodide} = await import(`${RUNTIME}pyodide.mjs`);
  const py = await loadPyodide({indexURL: RUNTIME});
  status("Loading NumPy, SciPy and pandas…");
  await py.loadPackage(["numpy", "scipy", "pandas"]);
  const manifestResponse = await fetch("./build-manifest.json");
  if (!manifestResponse.ok) throw new Error("Could not load the application manifest. Reload this page.");
  const manifest = await manifestResponse.json();
  py.FS.mkdirTree("/app/folding_practical");
  for (const [name, expected] of Object.entries(manifest.core_sha256)) {
    const response = await fetch(`./core/folding_practical/${name}`);
    if (!response.ok) throw new Error(`Missing analysis module: ${name}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    const sha = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2,"0")).join("");
    if (sha !== expected) throw new Error("Application assets are out of date. Reload the page before analysing data.");
    py.FS.writeFile(`/app/folding_practical/${name}`, bytes);
  }
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
      py.globals.set("_request_json", JSON.stringify(data.request));
      const text = py.runPython("json.dumps(dispatch(json.loads(_request_json)), allow_nan=False)");
      py.globals.delete("_request_json");
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
