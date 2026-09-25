import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { inflateRawSync } from "node:zlib";

const LONG_TIMEOUT = 240_000;

async function complete(page) {
  await expect(page.locator("#cancel")).toBeHidden({timeout: LONG_TIMEOUT});
  await expect(page.locator("#error")).toBeHidden();
}
async function downloadFrom(page, locator) {
  const [download] = await Promise.all([page.waitForEvent("download", {timeout: LONG_TIMEOUT}),locator.click()]);
  const buffer=await readFile(await download.path());
  await complete(page);
  return {buffer,filename:download.suggestedFilename()};
}
function unzipCsvs(buffer) {
  const files=new Map();
  let offset=0;
  while(buffer.readUInt32LE(offset)===0x04034b50){
    const method=buffer.readUInt16LE(offset+8),size=buffer.readUInt32LE(offset+18);
    const nameLength=buffer.readUInt16LE(offset+26),extraLength=buffer.readUInt16LE(offset+28);
    const name=buffer.subarray(offset+30,offset+30+nameLength).toString("utf8");
    const start=offset+30+nameLength+extraLength;
    const compressed=buffer.subarray(start,start+size);
    files.set(name,(method===8?inflateRawSync(compressed):compressed).toString("utf8"));
    offset=start+size;
  }
  return files;
}

test("multiple plates and a group list yield separate 16-point student CSVs", async ({page},testInfo)=>{
  await page.goto("/");
  await page.locator("#instructor > summary").click();
  await page.locator("#group-list-file").setInputFiles({
    name:"groups.csv",mimeType:"text/csv",
    buffer:Buffer.from("group name,plate number,well ranges\nGroup A,P1,A1-B4\nGroup B,P2,B5-C8\nMissing,P9,A1-B4\n"),
  });
  await complete(page);
  await expect(page.locator("#prepare-group-list")).toBeDisabled();
  await page.locator("#plate-file").setInputFiles(["examples/P1.csv","examples/P2.csv"]);
  await complete(page);
  await expect(page.locator("#plate-source")).toContainText("2 plates");
  await expect(page.locator("#wavelength")).toHaveValue("508");
  await page.locator("#prepare-group-list").click();
  await complete(page);
  await expect(page.locator("#group-list-status")).toHaveText("2 of 3 groups prepared · 1 need attention.");
  await expect(page.locator("#group-list-errors")).toContainText("P9");
  await expect(page.locator("#prepared-count")).toHaveText("2");
  await expect(page.locator("#row-count")).toContainText("16 rows");
  await expect(page.locator("#preview tbody tr")).toHaveCount(0);
  await page.screenshot({path:testInfo.outputPath("groups-desktop.png"),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath("groups-mobile.png"),fullPage:true});
  await page.setViewportSize({width:1280,height:720});

  const archive=await downloadFrom(page,page.locator("#save-group-csvs"));
  expect(archive.filename).toBe("group-csvs.zip");
  const files=unzipCsvs(archive.buffer);
  expect([...files.keys()]).toEqual(["Group_A.csv","Group_B.csv"]);
  for(const csv of files.values()){
    const lines=csv.trim().split("\n");
    expect(lines).toHaveLength(17);
    expect(lines[0]).toBe("GuHCl concentration (M),raw fluorescence values,normalized fluorescence values");
    expect(lines[1].split(",")[0]).toBe("0.0");
    expect(lines[16].split(",")[0]).toBe("6.0");
    const normalized=lines.slice(1).map(line=>Number(line.split(",")[2]));
    expect(Math.min(...normalized)).toBe(0);
    expect(Math.max(...normalized)).toBe(1);
  }
  const individual=await downloadFrom(page,page.getByRole("button",{name:"Download CSV for Group B",exact:true}));
  expect(individual.buffer.toString("utf8")).toBe(files.get("Group_B.csv"));

  // A malformed project must not remove prepared groups or their downloads.
  await page.locator("#project-file").setInputFiles({name:"invalid.json",mimeType:"application/json",buffer:Buffer.from('{"schema_version":"1.0"}')});
  await expect(page.locator("#error")).toBeVisible({timeout:LONG_TIMEOUT});
  await expect(page.locator("#prepared-count")).toHaveText("2");
  await expect(page.locator("#save-group-csvs")).toBeEnabled();

  await page.locator("#group-name").fill("Renamed group");
  await expect(page.getByRole("button",{name:"Download CSV for Renamed group",exact:true})).toBeVisible();
  await expect(page.locator("#group-export-rows tr").first()).toContainText("Renamed group");

  await page.locator("#series-file").setInputFiles({name:"Group_B.csv",mimeType:"text/csv",buffer:Buffer.from(files.get("Group_B.csv"))});
  await complete(page);
  await expect(page.locator("#row-count")).toContainText("16 rows · 0 excluded");
  await page.locator("#preview-details > summary").click();
  await expect(page.locator("#preview tbody tr")).toHaveCount(16);
  await page.locator("#fit").click();
  await complete(page);
  // Exclusion inputs must still target the current project after fitting.
  const first=page.locator("#preview tbody tr").first();
  await first.locator('input[type="checkbox"]').uncheck();
  await first.locator('input[type="text"]').fill("Check this measurement");
  const exported=await downloadFrom(page,page.locator("#save-project"));
  const project=JSON.parse(exported.buffer.toString("utf8"));
  expect(project.observations[0].excluded).toBe(true);
  expect(project.observations[0].exclusion_reason).toBe("Check this measurement");
  expect(project.result).toBeNull();
});

test("canceling a file read prevents late data from replacing a newer upload", async ({page})=>{
  await page.addInitScript(()=>{
    const original=File.prototype.arrayBuffer;
    window.delayedReadStarted=false;
    window.releaseDelayedRead=null;
    File.prototype.arrayBuffer=async function(){
      if(this.name==="delayed.csv"){
        window.delayedReadStarted=true;
        await new Promise(resolve=>{window.releaseDelayedRead=resolve;});
      }
      return original.call(this);
    };
  });
  await page.goto("/");
  await page.locator("#series-file").setInputFiles({
    name:"delayed.csv",mimeType:"text/csv",buffer:Buffer.from("concentration,signal\n0,10\n3,8\n6,2\n"),
  });
  await expect.poll(()=>page.evaluate(()=>window.delayedReadStarted)).toBe(true);
  await page.locator("#cancel").click();
  await expect(page.locator("#cancel")).toBeHidden();
  await expect(page.locator("#notice")).toContainText("Canceled");
  await page.locator("#series-file").setInputFiles({
    name:"current.csv",mimeType:"text/csv",buffer:Buffer.from("concentration,signal\n0,30\n3,20\n6,5\n"),
  });
  await page.evaluate(()=>window.releaseDelayedRead());
  await complete(page);
  await expect(page.locator("#source-summary")).toContainText("current.csv");
  await expect(page.locator("#error")).toBeHidden();
});
