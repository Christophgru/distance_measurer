// Real-browser regression, including GPU ID picking and a synthetic 100k cloud.
// Run: node smoke.mjs /absolute/path/to/exported/index.html
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';

const filename = path.resolve(process.argv[2]);
const html = await fs.readFile(filename, 'utf8');
const jsonPattern = /(<script type="application\/json" id="scene-data">)([\s\S]*?)(<\/script>)/;
const source = JSON.parse(html.match(jsonPattern)[2]);
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--disable-dev-shm-usage'] });
const failures = [], network = [];
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, offline: true });
const page = await context.newPage();
page.on('pageerror', e => failures.push(e.message));
page.on('console', m => { if(m.type()==='error') failures.push(m.text()); });
page.on('request', r => { if(/^https?:/.test(r.url())) network.push(r.url()); });
const state = () => page.evaluate(() => window.__reconstructionViewer.getState());
const waitFrame = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
let temp;
try {
  await page.goto(pathToFileURL(filename).href);
  await page.waitForFunction(() => window.__reconstructionViewer?.getState().drawCalls > 0);
  assert.equal(await page.locator('#error').isVisible(), false);
  assert.equal((await state()).pointCount, source.point_count);
  assert.deepEqual((await state()).cameraIds, source.cameras.map(c=>c.image_id));
  const firstId = Buffer.from(source.point_ids,'base64').readUInt32LE(0);
  await page.locator('#point-id').fill(String(firstId)); await page.locator('#point-id').press('Enter');
  assert.equal((await state()).selectedPointId, firstId);
  await page.locator('#clear-point').click();
  await page.locator('#overview').click(); await waitFrame();
  const rect = await page.locator('#viewport').boundingBox();
  let picked = false;
  for(let row=0;row<Math.min(300,source.point_count);row++) {
    const p = await page.evaluate(row=>window.__reconstructionViewer.projectPoint(row),row);
    if(p.z < -1 || p.z > 1 || p.x<25 || p.x>rect.width-25 || p.y<25 || p.y>rect.height-25) continue;
    await page.mouse.click(rect.x+p.x,rect.y+p.y); await waitFrame();
    if((await state()).selectedPointId !== null){picked=true;break;}
  }
  assert(picked, 'An actual GPU point click must select a persisted point ID');
  const beforeOrbit=await state();
  await page.mouse.move(rect.x+rect.width*.7,rect.y+rect.height*.6);
  await page.mouse.down(); await page.mouse.move(rect.x+rect.width*.7+90,rect.y+rect.height*.6+40,{steps:8}); await page.mouse.up(); await waitFrame();
  assert.notDeepEqual((await state()).cameraPosition,beforeOrbit.cameraPosition);
  const beforePan=await state();
  await page.mouse.down({button:'right'});await page.mouse.move(rect.x+rect.width*.6,rect.y+rect.height*.5,{steps:8});await page.mouse.up({button:'right'});await waitFrame();
  assert.notDeepEqual((await state()).target,beforePan.target);
  const beforeZoom=await state();await page.mouse.wheel(0,-200);await waitFrame();
  assert.notDeepEqual((await state()).cameraPosition,beforeZoom.cameraPosition);
  await page.locator('#max-error').evaluate(input=>{input.value='0';input.dispatchEvent(new Event('input'));});await waitFrame();
  const errors=new Float32Array(Uint8Array.from(Buffer.from(source.errors,'base64')).buffer);
  assert.equal((await state()).visibleCount,Array.from(errors).filter(x=>x<=0).length);
  await page.locator('#max-error').evaluate(input=>{input.value=input.max;input.dispatchEvent(new Event('input'));});
  await page.locator('#min-views').evaluate(input=>{input.value=input.max;input.dispatchEvent(new Event('input'));});await waitFrame();
  const counts=new Uint32Array(Uint8Array.from(Buffer.from(source.view_counts,'base64')).buffer);
  assert.equal((await state()).visibleCount,Array.from(counts).filter(x=>x>=Math.max(2,source.camera_count)).length);
  await page.locator('#min-views').evaluate(input=>{input.value='2';input.dispatchEvent(new Event('input'));});
  if(source.densification){
    await page.locator('#show-dense').uncheck();await waitFrame();
    assert.equal((await state()).visibleCount,source.densification.original_point_count);
    await page.locator('#show-dense').check();await waitFrame();
    assert.equal((await state()).visibleCount,source.point_count);
  }
  await page.locator(`[data-camera-id="${source.cameras[0].image_id}"]`).click();
  await page.locator('#focus-camera').click();await waitFrame();
  const target=(await state()).target;
  source.cameras[0].center.forEach((x,k)=>assert(Math.abs(x-target[k])<1e-8));
  await page.locator('#camera-view').click();await waitFrame();
  const view=await state(),forward=view.target.map((x,k)=>x-view.cameraPosition[k]),norm=Math.hypot(...forward);
  source.cameras[0].R_cw[2].forEach((x,k)=>assert(Math.abs(x-forward[k]/norm)<1e-8));
  if(source.has_photo_colors){await page.locator('#show-photos').check();await waitFrame();assert(await page.locator('#photo').evaluate(c=>c.width>0));await page.locator('#show-photos').uncheck();}
  await page.locator('#overview').click();await page.locator('#clear-point').click();await waitFrame();
  await page.screenshot({path:path.join(path.dirname(filename),'browser_smoke.png')});
  const desktop=await state();
  const mobile=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,offline:true});
  const phone=await mobile.newPage();phone.on('pageerror',e=>failures.push(e.message));
  await phone.goto(pathToFileURL(filename).href);await phone.waitForFunction(()=>window.__reconstructionViewer?.getState().drawCalls>0);
  assert.equal(await phone.locator('#sidebar').isVisible(),false);
  assert((await phone.locator('#viewport').boundingBox()).width>350);
  await phone.locator('#toggle-panel').tap();assert(await phone.locator('#sidebar').isVisible());await mobile.close();

  // Synthetic fixture only: never replace or enlarge the user's reconstructed cloud.
  const n=100000, fixture={...source,session_id:'SYNTHETIC renderer throughput fixture',point_count:n,has_photo_colors:false};
  fixture.cameras=source.cameras.map(c=>({...c,photo:null}));
  const positions=new Float32Array(n*3),colors=new Uint8Array(n*3),ids=new Uint32Array(n),e=new Float32Array(n),vc=new Uint32Array(n),off=new Uint32Array(n+1),images=new Uint32Array(2*n),pixels=new Float32Array(4*n),oe=new Float32Array(2*n);
  for(let i=0;i<n;i++){
    positions.set([(i%100)/20-2.5,(Math.floor(i/100)%100)/20-2.5,Math.floor(i/10000)/4],i*3);
    colors.set([90,200,220],i*3);ids[i]=i;e[i]=.5;vc[i]=2;off[i]=2*i;
    images[2*i]=source.cameras[0].image_id;images[2*i+1]=source.cameras[1].image_id;
  }off[n]=2*n;
  for(const [key,array] of Object.entries({positions,colors,point_ids:ids,track_ids:ids,errors:e,view_counts:vc,observation_offsets:off,observation_images:images,observation_pixels:pixels,observation_errors:oe}))fixture[key]=Buffer.from(array.buffer).toString('base64');
  temp=await fs.mkdtemp(path.join(os.tmpdir(),'reconstruction-renderer-'));
  const bigFile=path.join(temp,'synthetic.html');await fs.writeFile(bigFile,html.replace(jsonPattern,(_,open,_old,close)=>open+JSON.stringify(fixture)+close));
  await page.goto(pathToFileURL(bigFile).href);await page.waitForFunction(()=>window.__reconstructionViewer?.getState().drawCalls>0);await waitFrame();
  const large=await state();assert.equal(large.pointCount,n);assert(large.drawCalls<30,'Cloud draw calls must not scale with point count');
  assert.deepEqual(network,[]);assert.deepEqual(failures,[]);
  console.log(JSON.stringify({desktop,synthetic100k:large,orbit:true,pan:true,zoom:true,gpuPicking:true,filters:true,cameraViews:true,mobile:true,offline:true,errors:failures},null,2));
} finally {await browser.close();if(temp)await fs.rm(temp,{recursive:true,force:true});}
