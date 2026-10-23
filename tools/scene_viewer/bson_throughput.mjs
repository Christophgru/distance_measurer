// Explicit synthetic capacity check; never publishes or pads a reconstruction.
// Run: node bson_throughput.mjs /path/to/exported/reference/index.html
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { serialize } from 'bson';

const sourceFile=path.resolve(process.argv[2]);
const html=await fs.readFile(sourceFile,'utf8');
const pattern=/(<script type="application\/json" id="scene-data">)([\s\S]*?)(<\/script>)/;
const source=JSON.parse(html.match(pattern)[2]);
const fixture={...source,session_id:'SYNTHETIC 2M BSON capacity fixture',point_count:2000000,
  sparse_point_count:source.sparse_point_count||source.point_count,has_photo_colors:false,
  quality_status:'synthetic_renderer_capacity_fixture',scale_status:'synthetic_arbitrary_units'};
fixture.cameras=source.cameras.map(c=>({...c,photo:null}));
delete fixture.previous_cameras;delete fixture.camera_comparison;
fixture.densification={original_point_count:fixture.sparse_point_count,added_point_count:2000000-fixture.sparse_point_count};
const n=fixture.densification.added_point_count,views=fixture.cameras.slice(0,3).map(c=>c.image_id);
assert.equal(views.length,3);
const ids=new Uint32Array(Uint8Array.from(Buffer.from(source.point_ids,'base64')).buffer);
let firstId=0;for(const id of ids)firstId=Math.max(firstId,id+1);
fixture.dense_cloud={point_count:n,image_ids:fixture.cameras.map(c=>c.image_id),worker:'dense_worker.js',support_histogram:{'3':n},shards:[]};
fixture.point_cloud_bounds={all:[[-2,-2,-2],[2,2,2]],overview:[[-2,-2,-2],[2,2,2]]};
const directory=await fs.mkdtemp(path.join(os.tmpdir(),'SYNTHETIC-bson-2m-'));
try{
  for(let start=0;start<n;start+=50000){
    const points=[];
    for(let i=start;i<Math.min(start+50000,n);i++)points.push({point_id:firstId+i,
      position:[source.origin[0]+(i%1000)/250-2,source.origin[1]+(Math.floor(i/1000)%1000)/250-2,source.origin[2]+Math.floor(i/1000000)/4],
      color:[100,180,210],normal:[0,0,-1],supporting_image_ids:views});
    const bytes=serialize({schema_version:1,coordinate_frame:'reference_world',points});
    assert(bytes.length<16*1024*1024,'BSON batch must remain bounded');
    const url=`points_${start}.bson.gz`;
    await fs.writeFile(path.join(directory,url),gzipSync(bytes,{level:1}));
    fixture.dense_cloud.shards.push({url,point_count:points.length,sha256:createHash('sha256').update(bytes).digest('hex')});
  }
  await fs.copyFile(path.join(path.dirname(sourceFile),'dense_worker.js'),path.join(directory,'dense_worker.js'));
  const index=path.join(directory,'index.html');
  await fs.writeFile(index,html.replace(pattern,(_,open,_old,close)=>open+JSON.stringify(fixture)+close));
  const result=spawnSync(process.execPath,[fileURLToPath(new URL('smoke.mjs',import.meta.url)),index],{encoding:'utf8',maxBuffer:1024*1024});
  process.stdout.write(result.stdout);process.stderr.write(result.stderr);
  assert.equal(result.status,0,'Synthetic 2M BSON browser capacity check failed');
}finally{await fs.rm(directory,{recursive:true,force:true});}
