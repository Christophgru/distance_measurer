(() => {
  'use strict';
  const payload=JSON.parse(document.getElementById('measurement-data').textContent);
  let annotations=structuredClone(payload.annotations), dirty=false, ready=false;
  const $=id=>document.getElementById(id), canvas=$('viewport'), ctx=canvas.getContext('2d');
  const photos=new Map(payload.photos.map(p=>[String(p.image_id),p]));
  const images=new Map(); let scale=1, tx=0, ty=0, drag=null, token=0;
  const transform=(m,p)=>[m[0][0]*p[0]+m[0][1]*p[1]+m[0][2],m[1][0]*p[0]+m[1][1]*p[1]+m[1][2]];
  const photo=()=>photos.get($('image').value), point=()=>annotations.points[$('point').value];
  const observation=()=>point().observations[$('image').value];
  function changed(){dirty=true;update();draw();}
  function options(){
    $('point').replaceChildren(...Object.keys(annotations.points).map(name=>new Option(name,name)));
  }
  function summarize(report){
    if(!report)return 'Run Python to calculate lengths.';
    const labels={
      visual_cameras_unverified:'Camera geometry still needs visual validation.',
      observations_unreviewed:'Some mark locations have not been confirmed.',
      missing_observations:'Some selected photos are missing required marks.',
      triangulation_rejected:'Some marks fail the geometric checks.',
      additional_view_repeatability_missing:'A second supported view group is still needed.',
      held_out_error_over_10_percent:'A held-out length differs by more than 10%.',
      localization_sensitivity_over_10_percent:'Small mark shifts change a length by more than 10%.',
      sensitivity_geometry_unstable:'Small mark shifts often fail geometric checks.',
      repeatability_over_10_percent:'View groups disagree by more than 10%.',
      reference_repeatability_over_10_percent:'The same reference span disagrees across views at the fixed scale.',
      primary_reference_geometry_unavailable:'The first view group cannot establish a reference scale.',
      held_out_known_length_missing:'An independently known test length is still needed.'
    };
    const lines=['Exploratory results; metric accuracy remains unaccepted.',`Scale set by JPEGs ${report.scale_reference_image_ids.join(', ')} and kept fixed across views.`,'',...report.blocking_reasons.map(r=>'• '+(labels[r]||r))];
    for(const g of report.view_groups){
      lines.push('',`JPEGs ${g.image_ids.join(', ')}`);
      if(g.missing_observations.length){lines.push(`${g.missing_observations.length} required marks missing.`);continue;}
      if(!g.geometry_passed){
        for(const [name,p] of Object.entries(g.points||{}))if(p.rejection_reason)lines.push(`${name}: ${p.rejection_reason.replaceAll('_',' ')}`);
        continue;
      }
      if(g.reference_remeasured_length_m!==undefined)lines.push(`Reference remeasured: ${(g.reference_remeasured_length_m*100).toFixed(2)} cm.`);
      for(const m of g.measurements){
        lines.push(`${m.id}: ${(m.length_m*100).toFixed(2)} cm`);
        if(m.expected_length_m!==undefined)lines.push(`Known ${(m.expected_length_m*100).toFixed(2)} cm; error ${m.signed_error_percent.toFixed(1)}%.`);
        if(m.sensitivity)lines.push(`Pixel sensitivity (5–95%): ${(m.sensitivity.p05_m*100).toFixed(2)}–${(m.sensitivity.p95_m*100).toFixed(2)} cm.`);
      }
    }
    return lines.join('\n');
  }
  $('image').replaceChildren(...payload.photos.map(p=>new Option(`JPEG ${p.image_id}`,String(p.image_id))));options();
  $('session').textContent=annotations.session_id;
  function update(){
    const o=observation();
    $('coordinates').textContent=o ? `Working pixel (${o.pixel.map(v=>v.toFixed(3)).join(', ')}) · ${o.reviewed?'confirmed':'needs review'}` : 'No mark in this photo';
    $('review').disabled=!o; $('remove').disabled=!o;
    const used=new Set([...(annotations.reference.endpoints),...annotations.measurements.flatMap(m=>m.endpoints)]);
    let count=0, confirmed=0;
    for(const group of annotations.view_groups)for(const id of group)for(const name of used){
      count++; if(annotations.points[name].observations[String(id)]?.reviewed)confirmed++;
    }
    $('progress').textContent=`${confirmed}/${count} required mark/view uses confirmed. Shared views appear in each selected group.`;
    $('results').textContent=dirty?'Annotations changed. Re-run Python to update results.':summarize(payload.report);
  }
  function draw(){
    const w=canvas.clientWidth,h=canvas.clientHeight,dpr=window.devicePixelRatio||1;
    if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);}
    ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#0b1017';ctx.fillRect(0,0,w,h);
    const image=images.get($('image').value);if(!image)return;
    ctx.save();ctx.translate(tx,ty);ctx.scale(scale,scale);ctx.imageSmoothingEnabled=scale<2;
    ctx.drawImage(image,0,0);ctx.restore();
    for(const [name,p] of Object.entries(annotations.points)){
      const o=p.observations[$('image').value];if(!o)continue;
      const xy=transform(photo().working_to_display,o.pixel), x=tx+scale*xy[0],y=ty+scale*xy[1];
      ctx.strokeStyle=name===$('point').value?'#ffbf58':o.reviewed?'#72f7b5':'#f27ea9';ctx.lineWidth=1.5;
      ctx.beginPath();ctx.moveTo(x-9,y);ctx.lineTo(x+9,y);ctx.moveTo(x,y-9);ctx.lineTo(x,y+9);ctx.stroke();
      ctx.fillStyle=ctx.strokeStyle;ctx.font='13px system-ui';ctx.fillText(name,x+11,y-7);
    }
  }
  function fit(){const p=photo();scale=Math.min(canvas.clientWidth/p.width,canvas.clientHeight/p.height)*.96;tx=(canvas.clientWidth-scale*p.width)/2;ty=(canvas.clientHeight-scale*p.height)/2;draw();}
  async function load(){
    ready=false;const id=$('image').value,n=++token;
    if(!images.has(id)){const im=new Image();im.src=photos.get(id).photo;await im.decode();images.set(id,im);}
    if(n!==token)return;ready=true;fit();update();
  }
  function zoom(factor,x=canvas.clientWidth/2,y=canvas.clientHeight/2){
    const next=Math.max(.02,Math.min(30,scale*factor)), ratio=next/scale;tx=x-(x-tx)*ratio;ty=y-(y-ty)*ratio;scale=next;draw();
  }
  function place(display){
    const p=transform(photo().display_to_working,display),size=annotations.image_sizes[$('image').value];
    if(p[0]<0||p[1]<0||p[0]>=size[0]||p[1]>=size[1])return;
    point().observations[$('image').value]={pixel:p,reviewed:false,method:'manual_photo_review'};changed();
  }
  $('image').onchange=()=>load().catch(error=>{$('error').textContent=error.message;});
  $('point').onchange=()=>{update();draw();};$('fit').onclick=fit;
  $('center').onclick=()=>{const o=observation();if(!o)return;const p=transform(photo().working_to_display,o.pixel);scale=2;tx=canvas.clientWidth/2-scale*p[0];ty=canvas.clientHeight/2-scale*p[1];draw();};
  $('zoom-in').onclick=()=>zoom(1.5);$('zoom-out').onclick=()=>zoom(1/1.5);
  canvas.addEventListener('wheel',e=>{e.preventDefault();const r=canvas.getBoundingClientRect();zoom(Math.exp(-e.deltaY*.001),e.clientX-r.left,e.clientY-r.top);},{passive:false});
  canvas.onpointerdown=e=>{if(!ready)return;canvas.focus();canvas.setPointerCapture(e.pointerId);drag={x:e.clientX,y:e.clientY,tx,ty,moved:false};};
  canvas.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.hypot(dx,dy)>3)drag.moved=true;if(drag.moved){tx=drag.tx+dx;ty=drag.ty+dy;draw();}};
  canvas.onpointerup=e=>{if(!drag)return;const clicked=!drag.moved;drag=null;if(clicked){const r=canvas.getBoundingClientRect();place([(e.clientX-r.left-tx)/scale,(e.clientY-r.top-ty)/scale]);}};
  canvas.onpointercancel=()=>{drag=null;};
  canvas.onkeydown=e=>{
    const delta={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key],o=observation();if(!delta||!o)return;e.preventDefault();
    // Arrow directions follow the displayed upright photo, even after EXIF rotation.
    const d=photo().display_to_working, factor=e.shiftKey?1:.25;
    const norm=Math.hypot(d[0][0]*delta[0]+d[0][1]*delta[1],d[1][0]*delta[0]+d[1][1]*delta[1]);
    const p=transform(photo().working_to_display,o.pixel);place([p[0]+delta[0]*factor/norm,p[1]+delta[1]*factor/norm]);
  };
  $('review').onclick=()=>{if(observation()){observation().reviewed=true;changed();}};
  $('remove').onclick=()=>{delete point().observations[$('image').value];changed();};
  $('download').onclick=()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(annotations,null,2)+'\n'],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='measurement_annotations.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('file').onchange=async()=>{
    try{
      const next=JSON.parse(await $('file').files[0].text());
      if(next.scene_sha256!==payload.annotations.scene_sha256||next.session_id!==payload.annotations.session_id||next.coordinate_frame!==payload.annotations.coordinate_frame||next.schema_version!==1)throw Error('Annotations belong to a different scene or coordinate frame');
      const expectedSizes=payload.annotations.image_sizes;
      if(!next.image_sizes||Object.keys(next.image_sizes).length!==Object.keys(expectedSizes).length||Object.entries(expectedSizes).some(([id,size])=>JSON.stringify(next.image_sizes[id])!==JSON.stringify(size)))throw Error('Image sizes differ');
      if(!next.points||typeof next.points!=='object'||Object.keys(next.points).length<4||!Array.isArray(next.measurements)||!next.measurements.length||!next.reference||!Array.isArray(next.view_groups)||!next.view_groups.length)throw Error('Invalid annotation structure');
      if(!next.view_groups.every(g=>Array.isArray(g)&&g.length>=2&&new Set(g).size===g.length&&g.every(Number.isInteger)))throw Error('Invalid view groups');
      if(next.view_groups.flat().some(id=>!photos.has(String(id))))throw Error('Re-export HTML to include the requested photos');
      for(const p of Object.values(next.points)){
        if(!p.observations||typeof p.observations!=='object')throw Error('Invalid observations');
        for(const [id,o] of Object.entries(p.observations)){
          const size=next.image_sizes[id];
          if(!size||!Array.isArray(o.pixel)||o.pixel.length!==2||!o.pixel.every(Number.isFinite)||o.pixel.some((v,k)=>v<0||v>=size[k])||typeof o.reviewed!=='boolean')throw Error('Invalid mark coordinate or review state');
        }
      }
      const validPair=p=>Array.isArray(p)&&p.length===2&&p[0]!==p[1]&&p.every(n=>typeof n==='string'&&next.points[n]);
      if(!validPair(next.reference.endpoints)||!Number.isFinite(next.reference.length_m)||next.reference.length_m<=0||!next.measurements.every(m=>typeof m.id==='string'&&m.id&&validPair(m.endpoints)))throw Error('Invalid interval endpoints or reference length');
      if(!Number.isFinite(next.localization_radius_px)||next.localization_radius_px<0)throw Error('Invalid localization radius');
      annotations=next;options();changed();$('error').textContent='';
    }catch(error){$('error').textContent=error.message;}
  };
  new ResizeObserver(()=>draw()).observe(canvas);
  window.__measurementReview={getState:()=>({ready,dirty,imageId:Number($('image').value),point:$('point').value,scale,tx,ty,annotations:structuredClone(annotations)}),
    workingToScreen:p=>{const xy=transform(photo().working_to_display,p);return [tx+scale*xy[0],ty+scale*xy[1]];}};
  load().catch(error=>{$('error').textContent=error.message;});
})();
