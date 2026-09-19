const $ = id => document.getElementById(id);
const STORE = "protein-folding-practical.session.v1";
const MAX_FILE = 2_000_000;
let project = null, plate = null, prepared = [], locks = {}, activeFit = 0, activePreparedIndex = -1;
let worker = null, nextId = 0, pending = new Map(), busy = false, revision = 0;
let saveTimer;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function message(text, isError = false) {
  const target = $(isError ? "error" : "notice");
  target.textContent = text; target.hidden = !text;
  $(isError ? "notice" : "error").hidden = true;
}
function setBusy(value) {
  busy = value; $("cancel").hidden = !value;
  $("runtime-dot").className = `status-dot ${value ? "loading" : worker ? "ready" : ""}`;
  updateControls();
}
function updateControls() {
  document.querySelectorAll("[data-project-field]").forEach(el => {el.disabled = !project || busy;});
  if (project && locks.temperature_k !== undefined && locks.temperature_k !== false) $("temperature").disabled = true;
  if (project && locks.model) $("model").disabled = true;
  for (const id of ["fit","save-project","save-csv"]) $(id).disabled = !project || busy;
  for (const id of ["save-report","save-figure"]) $(id).disabled = !project?.result || busy;
  for (const id of ["example","example-plate","series-file","project-file","plate-file"]) $(id).disabled = busy;
  $("prepare").disabled = !plate || busy;
  $("save-practical").disabled = !prepared.length || busy;
  $("group-picker").disabled = busy;
}
function ensureWorker() {
  if (worker) return;
  worker = new Worker(new URL("./worker.js", import.meta.url), {type:"module"});
  worker.onmessage = ({data}) => {
    if (data.type === "status") {$("runtime-status").textContent = data.message; return;}
    const job = pending.get(data.id);
    if (!job) return;
    if (data.response.error?.code === "runtime_initialization_failed") {
      // A fresh worker also clears cached failed module imports after network errors.
      stopWorker(`${data.response.error.message} Check your connection, then try again.`);
      return;
    }
    clearTimeout(job.timeout); pending.delete(data.id);
    if (data.response.ok) job.resolve(data.response.data);
    else job.reject(new Error(data.response.error?.message || "The analysis could not be completed."));
  };
  worker.onerror = event => {
    event.preventDefault(); stopWorker("The browser could not start Python. Check your connection, then try again.");
  };
}
function stopWorker(reason = "Cancelled. Data retained.") {
  worker?.terminate(); worker = null;
  for (const job of pending.values()) {clearTimeout(job.timeout); job.reject(new Error(reason));}
  pending.clear(); $("runtime-status").textContent = "Python stopped. The next action will restart it.";
  setBusy(false);
}
function request(payload) {
  ensureWorker(); const id = ++nextId;
  return new Promise((resolve,reject) => {
    const timeout = setTimeout(() => stopWorker("Python took too long. Check your connection or use fewer observations, then try again."), 240_000);
    pending.set(id,{resolve,reject,timeout}); worker.postMessage({id,request:payload});
  });
}
async function task(fn) {
  if (busy) return;
  $("error").hidden = true; setBusy(true);
  try {await fn();} catch (error) {message(error.message || String(error),true);}
  finally {setBusy(false);}
}
function syncPrepared() {
  if(project && activePreparedIndex>=0 && activePreparedIndex<prepared.length) {
    prepared[activePreparedIndex]=structuredClone(project);
    const option=$("group-picker").options[activePreparedIndex];
    if(option)option.textContent=project.settings.group_name||`Group ${activePreparedIndex+1}`;
  }
}
function autosave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    if (!project && !prepared.length) return;
    syncPrepared();
    try {
      localStorage.setItem(STORE,JSON.stringify({project,prepared,locks,selected_index:activePreparedIndex,model:$("model").value,saved_at:new Date().toISOString()}));
      $("autosave-state").textContent = "Saved locally · " + new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});
    } catch {$("autosave-state").textContent = "Browser storage is unavailable or full. Download a project to keep your work.";}
  }, 350);
}
function invalidate(reason="Settings changed. Refit required.") {
  revision++;
  if (project) {project.result = null; project.content_fingerprint = null;}
  $("fit-state").textContent = reason;
  renderPlots(); renderResults(); updateControls(); autosave();
}
function setProject(value, {keepLocks=false,preparedIndex=-1}={}) {
  syncPrepared(); activePreparedIndex=preparedIndex;
  project = value; revision++; activeFit = 0;
  if (!keepLocks) locks = {};
  $("group-name").value = project.settings.group_name || "";
  $("temperature").value = project.settings.temperature_k ?? 298.15;
  $("visual-midpoint").value = project.visual_midpoint_m ?? "";
  $("notes").value = project.notes || "";
  if (locks.model) $("model").value = locks.model;
  else if(project.result?.fit_request?.model) $("model").value=project.result.fit_request.model;
  $("group-picker").selectedIndex=activePreparedIndex;
  $("source-summary").replaceChildren(element("strong", project.source?.name || "Prepared group"), element("div", `${project.observations.length} observations · ${project.source?.kind || "local file"}`,"small muted"));
  $("fit-state").textContent = project.result ? "Fit restored" : "Ready to fit";
  $("preview-details").open = false;
  renderPreview(); renderPlots(); renderResults(); updateControls(); autosave();
}
function projectData(data) {return data.project || data;}
function selectedSignal(row) {return row[project?.settings.fit_signal || "raw_signal"];}
function number(value, digits=3) {return Number.isFinite(value) ? Number(value).toPrecision(digits) : "—";}
function metadataText() {
  if (!project) return "";
  const s=project.settings;
  return `${s.group_name || "Unnamed group"} · ${s.measurement || "Signal not recorded"} · excitation ${s.excitation_nm ?? "not recorded"} nm · emission ${s.wavelength_nm ?? "not recorded"} nm · ${s.temperature_k} K · ${s.fit_signal === "blank_corrected_signal" ? "blank-corrected" : "raw"} fluorescence`;
}
function renderPreview() {
  const body=$("preview").querySelector("tbody"); body.replaceChildren();
  if (!project) return;
  const excluded=project.observations.filter(row=>row.excluded).length;
  $("row-count").textContent = `${project.observations.length} rows · ${excluded} excluded`;
  $("measurement-summary").textContent = metadataText();
  for (const row of project.observations) {
    const tr=element("tr"); const use=document.createElement("input"); use.type="checkbox";
    use.checked=!row.excluded; use.setAttribute("aria-label",`Include ${row.row_id}`);
    const finite=Number.isFinite(row.concentration_m) && Number.isFinite(selectedSignal(row));
    use.disabled=!finite;
    const reason=document.createElement("input"); reason.type="text"; reason.value=row.exclusion_reason||"";
    reason.placeholder="Reason for excluding"; reason.setAttribute("aria-label",`Exclusion reason for ${row.row_id}`);
    reason.maxLength=500;
    use.addEventListener("change",()=>{
      row.excluded=!use.checked;
      if (!row.excluded) {row.exclusion_reason=null;reason.value="";}
      else if (!reason.value.trim()) {reason.focus();message("Add a reason for the excluded observation before fitting.");}
      invalidate("Observation selection changed. Fit again.");
      $("row-count").textContent=`${project.observations.length} rows · ${project.observations.filter(r=>r.excluded).length} excluded`;
    });
    reason.addEventListener("input",()=>{row.exclusion_reason=reason.value;invalidate("Exclusion rationale changed. Fit again.");});
    const source=`${row.source_row ?? row.row_id}${row.well ? " · "+row.well : ""}${row.acquisition?.acquisition_id ? " · "+row.acquisition.acquisition_id : ""}`;
    for (const item of [use,source,number(row.concentration_m,4),number(selectedSignal(row),5),reason]) {
      const td=element("td"); item instanceof Node ? td.append(item) : td.textContent=item;tr.append(td);
    }
    body.append(tr);
  }
}

const SVG="http://www.w3.org/2000/svg";
function svgNode(tag,attrs={},text) {
  const node=document.createElementNS(SVG,tag);
  for (const [key,value] of Object.entries(attrs)) node.setAttribute(key,String(value));
  if (text!==undefined) node.textContent=text;
  return node;
}
function chart(points, curve, residual=false, labels={}) {
  const width=760,height=residual?150:350,margin={left:65,right:20,top:18,bottom:45};
  const svg=svgNode("svg",{xmlns:SVG,viewBox:`0 0 ${width} ${height}`,role:"img","aria-label":labels.x?`Fluorescence by ${labels.x}`:residual?"Residuals by GuHCl concentration":"Fluorescence by GuHCl concentration"});
  const finite=points.filter(p=>Number.isFinite(p.x)&&Number.isFinite(p.y));
  const all=finite.concat(curve.filter(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
  if (!all.length) {svg.append(svgNode("text",{x:width/2,y:60,"text-anchor":"middle",fill:"#647277","font-size":13},"No finite observations to plot"));return svg;}
  let xmin=Math.min(...all.map(p=>p.x)),xmax=Math.max(...all.map(p=>p.x));
  let ymin=Math.min(...all.map(p=>p.y)),ymax=Math.max(...all.map(p=>p.y));
  if (residual) {const extent=Math.max(Math.abs(ymin),Math.abs(ymax),1e-6)*1.15;ymin=-extent;ymax=extent;}
  else {const padding=Math.max((ymax-ymin)*.12,Math.abs(ymax)*.02,1);ymin-=padding;ymax+=padding;}
  if (xmin===xmax) {xmin-=.5;xmax+=.5;}
  const sx=x=>margin.left+(x-xmin)/(xmax-xmin)*(width-margin.left-margin.right);
  const sy=y=>height-margin.bottom-(y-ymin)/(ymax-ymin)*(height-margin.top-margin.bottom);
  for (let i=0;i<=5;i++) {
    const x=xmin+(xmax-xmin)*i/5;
    svg.append(svgNode("text",{x:sx(x),y:height-24,"text-anchor":"middle",fill:"#647277","font-size":11,"font-family":"Arial"},Number(x.toPrecision(3))));
  }
  for (let i=0;i<=(residual?2:4);i++) {
    const y=ymin+(ymax-ymin)*i/(residual?2:4);
    svg.append(svgNode("line",{x1:margin.left,x2:width-margin.right,y1:sy(y),y2:sy(y),stroke:"#e5ebe6","stroke-width":1}));
    svg.append(svgNode("text",{x:margin.left-10,y:sy(y)+4,"text-anchor":"end",fill:"#647277","font-size":10,"font-family":"Arial"},Number(y.toPrecision(3))));
  }
  if (residual) svg.append(svgNode("line",{x1:margin.left,x2:width-margin.right,y1:sy(0),y2:sy(0),stroke:"#91a49a","stroke-dasharray":"4 4"}));
  svg.append(svgNode("text",{x:width/2,y:height-3,"text-anchor":"middle",fill:"#456169","font-size":11,"font-family":"Arial"},labels.x||"GuHCl concentration (M)"));
  if (!residual) svg.append(svgNode("text",{transform:`translate(15 ${height/2}) rotate(-90)`,"text-anchor":"middle",fill:"#456169","font-size":11,"font-family":"Arial"},labels.y||(project?.settings.fit_signal==="blank_corrected_signal"?"Blank-corrected fluorescence (a.u.)":"Raw fluorescence (a.u.)")));
  if (curve.length) svg.append(svgNode("path",{d:curve.filter(p=>Number.isFinite(p.y)).map((p,i)=>`${i?"L":"M"}${sx(p.x).toFixed(2)},${sy(p.y).toFixed(2)}`).join(" "),fill:"none",stroke:"#c38643","stroke-width":2.5}));
  for (const p of finite) {
    const mark=p.excluded?svgNode("path",{d:`M${sx(p.x)-3},${sy(p.y)-3}l6,6m0,-6l-6,6`,stroke:"#a4988c","stroke-width":1.5}):svgNode("circle",{cx:sx(p.x),cy:sy(p.y),r:residual?3:4.2,fill:"#1a6863",stroke:"white","stroke-width":1});
    mark.append(svgNode("title",{},`${p.id || "Observation"}: ${number(p.x,4)} ${labels.unit||"M"}; ${number(p.y,5)}${p.excluded?" (excluded)":""}`));svg.append(mark);
  }
  return svg;
}
function currentFit() {return project?.result?.fits?.[activeFit] || null;}
function renderPlots() {
  if (!project) return;
  const fit=currentFit();
  const points=project.observations.map(r=>({x:r.concentration_m,y:selectedSignal(r),excluded:r.excluded,id:r.row_id}));
  const curve=(fit?.curve?.x||[]).map((x,i)=>({x,y:fit.curve.y[i]}));
  $("plot").replaceChildren(chart(points,curve));
  $("plot").setAttribute("aria-label",`Denaturation curve with ${points.length} observations${fit?.success?" and fitted "+fit.model_name:""}`);
  if (fit?.success) {
    const residuals=(fit.observed.x||[]).map((x,i)=>({x,y:fit.observed.residuals[i],id:fit.observed.row_ids?.[i]}));
    $("residual-plot").replaceChildren(chart(residuals,[],true));
  } else $("residual-plot").replaceChildren(element("p","No fit","muted small"));
}
const PARAM_LABELS={delta_g_h2o_kj_mol:["ΔG° unfolding","kJ mol⁻¹"],m_value_kj_mol_m:["m-value","kJ mol⁻¹ M⁻¹"],cm_m:["Midpoint Cₘ","M"],midpoint_m:["Midpoint Cₘ","M"],width_m:["Transition width","M"],low_denaturant_signal:["Low-denaturant baseline","a.u."],high_denaturant_signal:["High-denaturant baseline","a.u."]};
function renderResults() {
  const content=$("result-content"),badge=$("result-badge"); content.replaceChildren();
  badge.className="badge";
  if (!project?.result) {
    badge.textContent=project?"Ready to fit":"Awaiting data";
    return;
  }
  const result=project.result;
  if (result.fits.length>1) {
    const tabs=element("div");
    result.fits.forEach((fit,i)=>{const button=element("button",fit.model_name,`fit-tab ${i===activeFit?"active":""}`);button.addEventListener("click",()=>{activeFit=i;renderResults();renderPlots();});tabs.append(button);});content.append(tabs);
    content.append(element("p",result.preferred_model ? `Preferred statistical fit: ${result.preferred_model}.` : "No statistical preference.","small"));
    const table=element("table",undefined,"comparison");const head=element("tr");
    for (const text of ["Model","AICc","RMSE","Interpretation"]) head.append(element("th",text));table.append(head);
    result.fits.forEach(f=>{const tr=element("tr");for(const text of [f.model_name,number(f.metrics?.aicc,5),number(f.metrics?.rmse,4),(f.interpretation_status||"fit_failed").replaceAll("_"," ")]) tr.append(element("td",text));table.append(tr);});content.append(table);
  }
  const fit=currentFit(); if (!fit) return;
  const status=fit.interpretation_status||"fit_failed";
  badge.textContent=status.replaceAll("_"," ");
  badge.classList.add(status==="interpretable"?"success":status==="fit_failed"?"failure":"caution");
  content.append(element("p",`${fit.model_name}${fit.success?"":" · fit failed"}${fit.message?": "+fit.message:""}`,"state-message"));
  if (fit.success) {
    const stats=element("div",undefined,"stats");
    const keys=fit.parameters.cm_m!==undefined?["cm_m","delta_g_h2o_kj_mol","m_value_kj_mol_m"]:["midpoint_m","width_m","low_denaturant_signal"];
    for(const key of keys){const item=element("div",undefined,"stat"),[label,unit]=PARAM_LABELS[key];item.append(element("small",label),element("strong",number(fit.parameters[key],4)),element("small",`${unit} · SE ${number(fit.standard_errors?.[key],3)}`));stats.append(item);}content.append(stats);
  }
  const warnings=fit.warnings||[];
  if(warnings.length){const box=element("div",undefined,"warnings");warnings.forEach(w=>box.append(element("p",w)));content.append(box);}
  const details=element("details");details.append(element("summary","Parameters & diagnostics"));
  details.append(element("pre",JSON.stringify({parameters:fit.parameters,standard_errors:fit.standard_errors,metrics:fit.metrics,diagnostics:fit.diagnostics},null,2),"details-json"));content.append(details);
}

async function readFile(file) {
  if(!file) return null;
  if(file.size>MAX_FILE) throw new Error("File exceeds 2 MB. Use a smaller CSV or project.");
  const bytes=await file.arrayBuffer();
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),b=>b.toString(16).padStart(2,"0")).join("");
  let text,encoding="utf-8";
  try{text=new TextDecoder("utf-8",{fatal:true}).decode(bytes);}catch{encoding="windows-1252";text=new TextDecoder(encoding).decode(bytes);}
  return {filename:file.name,text,source_bytes_sha256:hash,source_encoding:encoding};
}
function download(text,filename,mime="application/json") {
  const link=element("a");const url=URL.createObjectURL(new Blob([text],{type:mime}));
  link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10_000);
}
async function exportAction(action) {
  const data=await request({action,project,include_excluded:true});download(data.text,data.filename,data.mime);
  if(action==="export_legacy_csv") message("Raw CSV exported. Only project JSON preserves exclusions, blank correction and metadata.");
}
function renderGroups() {
  const select=$("group-picker");select.replaceChildren();
  prepared.forEach((p,i)=>{const option=element("option",p.settings.group_name||`Group ${i+1}`);option.value=i;select.append(option);});
  select.hidden=prepared.length<2;$("group-picker-label").hidden=select.hidden;
  $("prepared-count").textContent=prepared.length;updateControls();
}
function saveFigure() {
  const main=$("plot").querySelector("svg"),residual=$("residual-plot").querySelector("svg");
  if(!main) return;
  const svg=svgNode("svg",{xmlns:SVG,width:960,height:720,viewBox:"0 0 960 720"});
  svg.append(svgNode("rect",{width:960,height:720,fill:"white"}));
  svg.append(svgNode("text",{x:50,y:35,"font-size":22,fill:"#19383e","font-family":"Arial"},project.settings.group_name||"Protein folding practical"));
  svg.append(svgNode("text",{x:50,y:62,"font-size":11,fill:"#456169","font-family":"Arial"},metadataText()));
  const first=main.cloneNode(true);first.setAttribute("x","40");first.setAttribute("y","90");first.setAttribute("width","880");first.setAttribute("height","405");svg.append(first);
  if(residual){const second=residual.cloneNode(true);second.setAttribute("x","40");second.setAttribute("y","500");second.setAttribute("width","880");second.setAttribute("height","175");svg.append(second);}
  svg.append(svgNode("text",{x:50,y:700,"font-size":11,fill:"#456169","font-family":"Arial"},`${currentFit()?.model_name||""} · ${currentFit()?.interpretation_status||""} · ${project.software_version}`));
  download(new XMLSerializer().serializeToString(svg),"folding-figure.svg","image/svg+xml");
}

$("example").addEventListener("click",()=>task(async()=>{const data=await request({action:"example"});setProject(projectData(data));message("Example loaded.");}));
$("series-file").addEventListener("change",event=>task(async()=>{const file=await readFile(event.target.files[0]);event.target.value="";if(!file)return;setProject(projectData(await request({action:"import_series",...file})));message("CSV loaded.");}));
$("project-file").addEventListener("change",event=>task(async()=>{
  const file=await readFile(event.target.files[0]);event.target.value="";if(!file)return;
  const parsed=JSON.parse(file.text);
  if(parsed.schema_version==="practical-1.0"){
    const data=await request({action:"load_practical",...file});const practical=data.practical||data;
    activePreparedIndex=-1;prepared=practical.projects;locks=practical.instructor_locks||{};renderGroups();setProject(structuredClone(prepared[practical.selected_index||0]),{keepLocks:true,preparedIndex:practical.selected_index||0});
  }else {activePreparedIndex=-1;prepared=[];renderGroups();setProject(projectData(await request({action:"load_project",...file})));}
  message("Project loaded. Refit required.");
}));
$("group-picker").addEventListener("change",()=>{const index=Number($("group-picker").value);syncPrepared();setProject(structuredClone(prepared[index]),{keepLocks:true,preparedIndex:index});});
$("group-name").addEventListener("input",()=>{if(project){project.settings.group_name=$("group-name").value;invalidate();}});
$("temperature").addEventListener("input",()=>{if(project){project.settings.temperature_k=$("temperature").value===""?null:Number($("temperature").value);invalidate();}});
$("visual-midpoint").addEventListener("input",()=>{if(project){project.visual_midpoint_m=$("visual-midpoint").value===""?null:Number($("visual-midpoint").value);autosave();}});
$("notes").addEventListener("input",()=>{if(project){project.notes=$("notes").value;autosave();}});
$("model").addEventListener("change",()=>invalidate("Model changed. Refit required."));
$("fit").addEventListener("click",()=>task(async()=>{
  const currentRevision=revision;
  const invalid=project.observations.find(r=>r.excluded&&!r.exclusion_reason?.trim());
  if(invalid){$("preview-details").open=true;throw new Error(`Add an exclusion reason for ${invalid.row_id}.`);}
  $("fit-state").textContent="Fitting…";
  const data=await request({action:"fit",project,model:$("model").value});
  if(currentRevision!==revision)return;
  project=data.project;activeFit=0;renderResults();renderPlots();autosave();
  $("fit-state").textContent="Fit complete";
}));
for(const [id,action] of [["save-project","export_project"],["save-csv","export_legacy_csv"],["save-report","export_report"]]) $(id).addEventListener("click",()=>task(()=>exportAction(action)));
$("save-figure").addEventListener("click",saveFigure);
$("cancel").addEventListener("click",()=>stopWorker());
$("reset").addEventListener("click",()=>{
  revision++;clearTimeout(saveTimer);if(busy)stopWorker();project=null;plate=null;prepared=[];locks={};activePreparedIndex=-1;
  try{localStorage.removeItem(STORE);}catch{}location.reload();
});
$("recover").addEventListener("click",()=>task(async()=>{
  const saved=JSON.parse(localStorage.getItem(STORE));
  prepared=[];activePreparedIndex=-1;
  for(const p of (saved.prepared||[])) prepared.push(projectData(await request({action:"validate_project",project:p})));
  locks=saved.locks||{};renderGroups();
  if(saved.project)setProject(projectData(await request({action:"validate_project",project:saved.project})),{keepLocks:true,preparedIndex:saved.selected_index??-1});
  if(saved.model)$("model").value=saved.model;
  $("recovery").hidden=true;message("Session recovered. Refit required.");
}));
$("discard").addEventListener("click",()=>{try{localStorage.removeItem(STORE);}catch{}$("recovery").hidden=true;});

function fillSelect(select,items,label=String) {
  select.replaceChildren();items.forEach(item=>{const option=element("option",label(item));option.value=item??"";select.append(option);});
}
function currentMeasurement(){return plate?.measurements.find(m=>m.plate_id===$("plate-id").value&&m.measurement===$("measurement").value);}
function updateMeasurementOptions(){
  const m=currentMeasurement();fillSelect($("wavelength"),m?.wavelengths_nm?.length?m.wavelengths_nm:[""],v=>v===""?"Not recorded":`${v} nm`);
  fillSelect($("acquisition"),m?.acquisition_ids||[]);renderPlateMap();
}
function updatePlateOptions(){
  fillSelect($("measurement"),[...new Set(plate.measurements.filter(m=>m.plate_id===$("plate-id").value).map(m=>m.measurement))]);updateMeasurementOptions();
}
function loadPlate(value){
  plate=value;$("plate-source").textContent=`${plate.source.name} · ${plate.rows.length} records`;
  fillSelect($("plate-id"),plate.plate_ids);updatePlateOptions();updateControls();
}
function orderedWells(){return $("assigned-wells").value.split(/[\s,;]+/).filter(Boolean).map(v=>v.toUpperCase());}
function renderPlateMap(){
  const grid=$("plate-map");grid.replaceChildren();const selected=orderedWells();
  const available=new Set((plate?.rows||[]).filter(r=>r.plate_id===$("plate-id").value&&r.measurement===$("measurement").value).map(r=>r.well));
  for(const row of "ABCDEFGH")for(let col=1;col<=12;col++){
    const well=`${row}${col}`,index=selected.indexOf(well);const button=element("button",well,`well ${index>=0?"selected":""} ${!available.has(well)?"unavailable":""}`);
    button.type="button";button.disabled=!available.has(well);button.setAttribute("aria-pressed",String(index>=0));button.setAttribute("aria-label",`${well}${index>=0?`, condition ${index+1}`:""}`);
    button.addEventListener("click",()=>{const current=orderedWells();const at=current.indexOf(well);if(at>=0)current.splice(at,1);else current.push(well);$("assigned-wells").value=current.join(", ");renderPlateMap();});grid.append(button);
  }
  renderSpectrum();
}
function renderSpectrum(){
  const rows=(plate?.rows||[]).filter(r=>r.plate_id===$("plate-id").value&&r.measurement===$("measurement").value&&Number.isFinite(r.wavelength_nm));
  const well=orderedWells()[0]||rows[0]?.well;
  const selected=rows.filter(r=>r.well===well&&($("repeat-policy").value!=="select"||r.acquisition_id===$("acquisition").value));
  $("spectrum-preview").hidden=!selected.length;
  if(!selected.length)return;
  const ids=[...new Set(selected.map(r=>r.acquisition_id))];
  $("spectrum-caption").textContent=`${well} · ${$("measurement").value} · ${ids.join(", ")} · unaveraged`;
  $("spectrum-plot").replaceChildren(chart(selected.map(r=>({x:r.wavelength_nm,y:r.value,id:`${r.well} / ${r.acquisition_id}`})),[],false,{x:"Emission wavelength (nm)",y:"Raw fluorescence (a.u.)",unit:"nm"}));
}
$("plate-file").addEventListener("change",event=>task(async()=>{const file=await readFile(event.target.files[0]);event.target.value="";if(!file)return;const data=await request({action:"import_plate",...file});loadPlate(data.plate);message("Plate loaded.");}));
$("example-plate").addEventListener("click",()=>task(async()=>{
  const data=await request({action:"example_plate"});loadPlate(data.plate);
  const setup=data.prepare_group_request;
  if(setup.selection.plate_id) {$("plate-id").value=setup.selection.plate_id;updatePlateOptions();}
  if(setup.selection.measurement) {$("measurement").value=setup.selection.measurement;updateMeasurementOptions();}
  if(setup.selection.wavelength_nm!=null) $("wavelength").value=setup.selection.wavelength_nm;
  const policy=setup.selection.repeat_policy||{mode:"reject"};
  $("repeat-policy").value=policy.mode;$("replicate-label").value=policy.label||"";
  if(policy.selected_acquisition_id) $("acquisition").value=policy.selected_acquisition_id;
  $("blank-enabled").checked=!!setup.blank_correction?.enabled;
  $("blank-wells").value=(setup.blank_correction?.blank_wells||[]).join(", ");
  $("assigned-wells").value=setup.wells.map(w=>w.well).join(", ");$("concentrations").value=setup.wells.map(w=>w.concentration_m).join(", ");
  $("prepared-name").value=setup.selection.group_name;renderPlateMap();message("Example plate loaded.");
}));
$("plate-id").addEventListener("change",updatePlateOptions);$("measurement").addEventListener("change",updateMeasurementOptions);$("assigned-wells").addEventListener("input",renderPlateMap);
$("repeat-policy").addEventListener("change",renderSpectrum);$("acquisition").addEventListener("change",renderSpectrum);
$("prepare").addEventListener("click",()=>task(async()=>{
  const wells=orderedWells(),concentrations=$("concentrations").value.split(/[\s,;]+/).filter(Boolean).map(Number);
  if(wells.length!==concentrations.length)throw new Error("Provide one concentration for every ordered well.");
  const m=currentMeasurement();
  const data=await request({action:"prepare_group",plate,selection:{group_name:$("prepared-name").value,plate_id:$("plate-id").value,measurement:$("measurement").value,wavelength_nm:$("wavelength").value===""?null:Number($("wavelength").value),excitation_nm:m?.excitations_nm?.length===1?m.excitations_nm[0]:null,temperature_k:298.15,repeat_policy:{mode:$("repeat-policy").value,selected_acquisition_id:$("repeat-policy").value==="select"?$("acquisition").value||null:null,technical_replicates:$("repeat-policy").value==="mean",label:$("replicate-label").value}},wells:wells.map((well,i)=>({well,concentration_m:concentrations[i]})),blank_correction:{enabled:$("blank-enabled").checked,method:"mean",blank_wells:$("blank-wells").value.split(/[\s,;]+/).filter(Boolean)}});
  const p=projectData(data);const duplicate=prepared.some(g=>g.settings.group_name.toLowerCase()===p.settings.group_name.toLowerCase());
  if(duplicate)throw new Error("Duplicate group name. Choose a unique name.");
  syncPrepared();prepared.push(structuredClone(p));renderGroups();setProject(p,{preparedIndex:prepared.length-1});
  message("Group added.");
}));
$("save-practical").addEventListener("click",()=>task(async()=>{
  syncPrepared();
  const data=await request({action:"export_practical",projects:prepared,title:"Protein folding practical",selected_index:Math.max(0,activePreparedIndex),instructor_locks:$("lock-settings").checked?{temperature_k:true,model:$("model").value}:{}});
  download(data.text,data.filename,data.mime);
}));
try{$("recovery").hidden=!localStorage.getItem(STORE);}catch{$("autosave-state").textContent="Browser storage is unavailable. Download a project to retain your work.";}
updateControls();renderPlateMap();
