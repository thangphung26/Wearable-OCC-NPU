const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor() { this.textContent=''; this.children=[]; this.handlers={}; this.hidden=false; this.disabled=false; this.parentElement=this; this.classList={toggle(){}}; }
  addEventListener(k,fn) {this.handlers[k]=fn;}
  append(el) {this.children.push(el); el.parent=this;}
  replaceChildren() {this.children=[];}
  remove() {this.parent.children.splice(this.parent.children.indexOf(this),1);}
  get firstChild(){return this.children[0];}
  setAttribute(k,v){this[k]=v;}
  getBoundingClientRect(){return {width:0,height:0};}
  getContext(){return {};}
  getClientRects(){return [1];}
}
const elements=new Map();
const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const context={console,Date,Math,Map,JSON,Number,String,Object,Array,Set,Error,URLSearchParams,encodeURIComponent,performance,
  document:{createElement:()=>new Element(),getElementById:get,documentElement:{dataset:{}},querySelectorAll:()=>[]},
  location:{search:'?theme=light'},ResizeObserver:class{observe(){}},setInterval(){},setTimeout(){},clearTimeout(){},
  matchMedia:()=>({addEventListener(){}}), getComputedStyle:()=>({getPropertyValue:()=> '#ffffff'}),
  EventSource:class{constructor(){this.handlers={};}addEventListener(k,fn){this.handlers[k]=fn;}},
  fetch:async()=>({ok:true,json:async()=>({history:[],stats:{}})})};
context.window={addEventListener(){},devicePixelRatio:1};
vm.createContext(context);
const base=require('node:path').join(__dirname, '../frontend/');
vm.runInContext(fs.readFileSync(base+'charts.js','utf8'),context);
vm.runInContext(fs.readFileSync(base+'app.js','utf8'),context);
vm.runInContext('globalThis.app = new MedicalMonitorApp()',context);
(async()=>{
 const app=context.app;
 assert.equal(app.soundEnabled,false);
 assert.equal(get('valHR').textContent,'--');
 assert.equal(context.document.documentElement.dataset.theme,'light');
 app.setMode('simulation');app.connected=true;
 assert.match(get('ratesText').textContent,/camera FPS n\/a \(simulation\)/);
 app.status({counters_since_ms:Date.now(),valid:2});assert.match(get('counterScope').textContent,/since \d{2}:\d{2}:\d{2} · simulation/);
 const row={patient:6,source:'simulation',decoder:'<img src=x onerror=alert(1)>',timestamp_ms:Date.now(),seq:1,hr:180,spo2:94,temp_c:38,imu:3,battery:0,tx_alert_bit:1,alert:1,crc_status:'unverified',dashboard_warnings:['HR above 140 bpm','IMU state 3']};
 app.telemetry(row);
 assert.equal(get('valHR').textContent,'180');
 assert.equal(get('valBattery').textContent,'0');
 assert.equal(get('decoderText').textContent,row.decoder);
 assert.equal(app.chart.data.length,1);
 app.alarms[6]={active:true,episode_id:42,reasons:['HR above 140 bpm'],patient:6};
 app.renderFreshness();assert.equal(get('ackButton').hidden,false);
 let submitted;
 app.request=async(path,body)=>{submitted={path,body};return {};};
 await get('ackButton').handlers.click();
 assert.equal(submitted.path,'/api/alert');assert.equal(submitted.body.episode_id,42);assert.equal(submitted.body.patient,6);
 app.telemetry({...row,seq:2,hr:0,hr_valid:false});
 assert.equal(get('valHR').textContent,'--');
 assert.equal(get('alarmTitle').textContent,'Data unavailable');
 assert.equal(get('ackButton').hidden,true);
 app.telemetry({...row,seq:3});app.ageAnchor=performance.now()-26000;app.renderFreshness();
 assert.match(get('ageText').textContent,/STALE/);assert.equal(get('alarmTitle').textContent,'Data unavailable');
 assert.equal(app.alarms[6].episode_id,42);
 for(let i=0;i<420;i++)app.telemetry({...row,timestamp_ms:Date.now()+i,seq:i%16});
 assert.equal(app.history.length,360);assert.equal(app.chart.data.length,360);assert.equal(get('packetRows').children.length,5);
 for(let i=0;i<450;i++)app.appendLog({time:'<script>',text:'<img onerror=alert(1)>'});
 assert.equal(get('terminalLogs').children.length,200);
 assert.equal(get('terminalLogs').firstChild.textContent,'[<script>] <img onerror=alert(1)>');
 app.telemetry({...row,patient:2});assert.equal(app.chart.data.length,1);assert.equal(get('packetRows').children.length,1);
 app.events.handlers.init({data:JSON.stringify({mode:'simulation',telemetry:null,stats:{},alarms:{}})});assert.equal(get('valHR').textContent,'--');
 app.setMode('serial');assert.equal(get('patientSelector').disabled,true);assert.equal(get('valHR').textContent,'--');assert.equal(app.chart.data.length,0);
 // Exercise the actual canvas draw path, including invalid and outage gaps.
 const ops=[];const ctx={setTransform(){},fillRect(){},fillText(...args){ops.push(['text',...args]);},beginPath(){},moveTo(){},lineTo(){ops.push(['line']);},stroke(){},arc(){ops.push(['dot']);},fill(){}};
 get('trendCanvas').getContext=()=>ctx;app.chart.ctx=ctx;
 get('trendCanvas').getBoundingClientRect=()=>({width:700,height:330});
 app.chart.updateData([row,{...row,timestamp_ms:row.timestamp_ms+10000,hr:0},{...row,timestamp_ms:row.timestamp_ms+20000,hr:190},{...row,timestamp_ms:row.timestamp_ms+50001,hr:200}]);
 assert(ops.some(op=>op[0]==='text'&&Number(op[1])===200));
 assert.equal(ops.filter(op=>op[0]==='dot').length,19);
 context.location.search='?view=figure';
 vm.runInContext('globalThis.figureApp = new MedicalMonitorApp()',context);
 assert.equal(context.document.documentElement.dataset.theme,'light');
 assert.equal(context.document.documentElement.dataset.view,'figure');
 const figure=context.figureApp;figure.setMode('simulation');figure.connected=true;figure.telemetry(row);
 // Labelled positions remain meaningful without hue, including all four IMU states.
 const labels=[], dots=[];
 figure.chart.ctx={...ctx,fillText(text,x,y){labels.push({text,x,y});},arc(x,y){dots.push({x,y,ink:this.fillStyle});}};
 get('trendCanvas').getBoundingClientRect=()=>({width:1000,height:524});
 figure.chart.thresholds={hr_low:50,hr_high:140,spo2_low:94,temp_high_c:39,imu_warning_states:[2,3]};
 figure.chart.updateData([0,1,2,3].map((imu,i)=>({...row,imu,timestamp_ms:row.timestamp_ms+i*2000,hr:75+i})));
 const stateLabels=labels.filter(label=>['0','1','2','3'].includes(label.text) && label.x===143 && label.y>308 && label.y<398);
 assert.equal(stateLabels.length,4);
 assert.equal(new Set(stateLabels.map(label=>label.y)).size,4);
 for(const label of ['HR','SpO2','Temp','IMU state','Battery']) assert(labels.some(item=>item.text===label));
 const imuDots=dots.slice(12,16);
 assert.equal(new Set(imuDots.map(dot=>dot.y)).size,4);
 const specs=vm.runInContext('observationSpecs({hr_low:50,hr_high:140,spo2_low:94,temp_high_c:39})',context);
 assert.equal(specs[0].min,45);assert.equal(specs[0].max,145);
 const scale=vm.runInContext('observationScale({key:"hr",min:45,max:145},[{hr:210},{hr:40}])',context);
 assert.equal(scale.lo,40);assert.equal(scale.hi,210);
 const body=get('packetRows');
 figure.history=[row,{...row,seq:2},{...row,seq:3}];figure.renderPackets();
 assert.equal(body.children.length,3);
 assert(!body.children[0].children[1].textContent.includes('CRC'));
 assert(get('linkSummary').textContent.includes('CRC unverified'));
 figure.alarms[6]={active:true,episode_id:1,reasons:['HR above 140 bpm']};figure.renderFreshness();
 assert.equal(get('ackButton').hidden,true);assert.match(get('alarmDetail').textContent,/HR above/);
 context.location.search='?view=figure&theme=dark';vm.runInContext('new MedicalMonitorApp()',context);
 assert.equal(context.document.documentElement.dataset.theme,'light');
 // Reload restores backend history, preserving wrap 15→0 and concurrent SSE receipts.
 const reload=new context.app.constructor();
 reload.setMode('simulation');reload.connected=true;
 const saved=Array.from({length:32},(_,i)=>({...row,timestamp_ms:row.timestamp_ms+i*2000,seq:i%16}));
 let resolveHistory;
 reload.request=()=>new Promise(resolve=>{resolveHistory=resolve;});
 const pending=reload.loadHistory();
 reload.telemetry(saved[31]);
 const live={...saved[31],timestamp_ms:saved[31].timestamp_ms+2000,seq:0};
 reload.telemetry(live);
 resolveHistory(saved);await pending;
 assert.equal(reload.history.length,33);assert.equal(reload.chart.data.length,33);
 assert.equal(reload.current.seq,0);
 assert.equal(vm.runInContext('observationWindow',context)(reload.history,live.timestamp_ms,2).rows.length,30);
 // Same-source reconnects merge history instead of resetting it.
 reload.request=async()=>saved;
 reload.events.handlers.init({data:JSON.stringify({mode:'simulation',telemetry:live,stats:{}})});
 await new Promise(setImmediate);
 assert.equal(reload.history.length,33);
 reload.events.handlers.mode({data:JSON.stringify({mode:'simulation'})});
 assert.equal(reload.history.length,33);await new Promise(setImmediate);
 assert.equal(reload.chart.historyLoading,false);
 // An old source request must not land after switching away and back.
 reload.request=()=>new Promise(resolve=>{resolveHistory=resolve;});
 const stale=reload.loadHistory();
 reload.setMode('serial');reload.setMode('simulation');
 reload.telemetry(live);resolveHistory(saved);await stale;
 assert.equal(reload.history.length,1);
 // Newest fetch wins; arrivals during either fetch are retained and deduplicated.
 let firstResolve,secondResolve;
 reload.request=()=>new Promise(resolve=>{firstResolve=resolve;});const first=reload.loadHistory();
 reload.request=()=>new Promise(resolve=>{secondResolve=resolve;});const second=reload.loadHistory();
 secondResolve(saved);await second;firstResolve([]);await first;
 assert.equal(reload.history.length,33);
 console.log('PASS: reload/history races, sequence wrap, source isolation, labelled scales, categorical IMU, figure theme, invalid/stale values, alarms and bounded buffers');
})().catch(error=>{console.error(error);process.exitCode=1;});

// Publication figure: exercise production receipt filtering and SVG paths in isolation.
(async()=>{
 const nodes=new Map();
 class FigureElement extends Element {
  constructor(){super();this.attributes={};this.classList={add(){}};}
  appendChild(node){this.children.push(node);return node;}
  setAttribute(k,v){this.attributes[k]=String(v);}
  getBoundingClientRect(){return {width:586,height:530};}
 }
 const node=id=>{if(!nodes.has(id))nodes.set(id,new FigureElement());return nodes.get(id);};
 const f={console,Date,URLSearchParams,encodeURIComponent,Number,Object,Map,JSON,Math,
  window:{location:{search:'?freeze=1'},addEventListener(){}},
  document:{body:new FigureElement(),getElementById:node,createElementNS:()=>new FigureElement()}};
 vm.createContext(f);
 const source=fs.readFileSync(base+'figure.js','utf8').replace('  loadData();','  globalThis.figure = {appState, setMode, receive, stats, updateUI, loadHistory};');
 vm.runInContext(source,f);
 const api=f.figure, now=Date.now();
 api.setMode('simulation');
 const thresholds={hr_low:60,hr_high:120,spo2_low:96,temp_high_c:38,imu_warning_states:[1,3]};
 const row={source:'simulation',patient:2,timestamp_ms:now,seq:1,hr:80,spo2:98,temp_c:37,imu:3,battery:7,tx_alert_bit:0,crc_status:'valid'};
 api.receive(row);api.stats({data_age_sec:0,snapshot_period_s:10,thresholds});
 api.appState.history=[
  {...row,timestamp_ms:now-60000},
  {...row,timestamp_ms:now-50000,hr_valid:false,battery_valid:false,imu_valid:false},
  {...row,timestamp_ms:now-40000},
  {...row,timestamp_ms:now-20000},row,
  {...row,patient:3,hr:999,timestamp_ms:now-10000},
  {...row,source:'serial',hr:999,timestamp_ms:now-10000},
  {...row,timestamp_ms:now+10000,hr:999}
 ];
 api.updateUI();
 assert.match(node('trendSummaryMeta').textContent,/5 snapshots/);
 assert.equal(node('valCrcStatus').textContent,'Unverified');
 assert.equal(node('valImu').textContent,'3 · protocol flag');
 assert.match(node('limitHr').textContent,/60–120/);
 const paths=node('trendSvg').children.filter(n=>n.attributes['data-series']);
 for(const key of ['hr','imu','battery']){
  const d=paths.find(n=>n.attributes['data-series']===key).attributes.d;
  assert.equal((d.match(/M /g)||[]).length,4,`${key}: invalid and missed receipts break paths`);
 }
 for(let imu=0;imu<4;imu++){
  api.receive({...row,imu});api.updateUI();
  assert.equal(node('valImu').textContent,`${imu} · ${['still','moving','fall flag','protocol flag'][imu]}`);
 }
 api.stats({data_age_sec:25,snapshot_period_s:10,thresholds});api.updateUI();
 assert.equal(node('statusHr').textContent,'stale · held value');
 api.receive(null);api.updateUI();
 assert.equal(node('valHr').textContent,'--');assert.equal(node('valAlertBit').textContent,'--');
 api.receive({...row,source:'serial'});assert.equal(api.appState.currentTelemetry,null);
 // A response for a previous source cannot return after an away-and-back switch.
 let resolve;
 f.fetch=()=>new Promise(r=>{resolve=r;});
 const pending=api.loadHistory();api.setMode('serial');api.setMode('simulation');
 resolve({ok:true,json:async()=>[row]});await pending;assert.equal(api.appState.history.length,0);
 console.log('PASS: publication patient/source isolation, freeze cutoff, gaps including Battery, all IMU labels, received thresholds, stale/null values and generation guard');
})().catch(error=>{console.error(error);process.exitCode=1;});
