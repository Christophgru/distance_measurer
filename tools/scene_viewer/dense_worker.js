// Decode bounded BSON batches off the UI thread; keep point records unchanged.
import { deserialize } from 'bson';

self.onmessage=async event=>{
  try{
    const {cloud,origin}=event.data,n=cloud.point_count;
    const positions=new Float32Array(3*n),colors=new Uint8Array(3*n),ids=new Uint32Array(n),visibility=new Uint32Array(n);
    const viewBits=new Map(cloud.image_ids.map((id,k)=>[id,k]));
    let cursor=0;
    for(let batch=0;batch<cloud.shards.length;batch++){
      const shard=cloud.shards[batch];
      const response=await fetch(shard.url);
      if(!response.ok)throw new Error(`BSON batch HTTP ${response.status}`);
      let bytes=new Uint8Array(await response.arrayBuffer());
      if(bytes[0]===0x1f&&bytes[1]===0x8b){
        const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
        bytes=new Uint8Array(await new Response(stream).arrayBuffer());
      }
      if(self.crypto?.subtle){
        const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
        if(hash!==shard.sha256)throw new Error('BSON batch source hash differs');
      }
      const doc=deserialize(bytes);
      if(doc.schema_version!==1||doc.coordinate_frame!=='reference_world'||doc.points.length!==shard.point_count)throw new Error('Incompatible BSON point-list structure');
      for(const point of doc.points){
        if(cursor>=n||!Number.isInteger(point.point_id)||point.point_id<0||point.point_id>0xffffffff)throw new Error('Invalid dense point ID/count');
        if(point.position.length!==3||point.color.length!==3||!point.position.every(Number.isFinite))throw new Error('Invalid BSON point position/color');
        positions.set(point.position.map((x,k)=>x-origin[k]),3*cursor);colors.set(point.color,3*cursor);ids[cursor]=point.point_id;
        let support=0;for(const image of point.supporting_image_ids){if(!viewBits.has(image))throw new Error('Unknown supporting camera');support|=1<<viewBits.get(image);}
        visibility[cursor]=support>>>0;cursor++;
      }
      self.postMessage({type:'progress',loaded:cursor,total:n,batch:batch+1,batches:cloud.shards.length});
    }
    if(cursor!==n)throw new Error('BSON point list is incomplete');
    self.postMessage({type:'complete',positions,colors,ids,visibility},[positions.buffer,colors.buffer,ids.buffer,visibility.buffer]);
  }catch(error){self.postMessage({type:'error',message:error.message});}
};
