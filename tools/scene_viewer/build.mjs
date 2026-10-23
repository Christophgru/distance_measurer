import { build } from 'esbuild';
const common={bundle:true,minify:true,format:'iife',target:'es2020',legalComments:'inline'};
await Promise.all([
  build({...common,entryPoints:['app.js'],outfile:'../../python/src/distance_measuring/visualization/web_assets/viewer.js'}),
  build({...common,entryPoints:['dense_worker.js'],outfile:'../../python/src/distance_measuring/visualization/web_assets/dense_worker.js'}),
]);
