// Creates an ignored local personal backup. It does not upload data or read CalDAV credentials.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),source=process.argv[2]||'D:\\OneDrive\\HLJY\\工作台统计';
const html=fs.readFileSync(path.join(source,'_教学工作台.html'),'utf8');
const raw=html.match(/<script id="wb-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const js=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].at(-1)[1];
const file=fs.readFileSync(path.join(source,'_calendar_data.js'),'utf8').replace(/^\uFEFF/,'');
const calendar=JSON.parse(file.slice(file.indexOf('{')).trim().replace(/;$/,''));
const state=fs.readFileSync(path.join(source,'_workbench_data.json'),'utf8').replace(/^\uFEFF/,'');
const script=js.slice(0,js.indexOf('/* ========== 事件绑定 / 启动 ========== */'))+`
window.bundle={version:1,meta:{source:'local-migration',exportedAt:new Date().toISOString()},settings:Object.assign({},settings(),{courseRoster:COURSES}),events:rawEvents().filter(e=>!e.manual),todos:rawTodos(),fix:S.fix,added:S.added,extra:S.extra,money:S.money,overtime:S.overtime,routes:S.routes};
};window.__wbInit(CALENDAR);`;
const context={window:{},CALENDAR:calendar,console,document:{querySelector:s=>{if(s!=='#wb-data')throw Error('Unexpected DOM access');return {textContent:raw};}},localStorage:{getItem:key=>key==='wb.teaching.v1'?state:key==='wb.teaching.calEpoch1'?'1':null,setItem(){},removeItem(){}}};
vm.runInNewContext(script,context);
const out=path.join(root,'private-export');fs.mkdirSync(out,{recursive:true});
const target=path.join(out,'workbench.personal.json');fs.writeFileSync(target,JSON.stringify(context.window.bundle));
console.log('个人迁移备份已生成（不会提交到 Git）：'+target);
console.log('打开在线工作台 → 日历与数据 → 选择文件，导入此文件。');
