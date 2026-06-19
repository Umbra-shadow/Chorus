// Chorus — two-column orchestra client (chat left, two-stage research right).
const $ = s => document.querySelector(s);
const KEY = "chorus_qwen_key";
let runId = null, es = null, sessionId = null, runStatus = "idle";
let budgetTotal = 100, qUsed = 0;
const newSessionId = () => "s_" + Math.random().toString(36).slice(2, 11);

// ── API key (per-tab) ───────────────────────────────────────────────
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

// ── collapsible top-level sections ──────────────────────────────────
document.querySelectorAll(".c-toggle").forEach(btn => {
  btn.addEventListener("click", () => {
    const body = $("#" + btn.dataset.target);
    const open = body.classList.toggle("open");
    btn.querySelector(".c-arrow").textContent = open ? "▼" : "▶";
  });
});

const esc = s => (s||"").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

// ── progress + budget ───────────────────────────────────────────────
function progress(msg, spin = true){
  const p = $("#progress");
  p.style.display = "";
  p.innerHTML = (spin ? '<span class="spin"></span>' : "") + esc(msg);
}
function stopProgress(msg){ $("#progress").innerHTML = esc(msg || ""); }
function setBudget(used, total){
  qUsed = used; if (total) budgetTotal = total;
  $("#budgetbar").style.display = "";
  $("#budgetfill").style.width = Math.min(100, Math.round((qUsed / budgetTotal) * 100)) + "%";
  $("#budget").textContent = `questions used: ${qUsed} / ${budgetTotal}`;
}

// ── orchestra memory panel (mirrors the Kioku Researcher) ───────────
let memTimer = null;
async function refreshMemory(){
  if (!runId) return;
  try{
    const j = await (await fetch(`/api/chorus/${runId}/memory`)).json();
    const total = j.total || 0;
    const c = $("#memCount");
    c.textContent = total + (total === 1 ? " engram" : " engrams");
    c.classList.add("writing"); setTimeout(() => c.classList.remove("writing"), 1000);
    const feed = $("#memFeed");
    if (!(j.engrams||[]).length){ feed.innerHTML = '<div class="mem-empty">No memories yet.</div>'; return; }
    feed.innerHTML = j.engrams.map((e,i) =>
      `<div class="mem-row"><div class="mem-dot${i===0?" new":""}"></div>` +
      `<div class="mem-text" title="${esc(e.meaning||e.message||"")}">${esc(e.meaning||e.message||"(processing…)")}</div></div>`
    ).join("");
    feed.scrollTop = 0;
  } catch(_){}
}

const STAGES = ["stage1","researching","coordinating","synthesizing","done"];
function setStage(active){
  $("#stagebar").style.display = "";
  const i = STAGES.indexOf(active);
  STAGES.forEach((s, j) => {
    const el = document.querySelector(`.st[data-st="${s}"]`);
    if (!el) return;
    el.className = "st" + (s === active ? " on" : (j < i ? " done" : ""));
  });
}
function setStatus(s){
  runStatus = s;
  const c = $("#statuschip");
  c.className = "status-chip";
  if (["stage1","analyzing","researching","coordinating","synthesizing"].includes(s)) c.classList.add("live");
  else if (s === "done") c.classList.add("done");
  c.textContent = s === "researching" ? "orchestrating…" : s;
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

// ── Stage 1 — expandable sub-questions ──────────────────────────────
const s1q = {};
function ensureSubq(id, question){
  $("#s1Section").style.display = "";
  if (s1q[id]){ if (question) s1q[id].q.textContent = question; return s1q[id]; }
  const row = document.createElement("div"); row.className = "subq";
  row.innerHTML =
    `<div class="sqhead"><span class="sqcaret">▸</span><span class="qn">Q${id}</span>` +
    `<span class="qt"></span><span class="sqstate"><span class="spin"></span></span></div>` +
    `<div class="sqbody">researching…</div>`;
  $("#s1Questions").appendChild(row);
  const head = row.querySelector(".sqhead"), body = row.querySelector(".sqbody"),
        caret = row.querySelector(".sqcaret");
  head.addEventListener("click", () => {
    const open = body.classList.toggle("open"); caret.classList.toggle("open", open);
  });
  const rec = { row, q: row.querySelector(".qt"), state: row.querySelector(".sqstate"),
                qn: row.querySelector(".qn"), body, caret };
  if (question) rec.q.textContent = question;
  s1q[id] = rec; return rec;
}
function fillSubq(id, d){
  const rec = ensureSubq(id, d.question);
  rec.qn.classList.toggle("g", !!d.grounded);
  rec.state.className = "sqstate" + (d.grounded ? " g" : "");
  rec.state.textContent = d.grounded ? `✓ ${d.sources||0} src` : "model-only";
  rec.body.textContent = d.answer || "(no answer)";
}

// ── Stage 2 — collapsible agent cards ───────────────────────────────
function agentCard(a){
  return `<div class="agent" id="ag-${a.domain}">
    <div class="ahead"><span class="adom">${esc(a.domain)}</span><span class="arole">${esc(a.role)}</span>
      <span class="astate" id="ast-${a.domain}"><span class="spin"></span></span>
      <span class="acaret">▸</span></div>
    <div class="abody"><div class="asum" id="asum-${a.domain}">${esc(a.why||"")}</div>
      <ul id="acl-${a.domain}"></ul></div></div>`;
}
$("#agents").addEventListener("click", e => {
  const head = e.target.closest(".ahead"); if (!head) return;
  const card = head.closest(".agent");
  const open = card.querySelector(".abody").classList.toggle("open");
  card.querySelector(".acaret").classList.toggle("open", open);
});

function renderReview(r){
  $("#revSection").style.display = "";
  let h = "";
  if (r.agreements?.length) h += `<div class="blk"><b>Agreements:</b><ul>${r.agreements.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>`;
  if (r.conflicts?.length) h += `<div class="blk"><b>Conflicts:</b><ul>${r.conflicts.map(c=>`<li>${esc((c.between||[]).join(" ↔ "))}: ${esc(c.issue||"")}</li>`).join("")}</ul></div>`;
  if (r.gaps?.length) h += `<div class="blk"><b>Gaps:</b><ul>${r.gaps.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>`;
  if (r.followups?.length) h += `<div class="blk"><b>Follow-ups:</b><ul>${r.followups.map(f=>`<li>${esc(f.domain)}: ${esc(f.question)}</li>`).join("")}</ul></div>`;
  $("#revBody").innerHTML = h || "<div class='blk'>Team aligned — proceeding to synthesis.</div>";
}

// ── run lifecycle ───────────────────────────────────────────────────
async function start(){
  const question = $("#question").value.trim();
  if (question.length < 4){ $("#question").focus(); return; }
  const questions = Math.max(2, Math.min(50, parseInt($("#numq").value) || 10));
  const budget = Math.max(2, Math.min(10000, parseInt($("#budget").value) || 100));
  $("#go").disabled = true; $("#err").textContent = ""; qUsed = 0; budgetTotal = budget;
  for (const k in s1q) delete s1q[k];
  ["s1Section","s2Section","revSection","repSection"].forEach(i => $("#"+i).style.display = "none");
  $("#s1Questions").innerHTML = ""; $("#agents").innerHTML = "";
  $("#s1Conclusion").style.display = "none"; $("#hypo").style.display = "none";
  progress("starting…");
  try{
    const r = await fetch("/api/chorus/start", {
      method:"POST", headers:{ "Content-Type":"application/json", ...keyHeaders() },
      body: JSON.stringify({ question, questions, budget })
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const j = await r.json(); runId = j.run_id; budgetTotal = j.question_budget || budget;
    sessionId = newSessionId();
    $("#ask").disabled = false; $("#askBtn").disabled = false;
    $("#chatlog").innerHTML = ""; $("#chatsub").textContent = "ask anything — even while it works";
    $("#memPanel").style.display = ""; $("#memFeed").innerHTML = '<div class="mem-empty">writing memories…</div>';
    setBudget(0, budgetTotal);
    if (memTimer) clearInterval(memTimer);
    memTimer = setInterval(refreshMemory, 2500);
    listen();
  } catch(e){ $("#err").textContent = "Could not start: " + e.message; $("#go").disabled = false; stopProgress(""); }
}

function listen(){
  if (es) es.close();
  es = new EventSource(`/api/chorus/${runId}/stream`);
  es.onmessage = ev => {
    const e = JSON.parse(ev.data);

    if (e.stage === "stage1_begin"){ setStage("stage1"); setStatus("stage1"); progress("Stage 1 — expanding the question into research…"); }
    else if (e.stage === "analyzing"){ setStatus("analyzing"); progress("Casting the domain team from the Stage 1 conclusion…"); }
    else if (e.stage === "researching"){ setStage("researching"); setStatus("researching"); }
    else if (e.stage === "coordinating"){ setStage("coordinating"); setStatus("coordinating"); progress(`Coordinator reconciling the team (round ${e.round||1})…`); }
    else if (e.stage === "synthesizing"){ setStage("synthesizing"); setStatus("synthesizing"); progress("Writing the unified document…"); }

    // Stage 1 — first research
    if (e.stage === "stage1"){
      if (e.sub === "expanded" && Array.isArray(e.questions)){
        e.questions.forEach((q,i)=>ensureSubq(i+1, q));
        progress(`Stage 1 — ${e.questions.length} questions, researching one by one…`);
      } else if (e.sub === "studying"){ ensureSubq(e.id, e.question); progress(`Stage 1 — answering Q${e.id}…`); }
      else if (e.sub === "studied"){ fillSubq(e.id, e); setBudget(qUsed + 1); progress(`Stage 1 — answered ${qUsed}/${$("#numq").value}…`); refreshMemory(); }
    } else if (e.stage === "stage1_done"){ $("#s1Badge").textContent = e.questions + " q"; progress("Stage 1 conclusion ready — going deeper."); }

    // Stage 2 — orchestra
    else if (e.stage === "cast"){
      $("#s2Section").style.display = "";
      $("#hypo").style.display = ""; $("#hypo").textContent = e.hypothesis;
      $("#agents").innerHTML = (e.agents||[]).map(agentCard).join("");
      $("#s2Badge").textContent = (e.agents||[]).length;
      if (e.question_budget) budgetTotal = e.question_budget;
      progress(`${(e.agents||[]).length} domain agents researching in parallel…`);
    }
    else if (e.stage === "agent_start"){ $(`#ag-${e.domain}`)?.classList.add("researching"); progress(`${e.domain} researching its domain…`); }
    else if (e.stage === "agent_capped"){ const st=$(`#ast-${e.domain}`); if(st){st.className="astate";st.textContent="capped";} }
    else if (e.stage === "agent_done"){
      const card = $(`#ag-${e.domain}`); if (card){ card.classList.remove("researching"); card.classList.add("done"); }
      const st = $(`#ast-${e.domain}`); if (st){ st.className="astate g"; st.textContent = `✓ ${e.grounded||0} grounded`; }
      const sum = $(`#asum-${e.domain}`); if (sum && e.summary) sum.textContent = e.summary;
      setBudget(qUsed + (e.grounded || 1)); refreshMemory();
    }
    else if (e.stage === "review"){ renderReview(e); }
    else if (e.stage === "followup"){ progress(`Follow-up for ${e.domain}…`); }
    else if (e.stage === "budget_exhausted"){ progress(`question budget spent (${e.used}/${e.total}) — stopping research`, false); setBudget(e.used, e.total); }
    else if (e.stage === "done"){
      es.close(); if (memTimer) clearInterval(memTimer); $("#go").disabled = false;
      if (e.question_budget) setBudget(e.questions_used, e.question_budget);
      stopProgress(`Done — ${e.grounded_total||0} grounded findings · ${e.questions_used}/${e.question_budget} questions used`);
      loadReport(); refreshMemory();
    }
    else if (e.stage === "error"){ es.close(); if (memTimer) clearInterval(memTimer); $("#go").disabled = false; $("#err").textContent = "Error: " + (e.error||"unknown"); stopProgress(""); }
  };
}

async function loadReport(){
  const run = await (await fetch(`/api/chorus/${runId}`)).json();
  // Stage 1 conclusion + full answers (in case an event was missed)
  (run.stage1?.findings || []).forEach(f => fillSubq(f.id, { question: f.question, grounded: f.grounded, sources: (f.sources||[]).length, answer: f.answer }));
  const concl = (run.stage1||{}).report || "";
  if (concl){ $("#s1Conclusion").style.display = ""; $("#s1Conclusion").innerHTML = "<b>Conclusion:</b> " + esc(concl.slice(0,1400)) + (concl.length>1400?"…":""); }
  (run.reports||[]).forEach(r => {
    const ul = $(`#acl-${r.domain}`);
    if (ul) ul.innerHTML = (r.claims||[]).slice(0,6).map(c=>`<li>${esc(c)}</li>`).join("");
  });
  $("#repSection").style.display = "";
  $("#reportBody").innerHTML = md(run.report);
}

// ── chat (talk to it) ───────────────────────────────────────────────
function addMsg(cls, html){
  $("#chatEmpty")?.remove();
  const d = document.createElement("div"); d.className = "msg " + cls;
  d.innerHTML = `<div class="b">${html}</div>`;
  $("#chatlog").appendChild(d); $("#chatlog").scrollTop = $("#chatlog").scrollHeight;
  return d;
}
async function ask(){
  const q = $("#ask").value.trim(); if (!q || !runId) return;
  $("#ask").value = ""; addMsg("you", esc(q));
  const ai = addMsg("ai", '<span class="spin"></span> thinking…');
  try{
    const r = await fetch(`/api/chorus/${runId}/ask`, {
      method:"POST", headers:{ "Content-Type":"application/json", ...keyHeaders() },
      body: JSON.stringify({ question: q, session_id: sessionId })
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const j = await r.json(); sessionId = j.session_id;
    ai.querySelector(".b").textContent = j.answer;
    const m = document.createElement("div"); m.className = "recall";
    const parts = [];
    if (j.recalled?.length) parts.push(j.recalled.length + " memories");
    if (j.has_context) parts.push("investigation loaded");
    if (j.history_turns > 0) parts.push(j.history_turns + " prior turns");
    if (j.run_status !== "done") parts.push("still working");
    m.textContent = "↺ " + (parts.join(" · ") || "context ready");
    ai.appendChild(m);
  } catch(e){ ai.querySelector(".b").textContent = "Error: " + e.message; }
}

$("#dl").onclick = () => { if (runId) window.location = `/api/chorus/${runId}/pdf`; };
$("#go").onclick = start;
$("#question").addEventListener("keydown", e => { if (e.key === "Enter") start(); });
$("#askBtn").onclick = ask;
$("#ask").addEventListener("keydown", e => { if (e.key === "Enter") ask(); });
