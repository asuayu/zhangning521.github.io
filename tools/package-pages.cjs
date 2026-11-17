const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),out=path.join(root,'_site');
fs.mkdirSync(out,{recursive:true});
for(const name of ['index.html','workbench.html','calendar-sync.js','album.html','together.html','together.js','together.css','.nojekyll','css','img-one','mp3','cube-photos.json','playlist.json']){
  fs.cpSync(path.join(root,name),path.join(out,name),{recursive:true});
}
console.log('Packaged public pages and existing album assets.');
