import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = id => document.getElementById(id);
const fail = error => { $('error').hidden = false; $('error').textContent = `Cannot display the reconstruction.\n${error.message}\nUse a browser with WebGL 2 and hardware acceleration enabled.`; };
try {
  const data = JSON.parse($('scene-data').textContent);
  const decode = (name, Type) => {
    const raw = atob(data[name]);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return new Type(bytes.buffer);
  };
  const xyz = decode('positions', Float32Array), rgb = decode('colors', Uint8Array);
  const pointIds = decode('point_ids', Uint32Array), trackIds = decode('track_ids', Uint32Array);
  const errors = decode('errors', Float32Array), counts = decode('view_counts', Uint32Array);
  const offsets = decode('observation_offsets', Uint32Array), observationImages = decode('observation_images', Uint32Array);
  const observationPixels = decode('observation_pixels', Float32Array), observationErrors = decode('observation_errors', Float32Array);
  const sparseCount = data.densification?.original_point_count ?? data.point_count;
  const dense = new Float32Array(data.point_count);
  if(data.densification) dense.fill(1,sparseCount);
  const viewport = $('viewport'), scene = new THREE.Scene(), origin = new THREE.Vector3(...data.origin);
  scene.background = new THREE.Color('#0b1220');
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  viewport.prepend(renderer.domElement);
  renderer.domElement.setAttribute('aria-label', 'Interactive reconstructed points and camera positions');
  renderer.domElement.addEventListener('webglcontextlost', event => { event.preventDefault(); fail(new Error('Graphics context lost. Reload this viewer to restore it.')); });
  const camera = new THREE.PerspectiveCamera(50, 1, data.span * 1e-5, data.span * 1000);
  // Lecture 002 pp. 38–45/81–83: world axes remain the saved scene axes.
  // The navigation up vector follows the first photo; it is not a gravity fit.
  const first = data.cameras[0];
  function photoUp(c){
    const o=c?.exif_orientation||1;
    if(!c)return new THREE.Vector3(0,1,0);
    return new THREE.Vector3(...c.R_cw[(o>=5)?0:1]).multiplyScalar([3,4,5,8].includes(o)?1:-1);
  }
  camera.up.copy(photoUp(first));
  let controls;
  function navigation(up){
    controls?.dispose();camera.up.copy(up);
    controls=new OrbitControls(camera,renderer.domElement);
    controls.enableDamping=false; // No permanent animation loop.
    controls.minDistance=data.span*1e-6;controls.maxDistance=data.span*2000;controls.zoomToCursor=true;
    controls.addEventListener('change',invalidate);
  }
  navigation(camera.up.clone());
  let pendingFrame = false, visibleCount = data.point_count, selectedRow = -1, selectedCamera = null;
  const uniforms = {
    uSize: { value: 4 * renderer.getPixelRatio() }, uMaxError: { value: 1e20 },
    uMinViews: { value: 2 }, uMode: { value: 0 }, uPick: { value: false },
    uErrorRange: { value: Math.max(3, ...Array.from(errors.slice(0, 10000))) },
    uMaxViews: { value: Math.max(3, data.camera_count) },
    uShowDense: { value: true },
  };
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(xyz, 3));
  geometry.setAttribute('color', new THREE.BufferAttribute(rgb, 3, true));
  geometry.setAttribute('error', new THREE.BufferAttribute(errors, 1));
  geometry.setAttribute('dense', new THREE.BufferAttribute(dense, 1));
  geometry.setAttribute('views', new THREE.Float32BufferAttribute(counts, 1));
  const pickingColors = new Float32Array(data.point_count * 3);
  for (let i = 0; i < data.point_count; i++) {
    const id = i + 1;
    pickingColors.set([(id & 255) / 255, ((id >> 8) & 255) / 255, ((id >> 16) & 255) / 255], i * 3);
  }
  geometry.setAttribute('pickColor', new THREE.BufferAttribute(pickingColors, 3));
  geometry.computeBoundingSphere();
  const material = new THREE.ShaderMaterial({ uniforms,
    vertexShader: `attribute vec3 color; attribute vec3 pickColor; attribute float error; attribute float views; attribute float dense;
      uniform float uSize, uMaxError, uMinViews, uErrorRange, uMaxViews; uniform int uMode;
      uniform bool uShowDense; varying vec3 vColor, vPick; varying float vVisible;
      void main(){
        vVisible = ((error < 0.0 || error <= uMaxError) && views >= uMinViews && (uShowDense || dense < .5)) ? 1.0 : 0.0;
        vColor = color; vPick = pickColor;
        if(uMode==1) vColor = error < 0.0 ? vec3(.55,.65,.75) : mix(vec3(.2,.85,.6),vec3(1.0,.3,.25),clamp(error/uErrorRange,0.0,1.0));
        if(uMode==2) vColor = mix(vec3(.25,.6,1.0),vec3(1.0,.8,.25),clamp((views-2.0)/max(1.0,uMaxViews-2.0),0.0,1.0));
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0);
        gl_PointSize = uSize;
        if(vVisible<.5) gl_Position = vec4(2.0,2.0,2.0,1.0);
      }`,
    fragmentShader: `uniform bool uPick; varying vec3 vColor, vPick; varying float vVisible;
      void main(){if(vVisible<.5 || distance(gl_PointCoord,vec2(.5))>.5) discard;
        gl_FragColor=vec4(uPick?vPick:vColor,1.0);}`,
  });
  const cloud = new THREE.Points(geometry, material);
  scene.add(cloud);
  // A separate ID pass picks only visible points using the same GPU filters.
  // Stable persisted IDs are mapped from row numbers; they are never renumbered.
  const pickingScene = new THREE.Scene(); pickingScene.background = new THREE.Color(0);
  const pickingCloud = new THREE.Points(geometry, material); pickingScene.add(pickingCloud);
  const pickingTarget = new THREE.WebGLRenderTarget(33, 33, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter });
  const axes = new THREE.AxesHelper(data.span * .15); axes.position.copy(origin).negate(); scene.add(axes);
  const cameraGroup = new THREE.Group(), photoGroup = new THREE.Group(); scene.add(cameraGroup, photoGroup);
  const markers = [], cameraObjects = new Map(), photoImages = new Map();
  for (const c of data.cameras) {
    const group = new THREE.Group(); group.position.set(...c.center); cameraGroup.add(group);
    const vertices = new THREE.Float32BufferAttribute(c.vertices.flat(), 3);
    const wireGeometry = new THREE.BufferGeometry(); wireGeometry.setAttribute('position', vertices); wireGeometry.setIndex(c.lines.flat());
    const wire = new THREE.LineSegments(wireGeometry, new THREE.LineBasicMaterial({ color: new THREE.Color(...c.color) })); group.add(wire);
    const marker = new THREE.Mesh(new THREE.SphereGeometry(data.frustum_depth * .055, 12, 8), new THREE.MeshBasicMaterial({ color: new THREE.Color(...c.color) }));
    marker.userData.camera = c; marker.position.set(...c.center); cameraGroup.add(marker); markers.push(marker);
    const label = document.createElement('span'); label.className = 'camera-label'; label.textContent = `Camera ${c.image_id}`;
    label.style.setProperty('--color', `rgb(${c.color.map(x => Math.round(255*x)).join(',')})`); $('labels').appendChild(label);
    cameraObjects.set(c.image_id, { data: c, group, marker, label });
    const button = document.createElement('button'); button.textContent = String(c.image_id); button.dataset.cameraId = c.image_id;
    button.style.borderColor = `rgb(${c.color.map(x => Math.round(255*x)).join(',')})`; button.onclick = () => inspectCamera(c.image_id); $('camera-buttons').appendChild(button);
    if (c.photo) {
      const image = new Image(); image.onload = () => { drawPhoto(); invalidate(); }; image.src = c.photo; photoImages.set(c.image_id, image);
      const texture = new THREE.TextureLoader().load(c.photo, invalidate); texture.colorSpace = THREE.SRGBColorSpace;
      const pg = new THREE.BufferGeometry(); pg.setAttribute('position', new THREE.Float32BufferAttribute(c.vertices.slice(1,5).flat(),3));
      pg.setAttribute('uv', new THREE.Float32BufferAttribute([0,1,1,1,1,0,0,0],2)); pg.setIndex([0,1,2,0,2,3]);
      const plane = new THREE.Mesh(pg, new THREE.MeshBasicMaterial({ map: texture, transparent: true, opacity: .4, depthWrite: false, side: THREE.DoubleSide }));
      plane.position.set(...c.center); photoGroup.add(plane); cameraObjects.get(c.image_id).plane = plane;
      const option = document.createElement('option'); option.value = c.image_id; option.textContent = `Photo ${c.image_id}`; $('photo-camera').appendChild(option);
    }
  }
  photoGroup.visible = false;
  const ordered = [...data.cameras].sort((a,b) => (a.time_s ?? a.image_id) - (b.time_s ?? b.image_id));
  const path = new THREE.Line(new THREE.BufferGeometry().setFromPoints(ordered.map(c => new THREE.Vector3(...c.center))), new THREE.LineBasicMaterial({ color: '#8ba0b8' })); scene.add(path);
  const selected = new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute([0,0,0],3)), new THREE.PointsMaterial({ color: '#ffe16d', size: 12 * renderer.getPixelRatio(), sizeAttenuation: false, depthTest: false }));
  selected.visible = false; selected.renderOrder = 20; scene.add(selected);
  const rays = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: '#ffe16d', transparent: true, opacity: .65, depthTest: false })); rays.visible = false; scene.add(rays);
  const number = x => Number(x).toPrecision(6);
  const visible = i => (errors[i] < 0 || errors[i] <= uniforms.uMaxError.value) && counts[i] >= uniforms.uMinViews.value && (uniforms.uShowDense.value || !dense[i]);
  function invalidate(){ if(!pendingFrame){ pendingFrame=true; requestAnimationFrame(render); } }
  function render(){
    pendingFrame=false;
    const distance = camera.position.distanceTo(controls.target);
    camera.near=Math.max(data.span*1e-8, distance*1e-5); camera.far=Math.max(data.span*100, distance*10); camera.updateProjectionMatrix();
    renderer.render(scene,camera);
    for(const c of data.cameras){
      const entry=cameraObjects.get(c.image_id), v=new THREE.Vector3(...c.center).project(camera);
      entry.label.hidden=!cameraGroup.visible || v.z < -1 || v.z > 1 || Math.abs(v.x)>1 || Math.abs(v.y)>1;
      entry.label.style.left=`${(v.x+1)*viewport.clientWidth/2}px`; entry.label.style.top=`${(1-v.y)*viewport.clientHeight/2}px`;
    }
    $('status').textContent=`${cloud.visible?visibleCount:0} / ${data.point_count} points · ${data.camera_count} cameras · ${renderer.info.render.calls} GPU draw calls · reconstruction units`;
  }
  function resize(){ renderer.setSize(viewport.clientWidth,viewport.clientHeight,false); camera.aspect=viewport.clientWidth/Math.max(1,viewport.clientHeight); camera.updateProjectionMatrix(); invalidate(); }
  new ResizeObserver(resize).observe(viewport);
  function fit(mode){
    navigation(photoUp(first));
    const points = mode==='cameras' ? data.cameras.map(c=>c.center) : Array.from({length:data.point_count},(_,i)=>Array.from(xyz.subarray(3*i,3*i+3)));
    points.push(...data.cameras.map(c=>c.center));
    if(!points.length) return;
    const low=[],high=[];
    for(let k=0;k<3;k++){
      const values=points.map(p=>p[k]).sort((a,b)=>a-b), last=values.length-1;
      low[k]=values[mode==='overview'?Math.floor(last*.01):0]; high[k]=values[mode==='overview'?Math.ceil(last*.99):last];
      for(const c of data.cameras){low[k]=Math.min(low[k],c.center[k]);high[k]=Math.max(high[k],c.center[k]);}
    }
    const middle=new THREE.Vector3(...low).add(new THREE.Vector3(...high)).multiplyScalar(.5);
    const radius=Math.max(new THREE.Vector3(...high).distanceTo(new THREE.Vector3(...low))*.5,data.span*.01);
    const direction=new THREE.Vector3(1,-.5,-1).normalize();
    if(first){ const forward=new THREE.Vector3(...first.R_cw[2]); direction.copy(forward).negate().addScaledVector(camera.up,.4).add(new THREE.Vector3(...first.R_cw[0]).multiplyScalar(.4)).normalize(); }
    const fov=Math.min(THREE.MathUtils.degToRad(50),2*Math.atan(Math.tan(THREE.MathUtils.degToRad(25))*camera.aspect));
    camera.fov=50; camera.position.copy(middle).addScaledVector(direction,1.18*radius/Math.sin(fov/2)); controls.target.copy(middle); controls.update(); invalidate();
  }
  function inspectCamera(id){
    selectedCamera=cameraObjects.get(id).data;
    document.querySelectorAll('[data-camera-id]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.cameraId)===id)));
    $('camera-detail').textContent=`Camera ${id}\nCenter: ${selectedCamera.world_center.map(number).join(', ')}\n${selectedCamera.time_s==null?'Photo order by ID':`Capture: ${selectedCamera.time_s.toFixed(3)} s`}\nFrustum size is a drawing control.`;
    $('camera-view').disabled=false; $('focus-camera').disabled=false;
    if(selectedCamera.photo){$('photo-camera').value=id;drawPhoto();}
    invalidate();
  }
  function focus(position){
    const delta=camera.position.clone().sub(controls.target); controls.target.copy(position); camera.position.copy(position).add(delta); controls.update(); invalidate();
  }
  function selectRow(row){
    if(row<0 || row>=data.point_count || !visible(row)){clearPoint();return;}
    selectedRow=row;
    const point=new THREE.Vector3().fromArray(xyz,3*row); selected.geometry.attributes.position.setXYZ(0,...point.toArray()); selected.geometry.attributes.position.needsUpdate=true; selected.geometry.computeBoundingSphere(); selected.visible=true;
    const segments=[],observations=[];
    for(let j=offsets[row];j<offsets[row+1];j++){
      const id=observationImages[j], c=cameraObjects.get(id);
      if(c) segments.push(...point.toArray(),...c.data.center);
      observations.push(`Photo ${id}: (${observationPixels[2*j].toFixed(1)}, ${observationPixels[2*j+1].toFixed(1)}) · ${observationErrors[j]<0?'unknown error':observationErrors[j].toFixed(2)+' px'}`);
    }
    rays.geometry.dispose(); rays.geometry=new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute(segments,3)); rays.visible=true;
    $('point-detail').textContent=`Point ${pointIds[row]} · track ${trackIds[row]}\nSource: ${dense[row]?'added stereo sample':'original sparse track'}\nWorld: ${point.add(origin).toArray().map(number).join(', ')}\n${counts[row]} observations · max error ${errors[row]<0?'unknown':errors[row].toFixed(3)+' px'}\n${observations.join('\n')}`;
    $('point-id').value=pointIds[row]; $('focus-point').disabled=false;
    const current=Number($('photo-camera').value);
    if(!Array.from(observationImages.subarray(offsets[row],offsets[row+1])).includes(current)) $('photo-camera').value=observationImages[offsets[row]];
    drawPhoto(); invalidate();
  }
  function clearPoint(){selectedRow=-1;selected.visible=false;rays.visible=false;$('focus-point').disabled=true;$('point-id').value='';$('point-detail').textContent='Click a point in the scene or enter its ID.';drawPhoto();invalidate();}
  function drawPhoto(){
    const id=Number($('photo-camera').value), entry=cameraObjects.get(id), image=photoImages.get(id);
    $('photo-wrapper').hidden=!image;
    if(!entry || !image?.complete || !image.naturalWidth) return;
    const c=entry.data,canvas=$('photo'),w=image.naturalWidth,h=image.naturalHeight,o=c.exif_orientation || 1;
    canvas.width=o>=5?h:w;canvas.height=o>=5?w:h;
    const ctx=canvas.getContext('2d'); const transforms={1:[1,0,0,1,0,0],2:[-1,0,0,1,w,0],3:[-1,0,0,-1,w,h],4:[1,0,0,-1,0,h],5:[0,1,1,0,0,0],6:[0,1,-1,0,h,0],7:[0,-1,-1,0,h,w],8:[0,-1,1,0,0,w]};
    ctx.setTransform(...(transforms[o]||transforms[1]));ctx.drawImage(image,0,0);
    if(selectedRow>=0){for(let j=offsets[selectedRow];j<offsets[selectedRow+1];j++) if(observationImages[j]===id){
      // Lecture 002 pp. 65–76: transform pixel centers with the sampling ratio;
      // EXIF orientation above affects the preview only, never K/R/C or rays.
      const x=(observationPixels[2*j]+.5)*w/c.image_size[0]-.5,y=(observationPixels[2*j+1]+.5)*h/c.image_size[1]-.5;
      ctx.strokeStyle='#ff4883';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(x-14,y);ctx.lineTo(x+14,y);ctx.moveTo(x,y-14);ctx.lineTo(x,y+14);ctx.stroke();
    }}
  }
  function pick(event){
    const rect=renderer.domElement.getBoundingClientRect(), x=event.clientX-rect.left,y=event.clientY-rect.top;
    if(cameraGroup.visible){
      const raycaster=new THREE.Raycaster(); raycaster.setFromCamera(new THREE.Vector2(2*x/rect.width-1,1-2*y/rect.height),camera);
      const hits=raycaster.intersectObjects(markers,false); if(hits.length){inspectCamera(hits[0].object.userData.camera.image_id);return;}
    }
    if(!cloud.visible) return;
    const dpr=renderer.getPixelRatio(),width=renderer.domElement.width,height=renderer.domElement.height;
    const cx=2*(Math.floor(x*dpr)+.5)/width-1,cy=1-2*(Math.floor(y*dpr)+.5)/height;
    const projection=camera.projectionMatrix.clone();
    const crop=new THREE.Matrix4().set(width/33,0,0,-cx*width/33,0,height/33,0,-cy*height/33,0,0,1,0,0,0,0,1);
    camera.projectionMatrix.premultiply(crop); uniforms.uPick.value=true;
    const previous=renderer.getRenderTarget(), pixel=new Uint8Array(4);
    try{renderer.setRenderTarget(pickingTarget);renderer.render(pickingScene,camera);renderer.readRenderTargetPixels(pickingTarget,16,16,1,1,pixel);}
    finally{renderer.setRenderTarget(previous);uniforms.uPick.value=false;camera.projectionMatrix.copy(projection);}
    const row=pixel[0]+256*pixel[1]+65536*pixel[2]-1;
    if(row>=0) selectRow(row);
    invalidate();
  }
  let pointerStart=null;
  renderer.domElement.addEventListener('pointerdown',e=>{pointerStart=[e.clientX,e.clientY,e.button];});
  renderer.domElement.addEventListener('pointerup',e=>{if(pointerStart && pointerStart[2]===0 && Math.hypot(e.clientX-pointerStart[0],e.clientY-pointerStart[1])<4)pick(e);pointerStart=null;});
  renderer.domElement.addEventListener('dblclick',e=>{pick(e);if(selectedRow>=0)focus(new THREE.Vector3().fromArray(xyz,3*selectedRow));});
  $('point-id').addEventListener('change',()=>{const value=$('point-id').value.trim();const row=pointIds.findIndex(id=>String(id)===value);if(row<0){$('point-detail').textContent='That point ID is not present in this reconstruction.';}else selectRow(row);});
  $('focus-point').onclick=()=>{if(selectedRow>=0)focus(new THREE.Vector3().fromArray(xyz,3*selectedRow));};$('clear-point').onclick=clearPoint;
  $('focus-camera').onclick=()=>{if(selectedCamera)focus(new THREE.Vector3(...selectedCamera.center));};
  $('camera-view').onclick=()=>{
    if(!selectedCamera)return;
    const c=selectedCamera;
    // Lecture 002 pp. 70–76, 81–83: saved optical +Z is world R.T*[0,0,1].
    // The inspection viewport preserves vertical field of view, adapting width
    // to the browser aspect; calibrated full-image corners remain in the frustum.
    navigation(photoUp(c));
    const quarterTurn=(c.exif_orientation||1)>=5;
    camera.fov=THREE.MathUtils.radToDeg(2*Math.atan(quarterTurn?c.image_size[0]/(2*c.K[0][0]):c.image_size[1]/(2*c.K[1][1])));
    camera.position.set(...c.center);controls.target.copy(camera.position).addScaledVector(new THREE.Vector3(...c.R_cw[2]),data.span*.3);controls.update();invalidate();
  };
  $('overview').onclick=()=>fit('overview');$('fit-all').onclick=()=>fit('all');$('fit-cameras').onclick=()=>fit('cameras');
  $('point-size').oninput=()=>{uniforms.uSize.value=Number($('point-size').value)*renderer.getPixelRatio();$('size-value').textContent=`${$('point-size').value} px`;invalidate();};
  $('color-mode').onchange=()=>{uniforms.uMode.value=Number($('color-mode').value);invalidate();};
  let maxError=0;for(const error of errors)maxError=Math.max(maxError,error);maxError=Math.max(3,Math.ceil(maxError*20)/20);uniforms.uErrorRange.value=maxError;
  $('max-error').max=maxError;$('max-error').value=maxError;$('error-value').textContent=`${maxError.toFixed(2)} px`;
  $('min-views').max=Math.max(2,data.camera_count);
  function filter(){uniforms.uMaxError.value=Number($('max-error').value);uniforms.uMinViews.value=Number($('min-views').value);visibleCount=0;for(let i=0;i<data.point_count;i++)if(visible(i))visibleCount++;$('error-value').textContent=`${uniforms.uMaxError.value.toFixed(2)} px`;$('views-value').textContent=uniforms.uMinViews.value;if(selectedRow>=0&&!visible(selectedRow))clearPoint();invalidate();}
  $('max-error').oninput=filter;$('min-views').oninput=filter;
  $('show-dense').onchange=()=>{uniforms.uShowDense.value=$('show-dense').checked;filter();};
  for(const [id,object] of [['show-cloud',cloud],['show-cameras',cameraGroup],['show-path',path],['show-photos',photoGroup],['show-axes',axes]]) $(id).onchange=()=>{object.visible=$(id).checked;invalidate();};
  $('frustum-size').oninput=()=>{const scale=Number($('frustum-size').value);for(const entry of cameraObjects.values()){entry.group.scale.setScalar(scale);if(entry.plane)entry.plane.scale.setScalar(scale);}invalidate();};
  $('photo-camera').onchange=drawPhoto;$('photo').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen();else $('photo').requestFullscreen?.();};
  $('toggle-panel').onclick=()=>$('sidebar').classList.toggle('closed');
  if(matchMedia('(max-width:760px)').matches)$('sidebar').classList.add('closed');
  if(!data.has_photo_colors){$('color-mode').options[0].textContent='Neutral';$('show-photos').disabled=true;}
  if(data.densification){$('dense-toggle').hidden=false;$('dense-note').hidden=false;$('dense-note').textContent=`${data.densification.added_point_count.toLocaleString()} added stereo samples. Their reprojection error checks consistency with the fixed cameras; it does not certify camera accuracy.`;}
  const warnings=[...new Set(data.warnings.map(s=>s.replace(/^(Input reconstruction: )+/,'')))];
  $('session').textContent=data.session_id;
  $('summary').textContent=`${data.point_count.toLocaleString()} points · ${data.camera_count} cameras · WebGL`;
  $('provenance').textContent=`Scale: ${data.scale_status}\nQuality: ${data.quality_status}\nSource SHA-256: ${data.source_scene_sha256}\nExcluded camera IDs: ${data.excluded_images.map(c=>c.image_id).join(', ')||'none'}\nOverview fits the central 98%; Fit all includes outliers.\nConnecting lines join photo positions, not a continuous IMU path.\n${warnings.join('\n')}\nRenderer: Three.js ${THREE.REVISION}.`;
  window.__reconstructionViewer = {
    getState:()=>({ready:true,pointCount:data.point_count,visibleCount,selectedPointId:selectedRow<0?null:pointIds[selectedRow],cameraIds:data.cameras.map(c=>c.image_id),drawCalls:renderer.info.render.calls,cameraPosition:camera.position.toArray(),cameraUp:camera.up.toArray(),target:controls.target.toArray()}),
    projectPoint:row=>{const v=new THREE.Vector3().fromArray(xyz,3*row).project(camera);return {x:(v.x+1)*viewport.clientWidth/2,y:(1-v.y)*viewport.clientHeight/2,z:v.z};},
  };
  resize();fit('overview');filter();if(first)inspectCamera(first.image_id);
} catch(error){ fail(error); }
