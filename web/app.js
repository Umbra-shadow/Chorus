// Chorus — multi-agent orchestration client.
const $ = s => document.querySelector(s);
const KEY = "chorus_qwen_key";
let runId = null, es = null;

const getKey = () => sessionStorage.getItem(KEY) || "";
const keyHeaders = () => { const k = getKey(); return k ? { "X-Qwen-Key": k } : {}; };
function reflectKey(){
  const k = getKey();
  $("#keyState").textContent = k ? ("set ✓ " + k.slice(0,3) + "…") : "no key";
  $("#keyState").className = "keystate" + (k ? " set" : "");
  if (k && !$("#key").value) $("#key").value = k;
}
function saveKey(){
  const v = $("#key").value.trim();
  if (v) sessionStorage.setItem(KEY, v); else sessionStorage.removeItem(KEY);
  reflectKey();
}
$("#saveKey").onclick = saveKey;
$("#key").addEventListener("keydown", e => { if (e.key === "Enter") saveKey(); });
reflectKey();

$("#examples").addEventListener("click", e => {
  if (e.target.classList.contains("chip")){ $("#question").value = e.target.textContent; $("#question").focus(); }
});

const esc = s => (s||"").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const STAGES = ["analyzing","researching","coordinating","synthesizing","done"];
function setStage(active){
  $("#stagebar").style.display = "";
  const i = STAGES.indexOf(active);
  STAGES.forEach((s, j) => {
    const el = document.querySelector(`.st[data-st="${s}"]`);
    if (!el) return;
    el.className = "st" + (s === active ? " on" : (j < i ? " done" : ""));
  });
}

function md(src){
  const lines = (src||"").split("\n"); let html = "", inList = false;
  const close = () => { if (inList){ html += "</ul>"; inList = false; } };
  const inl = t => esc(t).replace(/\*\*(.+?)\*\*/g,"<b>$1</b>").replace(/`(.+?)`/g,"<code>$1</code>");
  for (let ln of lines){ const t = ln.replace(/\s+$/,"");
    if (/^#\s+/.test(t)){ close(); html += "<h1>"+inl(t.replace(/^#\s+/,""))+"</h1>"; }
    else if (/^##\s+/.test(t)){ close(); html += "<h2>"+inl(t.replace(/^##\s+/,""))+"</h2>"; }
    else if (/^###\s+/.test(t)){ close(); html += "<h3>"+inl(t.replace(/^###\s+/,""))+"</h3>"; }
    else if (/^\s*[-*]\s+/.test(t)){ if(!inList){html+="<ul>";inList=true;} html += "<li>"+inl(t.replace(/^\s*[-*]\s+/,""))+"</li>"; }
    else if (t.trim()===""){ close(); }
    else { close(); html += "<p>"+inl(t)+"</p>"; } }
  close(); return html;
}

function agentCard(a){
  return `<div class="agent" id="ag-${a.domain}">
    <div class="ahead"><span class="adom">${esc(a.domain)}</span><span class="arole">${esc(a.role)}</span>
      <span class="astate" id="ast-${a.domain}"><span class="spin"></span></span></div>
    <div class="asum" id="asum-${a.domain}">${esc(a.why||"")}</div>
    <ul id="acl-${a.domain}"></ul></div>`;
}

function renderReview(r){
  $("#review").style.display = "";
  let h = "";
  if (r.agreements?.length) h += `<div class="blk"><b>Agreements:</b><ul>${r.agreements.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>`;
  if (r.conflicts?.length) h += `<div class="blk"><b>Conflicts:</b><ul>${r.conflicts.map(c=>`<li>${esc((c.between||[]).join(" ↔ "))}: ${esc(c.issue||"")}</li>`).join("")}</ul></div>`;
  if (r.gaps?.length) h += `<div class="blk"><b>Gaps:</b><ul>${r.gaps.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>`;
  if (r.followups?.length) h += `<div class="blk"><b>Follow-ups:</b><ul>${r.followups.map(f=>`<li>${esc(f.domain)}: ${esc(f.question)}</li>`).join("")}</ul></div>`;
  $("#reviewBody").innerHTML = h || "<div class='blk'>Team aligned — proceeding to synthesis.</div>";
}

async function start(){
  const question = $("#question").value.trim();
  if (question.length < 4){ $("#question").focus(); return; }
  // Key is optional: a UI key (X-Qwen-Key) overrides the server's default brain,
  // but the server may already have one (Qwen in .env, or a Gemini .env.local).
  $("#go").disabled = true; $("#err").textContent = "";
  $("#agents").innerHTML = ""; $("#hypo").style.display="none";
  $("#review").style.display="none"; $("#report").style.display="none";
  try{
    const r = await fetch("/api/chorus/start", {
      method:"POST", headers:{ "Content-Type":"application/json", ...keyHeaders() },
      body: JSON.stringify({ question })
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    runId = (await r.json()).run_id;
    listen();
  } catch(e){ $("#err").textContent = "Could not start: " + e.message; $("#go").disabled = false; }
}

function listen(){
  if (es) es.close();
  es = new EventSource(`/api/chorus/${runId}/stream`);
  es.onmessage = ev => {
    const e = JSON.parse(ev.data);
    if (["analyzing","researching","coordinating","synthesizing","done"].includes(e.stage)) setStage(e.stage);
    if (e.stage === "cast"){
      $("#hypo").style.display=""; $("#hypoText").textContent = e.hypothesis;
      $("#agents").innerHTML = (e.agents||[]).map(agentCard).join("");
    }
    else if (e.stage === "agent_start"){ $(`#ag-${e.domain}`)?.classList.add("researching"); }
    else if (e.stage === "agent_done"){
      const card = $(`#ag-${e.domain}`); if (card){ card.classList.remove("researching"); card.classList.add("done"); }
      const st = $(`#ast-${e.domain}`); if (st){ st.className="astate g"; st.textContent = `✓ ${e.grounded||0} grounded`; }
      const sum = $(`#asum-${e.domain}`); if (sum && e.summary) sum.textContent = e.summary;
    }
    else if (e.stage === "review"){ renderReview(e); }
    else if (e.stage === "followup"){ const st=$(`#ast-${e.domain}`); if(st) st.innerHTML=`<span class="spin"></span> follow-up`; }
    else if (e.stage === "done"){
      es.close(); $("#go").disabled = false; loadReport();
    }
    else if (e.stage === "error"){ es.close(); $("#go").disabled = false; $("#err").textContent = "Error: " + (e.error||"unknown"); }
  };
}

async function loadReport(){
  const run = await (await fetch(`/api/chorus/${runId}`)).json();
  // fill in claims per agent from final state
  (run.reports||[]).forEach(r => {
    const ul = $(`#acl-${r.domain}`);
    if (ul) ul.innerHTML = (r.claims||[]).slice(0,4).map(c=>`<li>${esc(c)}</li>`).join("");
  });
  $("#report").style.display = "";
  $("#reportBody").innerHTML = md(run.report);
}

$("#dl").onclick = () => { if (runId) window.location = `/api/chorus/${runId}/pdf`; };
$("#go").onclick = start;
$("#question").addEventListener("keydown", e => { if (e.key === "Enter") start(); });
