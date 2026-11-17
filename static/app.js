"use strict";

const $ = id => document.getElementById(id);
const CATEGORIES = { research: "研究・修論", philosophy: "哲学", llm: "LLM" };
const PLACES = { home:"自宅", lab:"研究室", travel:"移動中", other:"その他" };
const PLAN_TYPES = { research:"研究・修論",philosophy:"哲学（任意）",llm:"LLM（任意）",fixed:"固定予定",break:"休憩・食事",margin:"マージン",...PLACES };
const isPlaceKind = k => Object.prototype.hasOwnProperty.call(PLACES,k);
const planName = s => s.label || PLAN_TYPES[s.kind] || "予定";
const WEEKDAYS = ["月","火","水","木","金","土","日"];
const PX_PER_HOUR = 108;
const PX_PER_MIN = PX_PER_HOUR / 60;
const state = {
  selectedDate: japanToday(),
  view:"day",
  day:null,
  themes:[],
  activeCategory:"research",
  weekStart:weekMonday(japanToday()),
  summary:null,
  logEditingId:null,
  placeEditingId:null,
  planEditing:null,
  choiceSlot:null,
  showPlaces:loadShowPlaces(),
  toastTimeout:null,
  navigationSerial:0,
};

function loadShowPlaces(){try{return localStorage.getItem("focuslog.showPlaces")!=="0";}catch(e){return true;}}
function setShowPlaces(v){
  state.showPlaces=v;$("toggle-places").checked=v;
  try{localStorage.setItem("focuslog.showPlaces",v?"1":"0");}catch(e){}
  renderDay(true);
}
function nowMinutesJapan(){return toMinutes(new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Tokyo",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date()));}
function japanToday(){
  const p = new Intl.DateTimeFormat("en-US",{timeZone:"Asia/Tokyo",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());
  const obj=Object.fromEntries(p.map(x=>[x.type,x.value]));
  return `${obj.year}-${obj.month}-${obj.day}`;
}
function addDays(iso,n){const d=new Date(`${iso}T12:00:00Z`);d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10);}
function weekMonday(day){const dow=(new Date(`${day}T12:00:00Z`).getUTCDay()+6)%7;return addDays(day,-dow);}
function weekday(day){return (new Date(`${day}T12:00:00Z`).getUTCDay()+6)%7;}
function fmtDay(day,short=false){const [y,m,d]=day.split("-");return short?`${Number(m)}/${Number(d)}`:`${y}年${Number(m)}月${Number(d)}日（${WEEKDAYS[weekday(day)]}）`;}
function toMinutes(str){if(!str||!/^\d\d:\d\d$/.test(str))return NaN;const [h,m]=str.split(":").map(Number);return h*60+m;}
function toTime(n){return `${String(Math.floor(n/60)).padStart(2,"0")}:${String(n%60).padStart(2,"0")}`;}
function fmtMinutes(n){n=Math.max(0,Math.round(n)||0);return `${Math.floor(n/60)}h ${String(n%60).padStart(2,"0")}m`;}
function timeLength(x){return Math.max(0,toMinutes(x.end)-toMinutes(x.start));}
function escapeHTML(str){return String(str??"").replace(/[&<>"']/g,x=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[x]));}
function formatApiError(err){return err instanceof Error?err.message:String(err);}
function showToast(message,isError=false){
  const el=$("toast");el.textContent=message;el.classList.toggle("error",isError);el.hidden=false;
  if(state.toastTimeout)clearTimeout(state.toastTimeout);
  state.toastTimeout=setTimeout(()=>{el.hidden=true;},4600);
}
async function api(path,options={}){
  let response;
  try{
    response=await fetch(path,{headers: {"Content-Type":"application/json"},cache:"no-store",...options});
  }catch(e){setConnected(false);throw new Error("サーバーに接続できません。起動状態・ネットワークを確認してください。");}
  let data;
  try{data=await response.json();}catch(e){throw new Error("サーバーから読み取れない応答が返されました。");}
  if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
  setConnected(true);
  return data;
}
function setConnected(ok){$("connection-dot").classList.toggle("online",ok);$("connection-label").textContent=ok?"保存先に接続中":"接続できません";}
function request(path,method,body){return api(path,{method,body:JSON.stringify(body)});}
function icon(id){return `<svg class="icon"><use href="#i-${id}"/></svg>`;}

function init(){
  document.querySelectorAll("[data-view]").forEach(btn=>btn.addEventListener("click",()=>setView(btn.dataset.view)));
  $("prev-day").addEventListener("click",()=>switchDay(addDays(state.selectedDate,-1)));
  $("next-day").addEventListener("click",()=>switchDay(addDays(state.selectedDate,1)));
  $("go-today").addEventListener("click",()=>switchDay(japanToday()));
  $("open-date").addEventListener("click",()=>{
    const input=$("date-input");input.value=state.selectedDate;
    try{if(typeof input.showPicker==="function")input.showPicker();else input.click();}
    catch(e){input.focus();input.click();}
  });
  $("date-input").addEventListener("change",e=>{if(e.target.value)switchDay(e.target.value);});
  $("add-log").addEventListener("click",()=>openLogDialog());
  $("add-log-inline").addEventListener("click",()=>openLogDialog());
  $("add-plan").addEventListener("click",()=>openPlanDialog());
  $("add-place-inline").addEventListener("click",()=>openPlaceDialog());
  $("toggle-places").checked=state.showPlaces;
  $("toggle-places").addEventListener("change",e=>setShowPlaces(e.target.checked));
  $("place-form").addEventListener("submit",savePlace);
  $("delete-place").addEventListener("click",deletePlace);
  ["place-start","place-end"].forEach(id=>$(id).addEventListener("input",updatePlaceDuration));
  $("choice-place").addEventListener("click",()=>{const s=state.choiceSlot;$("choice-dialog").close();openPlaceDialog(null,s);});
  $("choice-activity").addEventListener("click",()=>{const s=state.choiceSlot;$("choice-dialog").close();openLogDialog(null,s);});
  $("choice-edit").addEventListener("click",()=>{const s=state.choiceSlot;$("choice-dialog").close();openPlanDialog(s);});
  $("scroll-start").addEventListener("click",()=>{$("timeline-scroll").scrollTo({left:0,behavior:"smooth"});});
  $("scroll-now").addEventListener("click",scrollToNow);
  $("prev-week").addEventListener("click",()=>switchWeek(addDays(state.weekStart,-7)));
  $("next-week").addEventListener("click",()=>switchWeek(addDays(state.weekStart,7)));
  $("current-week").addEventListener("click",()=>switchWeek(weekMonday(japanToday())));
  $("log-form").addEventListener("submit",saveLog);
  $("delete-log").addEventListener("click",deleteLog);
  ["log-start","log-end","log-day"].forEach(id=>$(id).addEventListener("input",updateLogValidation));
  $("log-category").addEventListener("change",refreshThemeSuggestions);
  $("plan-form").addEventListener("submit",savePlan);
  $("delete-plan").addEventListener("click",deletePlan);
  $("plan-kind").addEventListener("change",updatePlanKindFields);
  document.querySelectorAll("[data-close-dialog]").forEach(b=>b.addEventListener("click",()=>$(b.dataset.closeDialog).close()));
  document.querySelectorAll(".modal").forEach(dialog=>dialog.addEventListener("click",e=>{
    if(e.target===dialog)dialog.close();
  }));
  $("timeline-settings-form").addEventListener("submit",saveDisplaySettings);
  document.querySelectorAll(".filter-chip").forEach(b=>b.addEventListener("click",()=>{
    state.activeCategory=b.dataset.category;
    document.querySelectorAll(".filter-chip").forEach(x=>x.classList.toggle("active",x===b));
    renderThemes();
  }));
  $("new-theme-form").addEventListener("submit",createTheme);
  Promise.all([loadDay(),loadThemes()]).catch(e=>showToast(formatApiError(e),true));
}

function setView(view){
  if(!["day","summary","settings"].includes(view))return;
  state.view=view;
  document.querySelectorAll(".view").forEach(e=>e.classList.toggle("active",e.id===`${view}-view`));
  document.querySelectorAll("[data-view]").forEach(e=>{
    e.classList.toggle("active",e.dataset.view===view);
    if(e.dataset.view===view)e.setAttribute("aria-current","page");else e.removeAttribute("aria-current");
  });
  if(view==="summary")loadSummary();
  if(view==="settings")renderSettings();
}
async function switchDay(day){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(day))return;
  state.selectedDate=day;
  await loadDay(false);
}
async function loadDay(keepScroll=false){
  const requestId=++state.navigationSerial;
  const data=await api(`/api/day?date=${encodeURIComponent(state.selectedDate)}`);
  if(requestId!==state.navigationSerial)return;
  state.day=data;
  renderDay(keepScroll);
}
function renderDay(keepScroll=false){
  if(!state.day)return;
  const data=state.day;
  $("date-label").textContent=fmtDay(data.date);
  $("date-input").value=data.date;
  $("day-plan-state").textContent=data.applied_pattern?`パターン：${data.applied_pattern}`:data.plan_source==="override"?"この日の個別設定":"毎週のモデル";
  renderPatternSwitch();
  const researchPlan=data.plan.reduce((s,x)=>s+(Number(x.goal_minutes)||0),0);
  const totals={research:0,philosophy:0,llm:0};
  for(const log of data.logs)totals[log.category]+=timeLength(log);
  $("metric-research").textContent=fmtMinutes(totals.research);
  $("metric-research-plan").textContent=`予定 ${fmtMinutes(researchPlan)}`;
  $("metric-philosophy").textContent=fmtMinutes(totals.philosophy);
  $("metric-llm").textContent=fmtMinutes(totals.llm);
  const delta=totals.research-researchPlan;
  const notStarted=data.logs.length===0&&data.date>=japanToday();
  $("metric-research-diff").textContent=notStarted?"実績はまだ入力されていません":delta===0?"予定との差 ±0分":`${delta>0?"予定より +":"予定まで -"}${fmtMinutes(Math.abs(delta))}`;
  $("metric-research-diff").classList.toggle("good",!notStarted&&delta>=0);
  $("metric-research-diff").classList.toggle("warn",!notStarted&&delta<0);
  $("legend-places").hidden=!state.showPlaces;
  $("place-card").hidden=!state.showPlaces;
  renderTimeline(keepScroll);
  renderPlaces();
  renderLogs();
}
// 曜日ごとの別パターン（例：火曜「輪読なし」、金曜「ゼミなし」）。「通常」で解除して毎週のモデルに戻す。
function renderPatternSwitch(){
  const data=state.day;const sw=$("pattern-switch");const pats=data.patterns||[];
  sw.hidden=!pats.length;sw.replaceChildren();if(!pats.length)return;
  const label=document.createElement("span");label.className="pattern-label";label.textContent="パターン";sw.append(label);
  const items=[{id:null,name:"通常",active:data.plan_source==="weekly"},...pats.map(p=>({...p,active:data.applied_pattern===p.name}))];
  for(const p of items){
    const b=document.createElement("button");b.type="button";b.textContent=p.name;b.className="pattern-chip"+(p.active?" active":"");
    b.setAttribute("aria-pressed",String(p.active));
    b.addEventListener("click",()=>switchPattern(p));sw.append(b);
  }
}
async function switchPattern(p){
  const data=state.day;if(p.active)return;
  const manual=data.plan_source==="override"&&!data.applied_pattern;
  try{
    if(p.id===null){
      if(!confirm(`${fmtDay(data.date)}を通常（毎週のモデル）に戻しますか？${manual?"\nこの日だけ変更した予定は削除されます。":""}`))return;
      state.day=await request("/api/day-override","DELETE",{date:data.date});
      showToast("通常の予定に戻しました。");
    }else{
      if(data.plan_source==="override"&&!confirm(`この日の予定を「${p.name}」に置き換えますか？${manual?"\nこの日だけ変更した予定は削除されます。":""}`))return;
      state.day=await request("/api/day-pattern","POST",{date:data.date,pattern_id:p.id});
      showToast(`「${p.name}」の予定にしました。戻すときは「通常」を押してください。`);
    }
    renderDay(true);
  }catch(e){showToast(formatApiError(e),true);}
}
function renderPlaces(){
  const parent=$("place-list");parent.replaceChildren();
  const items=state.day.place_logs||[];
  const totals={};for(const p of items)totals[p.kind]=(totals[p.kind]||0)+timeLength(p);
  const parts=Object.keys(PLACES).filter(k=>totals[k]).map(k=>`${PLACES[k]} ${fmtMinutes(totals[k])}`);
  $("place-totals").textContent=parts.length?`${parts.join(" · ")}（活動時間には加算しません）`:"活動時間には加算しません。";
  if(!items.length){parent.innerHTML=`<div class="empty-state compact">この日の場所・移動の記録はありません。</div>`;return;}
  for(const p of items){
    const row=document.createElement("div");row.className="log-item compact";
    row.innerHTML=`<div class="log-time">${escapeHTML(p.start)}–${escapeHTML(p.end)}</div>
      <div class="log-item-main"><div class="log-title"><span class="log-category"><span class="category-dot place-${escapeHTML(p.kind)}"></span>${escapeHTML(PLACES[p.kind]||p.kind)}</span></div>${p.memo?`<div class="log-memo">${escapeHTML(p.memo)}</div>`:""}</div>
      <div class="log-length">${fmtMinutes(timeLength(p))}</div><button type="button" class="icon-button log-edit" aria-label="場所・移動の記録を編集" title="編集">${icon("edit")}</button>`;
    row.querySelector("button").addEventListener("click",()=>openPlaceDialog(p));parent.appendChild(row);
  }
}

function partitionOverlapping(logs){
  const sorted=logs.slice().sort((a,b)=>toMinutes(a.start)-toMinutes(b.start)||toMinutes(a.end)-toMinutes(b.end));
  const occupied=[];
  return sorted.map(log=>{
    const start=toMinutes(log.start),end=toMinutes(log.end);
    let lane=occupied.findIndex(x=>x<=start);
    if(lane<0){lane=occupied.length;occupied.push(end);}else occupied[lane]=end;
    return {...log,row:lane};  // lane（場所/活動）は上書きしない
  });
}
function renderTimeline(keepScroll=false){
  if(!state.day)return;
  const data=state.day;
  const start=toMinutes(data.preferences.timeline_start||"07:00");
  const end=toMinutes(data.preferences.timeline_end||"23:00");
  const width=Math.max(300,(end-start)*PX_PER_MIN);
  const scroll=$("timeline-scroll");const old=scroll.scrollLeft;
  const canvas=$("timeline-canvas");canvas.replaceChildren();canvas.style.width=`${width}px`;
  const gutter=$("timeline-gutter");gutter.replaceChildren();
  const show=state.showPlaces;
  const el=(tag,cls,text,style={},parent=canvas)=>{
    const x=document.createElement(tag);if(cls)x.className=cls;if(text!==null&&text!==undefined)x.textContent=text;
    Object.assign(x.style,style);parent.appendChild(x);return x;
  };

  // ---- 4段のレイアウト（場所・移動の段は非表示にできる） ----
  const lanesOf=items=>{const l=partitionOverlapping(items);return {items:l,count:Math.max(1,...l.map(x=>x.row+1))};};
  const L={
    planPlace:lanesOf(data.plan.filter(s=>s.lane==="place")),
    planAct:lanesOf(data.plan.filter(s=>s.lane!=="place")),
    logPlace:lanesOf(data.place_logs||[]),
    logAct:lanesOf(data.logs),
  };
  const SLIM={bar:26,step:30},TALL={bar:44,step:50};
  const rows=[],groups=[];let y=40;
  const openGroup=(key,title)=>{const g={key,title,top:y};groups.push(g);y+=22;return g;};
  const addRow=(key,label,spec)=>{
    const lanes=L[key];const r={key,label,spec,lanes,top:y,height:lanes.count*spec.step-(spec.step-spec.bar)};
    rows.push(r);y+=lanes.count*spec.step;return r;
  };
  const gModel=openGroup("model","モデル");
  if(show)addRow("planPlace","場所",SLIM);
  const planActRow=addRow("planAct","活動",TALL);
  gModel.bottom=y;y+=14;
  const gLog=openGroup("log","実績");
  if(show)addRow("logPlace","場所",SLIM);
  addRow("logAct","活動",TALL);
  gLog.bottom=y;y+=12;
  canvas.style.height=`${y}px`;gutter.style.height=`${y}px`;

  // グループの背景帯と、左端に固定したラベル列
  for(const g of groups){
    el("div",`timeline-group group-${g.key}`,null,{top:`${g.top-4}px`,height:`${g.bottom-g.top+6}px`,width:`${width}px`});
    el("div",`gutter-group group-${g.key}`,g.title,{top:`${g.top}px`},gutter);
  }
  for(const r of rows)el("div","gutter-row",r.label,{top:`${r.top}px`,height:`${r.height}px`},gutter);

  // 時刻の目盛り
  for(let t=Math.ceil(start/60)*60;t<=end;t+=60){
    const x=(t-start)*PX_PER_MIN;
    el("div","time-grid-line",null,{left:`${x}px`});
    const c=t===start?"start":t===end?"end":"";
    el("span",`time-tick ${c}`,toTime(t),{left:`${x}px`});
  }
  if(start%60!==0)el("span","time-tick start",toTime(start),{left:"0px"});
  if(end%60!==0)el("span","time-tick end",toTime(end),{left:`${width}px`});

  // 各段の空白：クリックした時刻（15分単位で切り捨て）から1時間の新規登録
  const ROW_HINT={planPlace:"クリックで場所・移動の予定を追加",planAct:"クリックで活動の予定を追加",logPlace:"クリックで場所・移動を記録",logAct:"クリックで活動の実績を追加"};
  for(const r of rows){
    const area=el("div","timeline-placeholder clickable",null,{left:"0px",top:`${r.top}px`,height:`${r.height}px`,width:`${width}px`});
    area.title=ROW_HINT[r.key];
    area.addEventListener("click",e=>{
      const x=e.clientX-area.getBoundingClientRect().left;
      let a=start+Math.floor(x/PX_PER_MIN/15)*15;
      a=Math.max(start,Math.min(a,end-15));
      const slot={start:toTime(a),end:toTime(Math.min(end,a+60,23*60+59))};
      if(r.key==="logAct")openLogDialog(null,slot);
      else if(r.key==="logPlace")openPlaceDialog(null,slot);
      else openPlanDialog(null,{...slot,lane:r.key==="planPlace"?"place":"activity"});
    });
    if((r.key==="logAct"||r.key==="logPlace")&&!r.lanes.items.length)
      el("span","row-empty-hint",r.key==="logAct"?"空白をクリックして活動を記録":"空白をクリックして場所・移動を記録",{top:`${r.top+r.height/2}px`});
  }

  // 移動中の研究目標：時刻未指定なので、移動の時間帯全体に点線の枠で示す（確定した作業予定としては描かない）
  for(const s of L.planPlace.items.filter(s=>s.kind==="travel"&&s.goal_minutes>0)){
    const a=Math.max(start,toMinutes(s.start)),b=Math.min(end,toMinutes(s.end));if(a>=b)continue;
    const w=(b-a)*PX_PER_MIN;
    const g=el("button","timeline-block goal-ghost",null,{left:`${(a-start)*PX_PER_MIN+1}px`,width:`${Math.max(2,w-2)}px`,top:`${planActRow.top}px`,height:`${TALL.bar}px`});
    g.type="button";
    g.title=`研究目標 ${s.goal_minutes}分（時刻未指定）\n${planName(s)} ${s.start}–${s.end} の中で合計${s.goal_minutes}分`;
    g.setAttribute("aria-label",g.title.replaceAll("\n"," "));
    if(w>38){
      const main=document.createElement("span");main.className="block-name";main.textContent=`研究目標 ${s.goal_minutes}分`;g.appendChild(main);
      if(w>102){const sub=document.createElement("small");sub.textContent="時刻未指定";g.appendChild(sub);}
    }
    g.addEventListener("click",()=>openChoice(s));
  }

  function makeBlock(entry,row){
    const a=Math.max(start,toMinutes(entry.start));const b=Math.min(end,toMinutes(entry.end));if(a>=b)return;
    const left=(a-start)*PX_PER_MIN;const w=(b-a)*PX_PER_MIN;
    const slim=row.spec===SLIM;const time=`${entry.start}–${entry.end}`;
    let cls,label,sub,title,onClick;
    if(row.key==="planPlace"){
      cls=`place-${entry.kind}`;label=planName(entry);sub=entry.goal_minutes?`研究目標${entry.goal_minutes}分`:time;
      title=`モデル（場所・移動）：${PLACES[entry.kind]} ${entry.label||""}\n${time}${entry.goal_minutes?`\n研究目標 ${entry.goal_minutes}分（時刻未指定）`:""}`;
      onClick=()=>openChoice(entry);
    }else if(row.key==="planAct"){
      cls=`kind-${entry.kind}`;label=planName(entry);sub=time;
      title=`モデル（活動）：${label}\n${time}`;onClick=()=>openChoice(entry);
    }else if(row.key==="logPlace"){
      cls=`place-${entry.kind}`;label=PLACES[entry.kind]||entry.kind;sub=entry.memo||time;
      title=`実績（場所・移動）：${label}\n${time}${entry.memo?"\n"+entry.memo:""}`;onClick=()=>openPlaceDialog(entry);
    }else{
      cls=`kind-${entry.category}`;label=entry.theme||entry.content;sub=time;
      title=`実績（活動）：${CATEGORIES[entry.category]} ${label}\n${time}\n${entry.content}`;onClick=()=>openLogDialog(entry);
    }
    const bttn=el("button",`timeline-block ${cls}${slim?" slim":""}`,null,{
      left:`${left+1}px`,width:`${Math.max(2,w-2)}px`,top:`${row.top+entry.row*row.spec.step}px`,height:`${row.spec.bar}px`
    });
    bttn.type="button";bttn.title=title;bttn.setAttribute("aria-label",title.replaceAll("\n"," "));
    if(w>38){
      const main=document.createElement("span");main.className="block-name";main.textContent=label;bttn.appendChild(main);
      if(slim?w>170:w>102){const s=document.createElement("small");s.textContent=sub;bttn.appendChild(s);}
    }
    bttn.addEventListener("click",onClick);
  }
  for(const r of rows)r.lanes.items.forEach(e=>makeBlock(e,r));

  const now=nowMinutesJapan();
  if(data.date===japanToday()&&now>=start&&now<=end)el("div","timeline-now",null,{left:`${(now-start)*PX_PER_MIN}px`});
  scroll.scrollLeft=keepScroll?old:0;
  $("scroll-start").textContent=`${toTime(start)}へ`;
  $("scroll-now").disabled=data.date!==japanToday()||now<start||now>end;
  const existing=$("reset-day-override");if(existing)existing.remove();
  if(data.plan_source==="override"){
    const reset=document.createElement("button");reset.type="button";reset.id="reset-day-override";reset.className="text-button";reset.textContent="この日の例外設定を解除";
    reset.addEventListener("click",async()=>{
      if(!confirm(`${fmtDay(data.date)}の個別予定を削除し、毎週の基本モデルに戻しますか？`))return;
      try{const d=await request("/api/day-override","DELETE",{date:data.date});state.day=d;renderDay(true);showToast("毎週のモデルに戻しました。");}catch(e){showToast(formatApiError(e),true);}
    });
    $("add-plan").before(reset);
  }
}
function openChoice(slot){
  state.choiceSlot=slot;
  $("choice-title").textContent=planName(slot);
  const goal=slot.kind==="travel"&&slot.goal_minutes?` · 研究目標${slot.goal_minutes}分（時刻未指定）`:"";
  $("choice-sub").textContent=`${slot.start}–${slot.end} · モデル（${slot.lane==="place"?"場所・移動":"活動"}）${goal}`;
  $("choice-dialog").showModal();
}
function scrollToNow(){
  if(!state.day)return;
  const prefs=state.day.preferences;
  const now=toMinutes(new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Tokyo",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date()));
  const x=(now-toMinutes(prefs.timeline_start))*PX_PER_MIN;
  $("timeline-scroll").scrollTo({left:Math.max(0,x-$("timeline-scroll").clientWidth*.45),behavior:"smooth"});
}
function renderLogs(){
  const parent=$("log-list");parent.replaceChildren();
  const logs=state.day.logs;
  if(!logs.length){parent.innerHTML=`<div class="empty-state">${icon("list")}この日の作業実績はまだありません。<br>「実績を追加」から開始・終了時刻と作業内容を入力してください。</div>`;return;}
  for(const log of logs){
    const row=document.createElement("div");row.className="log-item";
    row.innerHTML=`<div class="log-time">${escapeHTML(log.start)}–${escapeHTML(log.end)}</div>
      <div class="log-item-main"><div class="log-meta"><span class="log-category"><span class="category-dot ${escapeHTML(log.category)}"></span>${escapeHTML(CATEGORIES[log.category])}</span>${log.theme?`<span class="log-theme">${escapeHTML(log.theme)}</span>`:""}</div><div class="log-title">${escapeHTML(log.content)}</div>${log.memo?`<div class="log-memo">${escapeHTML(log.memo)}</div>`:""}</div>
      <div class="log-length">${fmtMinutes(timeLength(log))}</div><button type="button" class="icon-button log-edit" aria-label="作業実績を編集" title="編集">${icon("edit")}</button>`;
    row.querySelector("button").addEventListener("click",()=>openLogDialog(log));parent.appendChild(row);
  }
}

async function loadThemes(){state.themes=await api("/api/themes");refreshThemeSuggestions();renderThemes();}
function refreshThemeSuggestions(){
  const sel=$("log-category").value;
  $("theme-suggestions").innerHTML=state.themes.filter(x=>!x.archived&&x.category===sel).map(x=>`<option value="${escapeHTML(x.name)}"></option>`).join("");
}
function openLogDialog(log=null,plan=null){
  state.logEditingId=log?.id??null;
  $("log-dialog-title").textContent=log?"作業実績を編集":"作業実績を追加";
  $("log-day").value=log?.day||state.selectedDate;
  // モデルから追加する場合は、その予定の開始・終了をそのまま初期値にする（保存前に自由に変更可）
  $("log-start").value=log?.start||plan?.start||"07:00";
  $("log-end").value=log?.end||plan?.end||"08:00";
  $("log-category").value=log?.category||(Object.hasOwn(CATEGORIES,plan?.kind??"")?plan.kind:"research");
  $("log-theme").value=log?.theme||"";$("log-content").value=log?.content||"";$("log-memo").value=log?.memo||"";
  $("delete-log").hidden=!log;
  refreshThemeSuggestions();updateLogValidation();$("log-dialog").showModal();
  if(!log&&!plan)$("log-start").focus();else if(!log)$("log-content").focus();
}
function updateLogValidation(){
  const st=toMinutes($("log-start").value),en=toMinutes($("log-end").value);
  $("log-duration").textContent=Number.isFinite(st)&&Number.isFinite(en)&&en>st?`作業時間：${fmtMinutes(en-st)}`:"終了時刻は開始時刻より後に設定してください";
  const day=$("log-day").value;
  const clashes=(state.day?.logs||[]).filter(x=>x.id!==state.logEditingId&&x.day===day&&toMinutes(x.start)<en&&toMinutes(x.end)>st);
  const warning=$("log-overlap-warning");
  warning.hidden=clashes.length===0;
  if(clashes.length)warning.textContent=`この日の既存の作業実績 ${clashes.length} 件と時間が重複します。保存は可能ですが、集計では両方の時間が加算されます。`;
}
async function saveLog(e){
  e.preventDefault();
  const payload={day:$("log-day").value,start:$("log-start").value,end:$("log-end").value,
    category:$("log-category").value,theme:$("log-theme").value.trim(),content:$("log-content").value.trim(),memo:$("log-memo").value.trim()};
  if(toMinutes(payload.end)<=toMinutes(payload.start)){showToast("終了時刻は開始時刻より後にしてください。",true);return;}
  const id=state.logEditingId;const b=$("submit-log");b.disabled=true;
  try{
    const result=await request(id?`/api/logs/${id}`:"/api/logs",id?"PATCH":"POST",payload);
    $("log-dialog").close();
    state.selectedDate=payload.day;state.day=result.day;
    renderDay(true);await loadThemes();
    showToast(result.overlap_count?`保存しました（他の実績 ${result.overlap_count} 件と時間が重複しています）。`:`作業実績を${id?"更新":"保存"}しました。`);
  }catch(err){showToast(formatApiError(err),true);}finally{b.disabled=false;}
}
async function deleteLog(){
  if(!state.logEditingId||!confirm("この作業実績を削除しますか？"))return;
  try{const data=await request(`/api/logs/${state.logEditingId}`,"DELETE",{});$("log-dialog").close();state.day=data;renderDay(true);showToast("実績を削除しました。");}
  catch(e){showToast(formatApiError(e),true);}
}

function updatePlanKindFields(){
  const kind=$("plan-kind").value;const place=isPlaceKind(kind);
  $("goal-field").hidden=kind!=="travel";
  $("plan-label").required=!place;
  $("plan-label-text").textContent=place?"名前・メモ":"予定名";
  $("plan-label-optional").hidden=!place;
  $("plan-label").placeholder=place?"例：通学 / 帰宅":"例：研究・修論";
}
function openPlanDialog(plan=null,preset=null){
  state.planEditing=plan;
  $("plan-dialog-title").textContent=plan?"モデル予定を編集":"モデル予定を追加";
  $("plan-start").value=plan?.start||preset?.start||"07:00";
  $("plan-end").value=plan?.end||preset?.end||"08:00";
  $("plan-label").value=plan?.label||"";
  $("plan-kind").value=plan?.kind||(preset?.lane==="place"?"travel":"research");
  $("plan-goal").value=plan?.goal_minutes||0;
  $("delete-plan").hidden=!plan;
  document.querySelector('input[name="plan-scope"][value="date"]').checked=true;
  // A date-only custom slot cannot be edited as a recurring slot, but a weekly slot can.
  $("weekly-scope-label").style.display=plan&&!plan.weekly_id?"none":"flex";
  $("plan-scope-note").textContent=state.day.plan_source==="override"?
    "この日には個別設定があります。毎週の変更を選んでも、この日の個別設定には反映されません。":
    "毎週の変更は同じ曜日の基本モデルに反映されます。過去の実績は変わりません。";
  updatePlanKindFields();$("plan-dialog").showModal();
}
function planPayload(){
  const start=$("plan-start").value,end=$("plan-end").value,kind=$("plan-kind").value;
  const dur=toMinutes(end)-toMinutes(start);
  if(!Number.isFinite(dur)||dur<=0)throw new Error("終了時刻は開始時刻より後にしてください。");
  let goal=0;
  if(kind==="research")goal=dur;
  if(kind==="travel"){
    goal=Number($("plan-goal").value);
    if(!Number.isInteger(goal)||goal<0||goal>dur)throw new Error("移動中の研究時間は、移動の所要時間以内の整数（分）で指定してください。");
  }
  return {date:state.selectedDate,scope:document.querySelector('input[name="plan-scope"]:checked').value,
    id:state.planEditing?.id??null,
    slot:{start,end,label:$("plan-label").value.trim(),kind,goal_minutes:goal}};
}
async function savePlan(e){
  e.preventDefault();
  let payload;try{payload=planPayload();}catch(err){showToast(formatApiError(err),true);return;}
  const id=state.planEditing?.id;const submit=$("plan-form").querySelector('button[type="submit"]');submit.disabled=true;
  try{state.day=await request("/api/plans",id?"PATCH":"POST",payload);$("plan-dialog").close();renderDay(true);showToast("モデル時間割を保存しました。");}
  catch(err){showToast(formatApiError(err),true);}finally{submit.disabled=false;}
}
async function deletePlan(){
  const s=state.planEditing;if(!s)return;
  const scope=document.querySelector('input[name="plan-scope"]:checked').value;
  if(!confirm(`${planName(s)}（${s.start}–${s.end}）を${scope==="weekly"?"毎週の基本モデル":"この日のモデル"}から削除しますか？`))return;
  try{
    state.day=await request("/api/plans","DELETE",{date:state.selectedDate,scope,id:s.id});
    $("plan-dialog").close();renderDay(true);showToast("モデル予定を削除しました。");
  }catch(err){showToast(formatApiError(err),true);}
}

function openPlaceDialog(entry=null,plan=null){
  state.placeEditingId=entry?.id??null;
  $("place-dialog-title").textContent=entry?"場所・移動の実績を編集":"場所・移動の実績を追加";
  $("place-day").value=entry?.day||state.selectedDate;
  $("place-start").value=entry?.start||plan?.start||"07:00";
  $("place-end").value=entry?.end||plan?.end||"08:00";
  // 場所・移動のモデルからなら種類も初期選択。活動のモデル（例：バイト）から場所を推測はしない。
  const kind=entry?.kind||(plan&&isPlaceKind(plan.kind)?plan.kind:null);
  document.querySelectorAll('input[name="place-kind"]').forEach(r=>{r.checked=r.value===kind;});
  $("place-memo").value=entry?.memo||"";
  $("delete-place").hidden=!entry;
  updatePlaceDuration();$("place-dialog").showModal();
}
function updatePlaceDuration(){
  const st=toMinutes($("place-start").value),en=toMinutes($("place-end").value);
  $("place-duration").textContent=Number.isFinite(st)&&Number.isFinite(en)&&en>st?`時間：${fmtMinutes(en-st)}`:"終了時刻は開始時刻より後に設定してください";
}
async function savePlace(e){
  e.preventDefault();
  const kind=document.querySelector('input[name="place-kind"]:checked')?.value;
  if(!kind){showToast("場所・移動の種類を選択してください。",true);return;}
  const payload={day:$("place-day").value,start:$("place-start").value,end:$("place-end").value,kind,memo:$("place-memo").value.trim()};
  if(toMinutes(payload.end)<=toMinutes(payload.start)){showToast("終了時刻は開始時刻より後にしてください。",true);return;}
  const id=state.placeEditingId;const b=$("submit-place");b.disabled=true;
  try{
    const result=await request(id?`/api/place-logs/${id}`:"/api/place-logs",id?"PATCH":"POST",payload);
    $("place-dialog").close();
    state.selectedDate=payload.day;state.day=result.day;
    if(!state.showPlaces)setShowPlaces(true);else renderDay(true);
    showToast(`場所・移動の記録を${id?"更新":"保存"}しました。`);
  }catch(err){showToast(formatApiError(err),true);}finally{b.disabled=false;}
}
async function deletePlace(){
  if(!state.placeEditingId||!confirm("この場所・移動の記録を削除しますか？"))return;
  try{const data=await request(`/api/place-logs/${state.placeEditingId}`,"DELETE",{});$("place-dialog").close();state.day=data;renderDay(true);showToast("場所・移動の記録を削除しました。");}
  catch(e){showToast(formatApiError(e),true);}
}

async function switchWeek(start){state.weekStart=start;await loadSummary();}
async function loadSummary(){
  const start=state.weekStart,end=addDays(start,6);
  try{state.summary=await api(`/api/summary?start=${start}&end=${end}`);renderSummary();}
  catch(e){showToast(formatApiError(e),true);}
}
function renderSummary(){
  const data=state.summary;if(!data)return;
  $("week-label").textContent=`${fmtDay(data.start,true)}（月）〜 ${fmtDay(data.end,true)}（日）`;
  $("summary-research").textContent=fmtMinutes(data.total.research);
  $("summary-philosophy").textContent=fmtMinutes(data.total.philosophy);
  $("summary-llm").textContent=fmtMinutes(data.total.llm);
  const diff=data.total.research-data.total.research_goal;
  $("summary-research-diff").textContent=`モデル ${fmtMinutes(data.total.research_goal)} / ${diff>=0?"+":"−"}${fmtMinutes(Math.abs(diff))}`;
  $("summary-research-diff").classList.toggle("good",diff>=0);$("summary-research-diff").classList.toggle("warn",diff<0);
  const maximum=Math.max(60,...data.days.map(d=>Math.max(d.research_goal,d.research+d.philosophy+d.llm)))*1.08;
  $("summary-chart").innerHTML=data.days.map((d,i)=>{
    const sum=d.research+d.philosophy+d.llm;
    const parts=["research","philosophy","llm"].map(k=>`<span style="width:${(d[k]/maximum*100).toFixed(3)}%;background:var(--${k})" title="${escapeHTML(CATEGORIES[k])} ${fmtMinutes(d[k])}"></span>`).join("");
    return `<div class="day-chart-row"><div class="chart-day">${WEEKDAYS[d.weekday]}曜日<small>${fmtDay(d.day,true)}</small></div><div class="chart-bar-bg"><div class="chart-bar-stack">${parts}</div><div class="chart-target" style="left:${(d.research_goal/maximum*100).toFixed(3)}%" title="研究のモデル：${fmtMinutes(d.research_goal)}"></div></div><div class="chart-total">${fmtMinutes(sum)}</div></div>`;
  }).join("")+`<div class="summary-legend"><span><i class="category-dot research"></i> 研究</span><span><i class="category-dot philosophy"></i> 哲学</span><span><i class="category-dot llm"></i> LLM</span><span>┆ 研究の予定時間</span></div>`;
  const tp=data.total.places||{};
  const placeParts=Object.keys(PLACES).filter(k=>tp[k]).map(k=>`<span><i class="category-dot place-${k}"></i>${PLACES[k]} ${fmtMinutes(tp[k])}</span>`);
  $("summary-places").innerHTML=placeParts.length?`<b>場所・移動の実績</b>${placeParts.join("")}<small>活動時間には加算していません</small>`:"";
}

function renderSettings(){
  if(state.day){$("timeline-start-setting").value=state.day.preferences.timeline_start;$("timeline-end-setting").value=state.day.preferences.timeline_end;}
  renderThemes();
}
async function saveDisplaySettings(e){
  e.preventDefault();
  const start=$("timeline-start-setting").value,end=$("timeline-end-setting").value;
  if(toMinutes(end)-toMinutes(start)<60){showToast("1時間以上の表示範囲を指定してください。",true);return;}
  try{
    const p=await request("/api/preferences","PATCH",{timeline_start:start,timeline_end:end});
    if(state.day){state.day.preferences=p;renderDay();}
    showToast("時間軸の表示範囲を保存しました。");
  }catch(err){showToast(formatApiError(err),true);}
}
function renderThemes(){
  const parent=$("theme-list");if(!parent)return;
  const themes=state.themes.filter(x=>x.category===state.activeCategory).sort((a,b)=>a.archived-b.archived||a.name.localeCompare(b.name,"ja"));
  parent.replaceChildren();
  if(!themes.length)parent.innerHTML='<div class="empty-state">まだテーマがありません。</div>';
  for(const item of themes){
    const row=document.createElement("div");row.className="theme-item"+(item.archived?" archived":"");
    const name=document.createElement("span");name.className="theme-item-name";name.textContent=item.name+(item.archived?"（アーカイブ）":"");row.append(name);
    const edit=document.createElement("button");edit.type="button";edit.textContent="名前変更";
    edit.addEventListener("click",async()=>{
      const newName=prompt("テーマ名を変更します",item.name);
      if(newName===null||!newName.trim()||newName.trim()===item.name)return;
      try{state.themes=await request(`/api/themes/${item.id}`,"PATCH",{name:newName.trim()});renderThemes();refreshThemeSuggestions();await loadDay(true);showToast("テーマ名を更新しました。");}
      catch(e){showToast(formatApiError(e),true);}
    });
    row.append(edit);
    const arch=document.createElement("button");arch.type="button";arch.textContent=item.archived?"復元":"アーカイブ";
    arch.addEventListener("click",async()=>{
      if(!item.archived&&!confirm(`「${item.name}」を候補一覧から隠しますか？ 過去の記録は保持されます。`))return;
      try{state.themes=await request(`/api/themes/${item.id}`,"PATCH",{archived:!item.archived});renderThemes();refreshThemeSuggestions();showToast(item.archived?"テーマを復元しました。":"テーマをアーカイブしました。");}
      catch(e){showToast(formatApiError(e),true);}
    });row.append(arch);parent.append(row);
  }
}
async function createTheme(e){
  e.preventDefault();
  const field=$("new-theme");const name=field.value.trim();if(!name)return;
  try{state.themes=await request("/api/themes","POST",{category:state.activeCategory,name});field.value="";renderThemes();refreshThemeSuggestions();showToast("テーマを追加しました。");}
  catch(e){showToast(formatApiError(e),true);}
}

document.addEventListener("DOMContentLoaded",init);
