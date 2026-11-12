// Exercise actual canvas input, coordinate round trips and JSON download/import.
// Temporary UI edits are test interactions, never physical mark annotations.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';

const filename=path.resolve(process.argv[2]);
const source=JSON.parse((await fs.readFile(filename,'utf8')).split('id="measurement-data">')[1].split('</script>')[0]);
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const context=await browser.newContext({viewport:{width:1440,height:1000},offline:true,acceptDownloads:true});
const page=await context.newPage(),failures=[],network=[];
page.on('pageerror',e=>failures.push(e.message));
page.on('request',r=>{if(/^https?:/.test(r.url()))network.push(r.url());});
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'measurement-review-'));
const state=()=>page.evaluate(()=>window.__measurementReview.getState());
try{
  await page.goto(pathToFileURL(filename).href);
  await page.waitForFunction(()=>window.__measurementReview?.getState().ready);
  assert.deepEqual((await state()).annotations,source.annotations);
  assert.equal(await page.locator('#image option').count(),source.photos.length);
  await page.locator('#center').click();
  await page.screenshot({path:path.join(path.dirname(filename),'review_desktop.png'),timeout:20000});
  const initial=await state(),pointName=initial.point,id=String(initial.imageId);
  const original=initial.annotations.points[pointName].observations[id].pixel;
  await page.locator('#review').click();
  assert.equal((await state()).annotations.points[pointName].observations[id].reviewed,true);
  assert.match(await page.locator('#results').textContent(),/Re-run Python/);
  await page.locator('#viewport').focus();await page.keyboard.press('ArrowRight');
  let current=(await state()).annotations.points[pointName].observations[id];
  assert.equal(current.reviewed,false);
  assert(Math.abs(Math.hypot(...current.pixel.map((v,k)=>v-original[k]))-.25)<1e-8);
  const screen=await page.evaluate(p=>window.__measurementReview.workingToScreen(p),current.pixel);
  const box=await page.locator('#viewport').boundingBox();
  await page.mouse.click(box.x+screen[0],box.y+screen[1]);
  const clicked=(await state()).annotations.points[pointName].observations[id].pixel;
  assert(Math.hypot(...clicked.map((v,k)=>v-current.pixel[k]))<.01,'Click must preserve the integer-pixel-center convention');
  const downloadPromise=page.waitForEvent('download');await page.locator('#download').click();
  const downloaded=await downloadPromise,file=path.join(temp,'annotations.json');await downloaded.saveAs(file);
  const edited=JSON.parse(await fs.readFile(file,'utf8'));
  assert.deepEqual(edited,(await state()).annotations);
  await page.locator('#remove').click();assert.equal((await state()).annotations.points[pointName].observations[id],undefined);
  await page.locator('#file').setInputFiles(file);
  await page.waitForFunction(({name,id})=>!!window.__measurementReview.getState().annotations.points[name].observations[id],{name:pointName,id});
  assert.deepEqual((await state()).annotations,edited);
  const bad=path.join(temp,'wrong_scene.json');await fs.writeFile(bad,JSON.stringify({...edited,scene_sha256:'wrong'}));
  await page.locator('#file').setInputFiles(bad);await page.waitForFunction(()=>document.getElementById('error').textContent.length>0);
  assert.match(await page.locator('#error').textContent(),/different scene/);
  assert.deepEqual((await state()).annotations,edited);
  const malformed=path.join(temp,'malformed.json');await fs.writeFile(malformed,JSON.stringify({...edited,points:{}}));
  await page.locator('#file').setInputFiles(malformed);await page.waitForFunction(()=>document.getElementById('error').textContent==='Invalid annotation structure');
  assert.deepEqual((await state()).annotations,edited);
  const other=source.photos.find(p=>!edited.points[pointName].observations[String(p.image_id)]);
  if(other){
    await page.locator('#image').selectOption(String(other.image_id));
    await page.waitForFunction(id=>window.__measurementReview.getState().ready&&window.__measurementReview.getState().imageId===id,other.image_id);
    assert.match(await page.locator('#coordinates').textContent(),/No mark/);
    await page.locator('#viewport').click({position:{x:box.width/2,y:box.height/2}});
    assert.equal((await state()).annotations.points[pointName].observations[String(other.image_id)].reviewed,false);
    await page.locator('#remove').click();
  }
  const before=await state(),r=await page.locator('#viewport').boundingBox();
  await page.mouse.move(r.x+r.width*.5,r.y+r.height*.5);await page.mouse.down();
  await page.mouse.move(r.x+r.width*.5+40,r.y+r.height*.5+30,{steps:5});await page.mouse.up();
  const panned=await state();assert(Math.abs(panned.tx-before.tx-40)<1e-8);
  await page.mouse.wheel(0,-150);await page.waitForFunction(s=>window.__measurementReview.getState().scale!==s,panned.scale);
  await page.setViewportSize({width:390,height:844});
  await page.locator('#fit').click();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  assert.deepEqual(network,[]);assert.deepEqual(failures,[]);
  console.log(JSON.stringify({status:'passed',offline:true,photos:source.photos.length,
    checks:['full-resolution canvas','EXIF-aware click round trip','quarter-pixel nudge','review reset',
      'stale results hidden','JSON download/import','wrong-scene rejection','missing-view edit',
      'pan and zoom','mobile layout'],physical_annotations_changed:false},null,2));
}finally{await browser.close();await fs.rm(temp,{recursive:true,force:true});}
