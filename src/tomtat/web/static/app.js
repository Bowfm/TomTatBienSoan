"use strict";

let FILES = [];
let CURRENT_STEM = null;
let CURRENT_STATE = null;
let CURRENT_UNITS = null;
let SELECTIONS = {}; // {doc_n: Set(id)}
let DONE_SELECTED = new Set(); // tập các stem được tick trong danh sách "Đã xong"
let RUNNING_STEMS = new Set(); // tập các stem đang được AI xử lý
let STOP_BATCH_REQUESTED = false;
let CUSTOM_EDITED = false;
let checkTimer = null;

const $ = (id) => document.getElementById(id);

function showToast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.style.background = isErr ? "#b91c1c" : "#0f172a";
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), 3200);
}

async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok && !data.need_confirm) {
    throw new Error(data.error || `Lỗi HTTP ${res.status}`);
  }
  return data;
}

async function loadFileList(keepCurrent = true) {
  const data = await api("/api/files");
  FILES = data.files || [];
  const badge = $("mode-badge");
  if (data.mock || !data.has_api_key) {
    badge.textContent = data.has_api_key ? "Chế độ giả lập (--mock)" : "Chưa có GEMINI_API_KEY (dùng --mock)";
  } else {
    const fb = (data.fallback_models || []).slice(0, 2).join(" → ");
    const learned = data.learned_count ? ` · 🧠 Đã học: ${data.learned_count} bài duyệt` : "";
    badge.textContent = `Model chính: ${data.primary_model || "gemini-4-hydrogen"}${fb ? ` (dự phòng: ${fb}...)` : ""}${learned}`;
    badge.style.background = "#1d4ed8";
    badge.style.color = "#ffffff";
  }
  renderFileList();
  if (!keepCurrent || !CURRENT_STEM) {
    if (FILES.length > 0) {
      await selectFile(FILES[0].file.replace(/\.docx$/i, ""));
    }
  }
}

function isApprovedStatus(st) {
  return st === "da_duyet" || st === "da_xuat";
}

function pendingFiles() {
  const f = $("filter-status").value;
  return FILES.filter((r) => !isApprovedStatus(r.status) && (!f || r.status === f));
}

function approvedFiles() {
  return FILES.filter((r) => isApprovedStatus(r.status));
}

function filteredFiles() {
  // Dùng cho phím tắt Alt+↑/↓: duyệt qua cột Chờ duyệt trước, rồi tới Đã duyệt
  return [...pendingFiles(), ...approvedFiles()];
}

function setText(id, val) {
  const el = $(id);
  if (el) el.textContent = val;
}

function renderFileList() {
  const allPending = FILES.filter((r) => !isApprovedStatus(r.status));
  const allApproved = approvedFiles();

  // Cập nhật các con số thống kê (an toàn kể cả khi HTML cũ chưa reload)
  setText("cnt-total", FILES.length);
  setText("cnt-pending", allPending.length);
  setText("cnt-approved", allApproved.length);
  setText("badge-pending", allPending.length);
  setText("done-count", allApproved.length);
  if ($("btn-clear-pending")) $("btn-clear-pending").disabled = allPending.length === 0;
  if ($("btn-clear-approved")) $("btn-clear-approved").disabled = allApproved.length === 0;

  const ul = $("file-list");
  if (!ul) return;
  ul.innerHTML = "";
  const list = pendingFiles();
  if (!list.length) {
    ul.innerHTML = `<li class="muted" style="padding:10px 12px">Không có file nào trong mục này.</li>`;
  } else {
    for (const r of list) {
      const stem = r.file.replace(/\.docx$/i, "");
      const isRunning = RUNNING_STEMS.has(stem);
      const li = document.createElement("li");
      li.className = "file-item" + (stem === CURRENT_STEM ? " active" : "");
      li.dataset.stem = stem;
      const pillClass = isRunning ? "st-running" : `st-${r.status}`;
      const pillText = isRunning ? "⏳ Đang chạy AI..." : r.status_vi;
      const suspBadge = r.is_suspicious
        ? `<span class="badge-suspicious" title="${escapeHtml((r.suspicious_reasons || []).join('; ') || 'File có dấu hiệu lỗi cần hủy')}">⚠️ Nghi hủy</span>`
        : "";
      li.innerHTML = `
        <div class="file-item-top">
          <span style="display:inline-flex;align-items:center;gap:6px">${escapeHtml(r.file)} ${suspBadge}</span>
          <span style="display:flex;align-items:center;gap:4px">
            <span class="status-pill ${pillClass}">${escapeHtml(pillText)}</span>
            <button class="btn-del-item" data-del-stem="${stem}" title="Xóa file ${escapeHtml(r.file)}">✕</button>
          </span>
        </div>
        <div class="file-item-sub">
          <span>${escapeHtml(r.label || "(chưa gán nhãn)")}${r.model_used ? ` <strong style="color:#1d4ed8">[${escapeHtml(r.model_used)}]</strong>` : ""}</span>
          <span>${r.n_errors ? `Lỗi: ${r.n_errors}` : `CB: ${r.n_warnings || 0}`}</span>
        </div>
      `;
      li.querySelector(".btn-del-item").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteFiles("selected", [stem], `Bạn có chắc muốn xóa file '${r.file}'?`);
      });
      li.addEventListener("click", () => selectFile(stem));
      ul.appendChild(li);
    }
  }
  renderDoneList();
}

function todayLabel() {
  const now = new Date();
  const dd = String(now.getDate()).padStart(2, "0");
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  return { slash: `${dd}/${mm}`, file: `${dd}-${mm}.zip` };
}

function renderDoneList() {
  const doneUl = $("done-list");
  if (!doneUl) return;
  const doneFiles = approvedFiles();

  // Tự động tick sẵn các file mới được chuyển sang cột Đã duyệt
  if (!DONE_SELECTED._initializedStems) DONE_SELECTED._initializedStems = new Set();
  for (const r of doneFiles) {
    const stem = r.file.replace(/\.docx$/i, "");
    if (!DONE_SELECTED._initializedStems.has(stem)) {
      DONE_SELECTED.add(stem);
      DONE_SELECTED._initializedStems.add(stem);
    }
  }

  doneUl.innerHTML = "";
  if (!doneFiles.length) {
    doneUl.innerHTML = `<li class="muted" style="padding:10px 12px">Chưa có file nào đã duyệt.</li>`;
  } else {
    for (const r of doneFiles) {
      const stem = r.file.replace(/\.docx$/i, "");
      const checked = DONE_SELECTED.has(stem);
      const suspBadge = r.is_suspicious
        ? `<span class="badge-suspicious" title="${escapeHtml((r.suspicious_reasons || []).join('; ') || 'File có dấu hiệu lỗi cần hủy')}">⚠️ Nghi hủy</span>`
        : "";
      const li = document.createElement("li");
      li.className = "file-item" + (stem === CURRENT_STEM ? " active" : "");
      li.dataset.stem = stem;
      li.innerHTML = `
        <div class="file-item-top">
          <label class="chk-inline" title="Tick để đưa vào file ZIP" onclick="event.stopPropagation()">
            <input type="checkbox" data-done-stem="${stem}" ${checked ? "checked" : ""}>
            <strong>${escapeHtml(r.file)}</strong>
            ${suspBadge}
          </label>
          <span style="display:flex;align-items:center;gap:4px">
            <span class="status-pill st-${r.status}">Đã duyệt</span>
            <button class="btn-del-item" data-del-stem="${stem}" title="Xóa file ${escapeHtml(r.file)}">✕</button>
          </span>
        </div>
        <div class="done-item-actions">
          <span class="muted">${escapeHtml(r.label || "")}</span>
          <a href="/api/download-file/${encodeURIComponent(stem)}" onclick="event.stopPropagation()" title="Tải lẻ file .docx này">⬇ Tải lẻ</a>
        </div>
      `;
      li.querySelector(".btn-del-item").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteFiles("selected", [stem], `Bạn có chắc muốn xóa file '${r.file}'?`);
      });
      const cb = li.querySelector("input");
      cb.addEventListener("change", (e) => {
        e.stopPropagation();
        if (cb.checked) DONE_SELECTED.add(stem);
        else DONE_SELECTED.delete(stem);
        updateZipButtonState(doneFiles.length);
      });
      li.addEventListener("click", () => selectFile(stem));
      doneUl.appendChild(li);
    }
  }
  updateZipButtonState(doneFiles.length);
}

function updateZipButtonState(totalDone) {
  const btn = $("btn-download-zip");
  if (!btn) return;
  const t = todayLabel();
  const count = Array.from(DONE_SELECTED).filter((s) =>
    FILES.some((r) => (r.status === "da_xuat" || r.status === "da_duyet") && r.file.replace(/\.docx$/i, "") === s)
  ).length;
  btn.textContent = `⬇ Tải ZIP ngày ${t.slash} (${count} file)`;
  btn.disabled = count === 0;
  const chkAll = $("chk-done-all");
  if (chkAll) chkAll.checked = totalDone > 0 && count === totalDone;
}

function downloadDoneZip() {
  const activeStems = FILES
    .filter((r) => r.status === "da_xuat" || r.status === "da_duyet")
    .map((r) => r.file.replace(/\.docx$/i, ""))
    .filter((s) => DONE_SELECTED.has(s));
  if (!activeStems.length) {
    showToast("Hãy tick chọn ít nhất 1 file đã xong để tải ZIP.", true);
    return;
  }
  const url = `/api/download-zip?stems=${encodeURIComponent(activeStems.join(","))}`;
  window.location.href = url;
}

async function selectFile(stem) {
  try {
    const data = await api(`/api/file/${encodeURIComponent(stem)}`);
    CURRENT_STEM = stem;
    CURRENT_STATE = data.state;
    CURRENT_UNITS = data.units;
    CUSTOM_EDITED = Boolean((CURRENT_STATE.extract || {}).custom_edited);
    initSelectionsFromState();
    renderFileList();
    renderEditor(data.versions || []);
  } catch (err) {
    showToast(err.message, true);
  }
}

function initSelectionsFromState() {
  SELECTIONS = {};
  if (!CURRENT_UNITS) return;
  for (const d of CURRENT_UNITS.docs || []) {
    SELECTIONS[d.n] = new Set();
  }
  const saved = (CURRENT_STATE.extract || {}).selections || {};
  let hasAny = false;
  for (const [k, arr] of Object.entries(saved)) {
    const dn = Number(k);
    if (!SELECTIONS[dn]) SELECTIONS[dn] = new Set();
    for (const id of arr || []) {
      SELECTIONS[dn].add(Number(id));
      hasAny = true;
    }
  }
  if (!hasAny && CURRENT_STATE.checks && CURRENT_STATE.checks.matched_units) {
    for (const u of CURRENT_STATE.checks.matched_units) {
      if (!SELECTIONS[u.doc]) SELECTIONS[u.doc] = new Set();
      SELECTIONS[u.doc].add(Number(u.id));
    }
  }
}

function renderEditor(versions) {
  $("empty-state").classList.add("hidden");
  $("editor-pane").classList.remove("hidden");

  const st = CURRENT_STATE;
  const u = CURRENT_UNITS;
  $("cur-filename").textContent = st.file;
  const nDocs = (u.docs || []).length;
  const mLabel = (st.label || {}).model_used;
  const mExt = (st.extract || {}).model_used;
  const mAbs = (st.abstract || {}).model_used;
  const modelParts = [];
  if (mLabel) modelParts.push(`Nhãn: ${mLabel}`);
  if (mExt) modelParts.push(`Trích: ${mExt}`);
  if (mAbs) modelParts.push(`Biên soạn: ${mAbs}`);
  const modelSummary = modelParts.length
    ? ` · 🤖 Model đã chạy: ${modelParts.join(" | ")}`
    : st.model_used
      ? ` · 🤖 Model: ${st.model_used}`
      : "";
  $("cur-meta").textContent = `${nDocs > 1 ? "Đa văn bản" : "Đơn văn bản"} (${nDocs} văn bản, ${u.budget.total_words} từ gốc)${modelSummary}`;

  const pill = $("cur-status");
  pill.textContent = st.status_vi;
  pill.className = `status-pill st-${st.status}`;
  syncRerunButtonsState();

  const flags = [...(st.flags || [])];
  if (st.stale_steps && st.stale_steps.length) {
    flags.push(`Các bước cần chạy lại/cập nhật sau khi sửa bước trước: ${st.stale_steps.join(", ")}`);
  }
  const fbox = $("flags-box");
  if (flags.length) {
    fbox.classList.remove("hidden");
    fbox.innerHTML = flags.map((x) => `<div>• ${escapeHtml(x)}</div>`).join("");
  } else {
    fbox.classList.add("hidden");
  }

  $("query-box").textContent = u.query || "(Không có câu truy vấn)";
  $("budget-hint").textContent =
    `Ngân sách trích chọn: ${u.budget.extract_min}–${u.budget.extract_max} từ (nhắm ${u.budget.extract_target_low}–${u.budget.extract_target_high} từ, lý tưởng ~${u.budget.extract_target_mid} từ)`;

  $("ai-extract-ref").textContent = u.ai_extract || "(trống)";
  $("ai-abstract-ref").textContent = u.ai_abstract || "(trống)";

  // Hiển thị huy hiệu model riêng cho từng phần: 1. Nhãn, 2. Trích chọn, 3. Biên soạn
  const setStepBadge = (elId, modelName) => {
    const el = $(elId);
    if (!el) return;
    if (modelName) {
      el.textContent = `🤖 ${modelName}`;
      el.style.background = "#dbeafe";
      el.style.color = "#1e40af";
      el.classList.remove("hidden");
    } else {
      el.textContent = "";
      el.classList.add("hidden");
    }
  };
  setStepBadge("model-badge-label", mLabel);
  setStepBadge("model-badge-extract", mExt);
  setStepBadge("model-badge-abstract", mAbs);

  // Nhãn
  const lb = st.label || {};
  $("sel-style").value = lb.value || "";
  $("label-conf").textContent = lb.confidence ? `Độ chắc chắn: ${lb.confidence}` : "Chưa có độ chắc chắn";
  $("label-reason").textContent = lb.reason || "";
  const perDocDiv = $("label-per-doc");
  if (lb.per_doc && lb.per_doc.length > 1) {
    perDocDiv.innerHTML = lb.per_doc
      .map((p) => `<div>- Văn bản ${p.doc} (${p.words} từ): <strong>${escapeHtml(p.label)}</strong> — ${escapeHtml(p.reason || "")}</div>`)
      .join("");
  } else {
    perDocDiv.innerHTML = "";
  }

  // Trích chọn & Biên soạn
  $("txt-extract").value = (st.extract || {}).text || "";
  $("txt-abstract").value = (st.abstract || {}).text || "";

  renderDocsUnits();
  renderChecks(st.checks);
  renderJudge(st.judge);
  renderVersions(versions);
}

function renderDocsUnits() {
  const box = $("docs-container");
  box.innerHTML = "";
  for (const d of CURRENT_UNITS.docs || []) {
    const selSet = SELECTIONS[d.n] || new Set();
    const block = document.createElement("div");
    block.className = "doc-block";
    const head = document.createElement("div");
    head.className = "doc-header";
    head.innerHTML = `
      <span>Văn bản ${d.n} (${d.words} từ, ${d.units.length} câu/đề mục)</span>
      <span>
        <button class="btn btn-sm" data-doc-act="all" data-doc="${d.n}">Chọn hết</button>
        <button class="btn btn-sm" data-doc-act="none" data-doc="${d.n}">Bỏ hết</button>
      </span>
    `;
    block.appendChild(head);

    for (const unit of d.units) {
      const row = document.createElement("label");
      const checked = selSet.has(unit.id);
      row.className = "unit-item" + (checked ? " selected" : "");
      row.dataset.doc = d.n;
      row.dataset.uid = unit.id;
      row.innerHTML = `
        <input type="checkbox" ${checked ? "checked" : ""}>
        <span class="unit-id">[${unit.id}] (${unit.words}w)</span>
        <span class="unit-text">${escapeHtml(unit.text)}</span>
      `;
      const cb = row.querySelector("input");
      cb.addEventListener("change", () => onToggleUnit(d.n, unit.id, cb.checked));
      block.appendChild(row);
    }
    box.appendChild(block);
  }
  box.querySelectorAll("button[data-doc-act]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const dn = Number(btn.dataset.doc);
      const act = btn.dataset.docAct;
      const docObj = CURRENT_UNITS.docs.find((x) => x.n === dn);
      if (!docObj) return;
      SELECTIONS[dn] = act === "all" ? new Set(docObj.units.map((u) => u.id)) : new Set();
      assembleFromSelections();
    });
  });
}

async function onToggleUnit(docN, unitId, isChecked) {
  if (!SELECTIONS[docN]) SELECTIONS[docN] = new Set();
  if (isChecked) SELECTIONS[docN].add(unitId);
  else SELECTIONS[docN].delete(unitId);
  await assembleFromSelections();
}

function selectionsPayload() {
  const out = {};
  for (const [k, setObj] of Object.entries(SELECTIONS)) {
    out[k] = Array.from(setObj).sort((a, b) => a - b);
  }
  return out;
}

async function assembleFromSelections() {
  if (!CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(CURRENT_STEM)}/assemble`, {
      method: "POST",
      body: JSON.stringify({
        selections: selectionsPayload(),
        style: $("sel-style").value,
        abstract: $("txt-abstract").value,
      }),
    });
    CUSTOM_EDITED = false;
    $("txt-extract").value = data.extract || "";
    renderDocsUnits();
    renderChecks(data.checks);
    autoSaveSilent();
  } catch (err) {
    showToast(err.message, true);
  }
}

function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(async () => {
    await runLiveCheck();
    await autoSaveSilent();
  }, 300);
}

async function runLiveCheck() {
  if (!CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(CURRENT_STEM)}/check`, {
      method: "POST",
      body: JSON.stringify({
        style: $("sel-style").value,
        extract: $("txt-extract").value,
        abstract: $("txt-abstract").value,
      }),
    });
    // Cập nhật tô sáng các câu khớp trong văn bản gốc khi sửa trực tiếp chữ
    if (data.checks && data.checks.matched_units) {
      SELECTIONS = {};
      for (const d of CURRENT_UNITS.docs || []) SELECTIONS[d.n] = new Set();
      for (const u of data.checks.matched_units) {
        if (!SELECTIONS[u.doc]) SELECTIONS[u.doc] = new Set();
        SELECTIONS[u.doc].add(Number(u.id));
      }
      updateUnitHighlights();
    }
    renderChecks(data.checks);
  } catch (err) {
    showToast(err.message, true);
  }
}

function updateUnitHighlights() {
  document.querySelectorAll(".unit-item").forEach((el) => {
    const dn = Number(el.dataset.doc);
    const uid = Number(el.dataset.uid);
    const on = SELECTIONS[dn] && SELECTIONS[dn].has(uid);
    el.classList.toggle("selected", Boolean(on));
    const cb = el.querySelector("input");
    if (cb) cb.checked = Boolean(on);
  });
}

function renderChecks(chk) {
  if (!chk) return;
  const m = chk.metrics || {};
  $("m-total").textContent = m.total_words ?? 0;
  $("m-ext").textContent = m.extract_words ?? 0;
  $("ext-word-inline").textContent = m.extract_words ?? 0;
  $("m-compress").textContent = m.compress !== undefined ? `${(m.compress * 100).toFixed(1)}%` : "0%";
  $("m-abs").textContent = m.abstract_words ?? 0;
  $("abs-word-inline").textContent = m.abstract_words ?? 0;

  const t = m.abstract_targets || {};
  if (t.short_max !== undefined) {
    const hint = t.allow_longer
      ? `Cần ≤ ${t.short_max} từ hoặc ≥ ${t.long_min} từ`
      : `Mục tiêu: ${t.short_low}–${t.short_high} từ (tối đa ${t.short_max} từ)`;
    $("m-abs-target").textContent = `≤ ${t.short_max} từ (nên ${t.short_low}–${t.short_high})`;
    $("abs-target-inline").textContent = hint;
  }

  $("m-ratio-label").textContent = `Tỉ lệ ${m.ratio_name || "B/T"}`;
  $("m-ratio").textContent = m.ratio !== undefined ? `${(m.ratio * 100).toFixed(1)}%` : "—";
  const editStr = m.edit !== undefined ? m.edit.toFixed(2) : "—";
  const covStr = m.coverage !== undefined ? `${(m.coverage * 100).toFixed(0)}%` : "—";
  $("m-edit-cov").textContent = `${editStr} / ${covStr}`;

  // Hiển thị 3 điểm ước lượng ROUGE-2, BARTScore, G-Eval
  if ($("m-rouge2")) {
    const r2 = m.rouge2_est;
    $("m-rouge2").textContent = r2 !== undefined ? `${r2.toFixed(2)}%` : "—";
    $("m-rouge2").style.color = r2 !== undefined ? (r2 >= 33.0 ? "#047857" : "#b91c1c") : "";
  }
  if ($("m-bart")) {
    const bs = m.bartscore_est;
    $("m-bart").textContent = bs !== undefined ? bs.toFixed(2) : "—";
    $("m-bart").style.color = bs !== undefined ? (bs >= -2.56 ? "#047857" : "#b91c1c") : "";
  }
  if ($("m-geval")) {
    const jAvg = CURRENT_STATE && CURRENT_STATE.judge && CURRENT_STATE.judge.average;
    const ge = jAvg !== undefined ? jAvg : m.geval_est;
    const suffix = jAvg !== undefined ? " (AI)" : " (tạm tính)";
    $("m-geval").textContent = ge !== undefined ? `${ Number(ge).toFixed(2) }${suffix}` : "—";
    $("m-geval").style.color = ge !== undefined ? (ge >= 4.25 ? "#047857" : "#b91c1c") : "";
  }

  // Thanh trạng thái nguyên văn của trích chọn
  const sents = chk.extract_sentences || [];
  const badVerb = sents.filter((s) => s.status === "not_verbatim").length;
  const badOrd = sents.filter((s) => s.status === "out_of_order").length;
  const leadCut = sents.filter((s) => s.status === "lead_cut").length;
  const vbar = $("verbatim-bar");
  if (!sents.length) {
    vbar.innerHTML = `<span class="muted">Chưa có câu trích chọn.</span>`;
  } else if (badVerb || badOrd) {
    vbar.innerHTML = `<span style="color:#b91c1c;font-weight:600">⚠ Có ${badVerb} câu không nguyên văn, ${badOrd} câu sai thứ tự!</span>`;
  } else {
    vbar.innerHTML = `<span style="color:#047857">✓ Tất cả ${sents.length} câu đều nguyên văn và đúng thứ tự gốc${leadCut ? ` (${leadCut} câu đã cắt thành phần phụ đầu câu)` : ""}.</span>`;
  }

  // Danh sách lỗi / cảnh báo
  const list = $("issues-list");
  list.innerHTML = "";
  const issues = chk.issues || [];

  if (m.is_suspicious) {
    const banner = document.createElement("div");
    banner.className = "alert-suspicious";
    const reasonsStr = (m.suspicious_reasons || []).join(" · ") || "File có dấu hiệu lỗi bất thường";
    banner.innerHTML = `⚠️ <strong>CẢNH BÁO HỦY BÀI:</strong> ${escapeHtml(reasonsStr)}. Người duyệt nên cân nhắc hủy bài này!`;
    list.appendChild(banner);
  }

  if (!issues.length && !m.is_suspicious) {
    list.innerHTML = `<div class="issue-item issue-ok">✓ Không có lỗi hay cảnh báo cơ học.</div>`;
  } else {
    for (const iss of issues) {
      const div = document.createElement("div");
      const isSusp = iss.code === "suspicious_file" || (iss.message && iss.message.includes("[CẢNH BÁO HỦY BÀI]"));
      div.className = `issue-item issue-${iss.level}${isSusp ? " issue-suspicious" : ""}`;
      div.textContent = `[${iss.level_vi}] ${iss.message}`;
      list.appendChild(div);
    }
  }
  $("btn-approve").disabled = (chk.n_errors || 0) > 0;
}

function renderJudge(j) {
  const card = $("judge-card");
  const box = $("judge-content");
  if (!j || j.average === undefined) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  const issuesHtml = (j.issues || []).length
    ? `<ul>${j.issues.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul>`
    : `<div class="muted">Không phát hiện vấn đề nội dung.</div>`;
  box.innerHTML = `
    <div><strong>Trung bình: ${j.average} / 5.0</strong>
      (Phù hợp: ${j.relevance} · Liền mạch: ${j.coherence} · Nhất quán: ${j.consistency} · Trôi chảy: ${j.fluency})
      ${j.rewritten ? '<span class="badge">Đã tự viết lại 1 lần</span>' : ""}
    </div>
    <div class="small-mt">${escapeHtml(j.summary || "")}</div>
    ${issuesHtml}
  `;
}

function renderVersions(versions) {
  const ul = $("versions-list");
  if (!ul) return;
  ul.innerHTML = "";
}

async function autoSaveSilent() {
  if (!CURRENT_STEM) return;
  try {
    await api(`/api/file/${encodeURIComponent(CURRENT_STEM)}/save`, {
      method: "POST",
      body: JSON.stringify({
        style: $("sel-style").value,
        extract: $("txt-extract").value,
        abstract: $("txt-abstract").value,
        selections: selectionsPayload(),
        custom_edited: CUSTOM_EDITED,
      }),
    });
  } catch (_) {
    // Lưu ngầm không làm phiền người dùng
  }
}

async function saveDraft() {
  if (!CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(CURRENT_STEM)}/save`, {
      method: "POST",
      body: JSON.stringify({
        style: $("sel-style").value,
        extract: $("txt-extract").value,
        abstract: $("txt-abstract").value,
        selections: selectionsPayload(),
        custom_edited: CUSTOM_EDITED,
      }),
    });
    CURRENT_STATE = data.state;
    renderEditor(data.versions || []);
    await loadFileList(true);
    showToast(`Đã lưu nháp (v${String(data.version).padStart(3, "0")}).`);
  } catch (err) {
    showToast(err.message, true);
  }
}

function syncRerunButtonsState() {
  const isRunning = Boolean(CURRENT_STEM && RUNNING_STEMS.has(CURRENT_STEM));
  document.querySelectorAll("button[data-rerun]").forEach((b) => {
    b.disabled = isRunning;
  });
  const pill = $("cur-status");
  if (pill && isRunning) {
    pill.textContent = "⏳ Đang chạy AI...";
    pill.className = "status-pill st-running";
  }
}

async function rerunStep(step) {
  if (!CURRENT_STEM) return;
  const stem = CURRENT_STEM;
  if (RUNNING_STEMS.has(stem)) return;
  const stepNames = {
    label: "Gán nhãn",
    extract: "Trích chọn",
    abstract: "Biên soạn",
    judge: "Chấm G-Eval",
    all: "Chạy lại cả bài",
  };
  const labelVi = stepNames[step] || step;

  const chkMock = $("chk-mock");
  const reqPayload = {
    step,
    note: $("ai-note").value,
    mock: chkMock ? chkMock.checked : false,
    style: $("sel-style").value,
    extract: $("txt-extract").value,
    abstract: $("txt-abstract").value,
    ignore_mismatch: true,
  };

  // Đánh dấu đang chạy ở cột bên trái và trên thanh trạng thái của bài
  RUNNING_STEMS.add(stem);
  renderFileList();
  syncRerunButtonsState();
  const pill = $("cur-status");
  if (pill && CURRENT_STEM === stem) {
    pill.textContent = `⏳ Đang chạy (${labelVi})...`;
    pill.className = "status-pill st-running";
  }
  showToast(`⏳ Đang chạy '${labelVi}' cho ${stem}.docx...`);

  try {
    const data = await api(`/api/file/${encodeURIComponent(stem)}/rerun`, {
      method: "POST",
      body: JSON.stringify(reqPayload),
    });
    RUNNING_STEMS.delete(stem);
    if (CURRENT_STEM === stem) {
      CURRENT_STATE = data.state;
      CURRENT_UNITS = data.units || CURRENT_UNITS;
      initSelectionsFromState();
      renderEditor(data.versions || []);
    }
    await loadFileList(true);
    showToast(`✓ Đã chạy xong '${labelVi}' cho ${stem}.docx!`);
  } catch (err) {
    RUNNING_STEMS.delete(stem);
    await loadFileList(true);
    if (CURRENT_STEM === stem) {
      await selectFile(stem);
    }
    showToast(err.message, true);
  } finally {
    syncRerunButtonsState();
  }
}

async function restoreVersion(ver) {
  if (!CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(CURRENT_STEM)}/restore`, {
      method: "POST",
      body: JSON.stringify({ version: ver }),
    });
    CURRENT_STATE = data.state;
    initSelectionsFromState();
    renderEditor(data.versions || []);
    await loadFileList(true);
    showToast(`Đã khôi phục phiên bản v${String(ver).padStart(3, "0")}.`);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function approveAndExport(confirmWarnings = false) {
  if (!CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(CURRENT_STEM)}/approve`, {
      method: "POST",
      body: JSON.stringify({
        style: $("sel-style").value,
        extract: $("txt-extract").value,
        abstract: $("txt-abstract").value,
        confirm_warnings: confirmWarnings,
        allow_overwrite_content: true,
      }),
    });
    if (data.need_confirm) {
      if (window.confirm(data.message)) {
        await approveAndExport(true);
      }
      return;
    }
    CURRENT_STATE = data.state;
    renderEditor(data.versions || []);
    await loadFileList(true);
    showToast(`Đã duyệt và ghi: ${data.fill.output}`);
  } catch (err) {
    showToast(err.message, true);
  }
}

function openPreviewModal() {
  if (!CURRENT_UNITS) return;
  if ($("preview-modal-title")) $("preview-modal-title").textContent = "Xem trước bảng .docx cuối cùng";
  const body = $("preview-modal-body");
  if (body) body.innerHTML = `<table class="docx-preview-table" id="preview-table"></table>`;
  const tbl = $("preview-table");
  tbl.innerHTML = "";
  const addRow = (tag, text, yellow = false) => {
    const tr = document.createElement("tr");
    if (yellow) tr.className = "row-yellow";
    tr.innerHTML = `<td><div class="row-tag">${escapeHtml(tag)}</div><div>${escapeHtml(text || "")}</div></td>`;
    tbl.appendChild(tr);
  };
  addRow("Hàng 1 — Query", CURRENT_UNITS.query || "");
  for (const d of CURRENT_UNITS.docs || []) {
    const txt = d.units.map((u) => u.text).join(" ");
    addRow(`Hàng ${d.row + 1} — Văn bản gốc ${d.n} (${d.words} từ)`, txt);
  }
  addRow("Gợi ý trích chọn của AI", CURRENT_UNITS.ai_extract || "");
  addRow("Gợi ý biên soạn của AI", CURRENT_UNITS.ai_abstract || "");
  addRow("Hàng điền 1 — Phong cách chức năng", $("sel-style").value, true);
  addRow("Hàng điền 2 — Bản trích chọn", $("txt-extract").value, true);
  addRow("Hàng điền 3 — Bản biên soạn", $("txt-abstract").value, true);
  $("preview-modal").classList.remove("hidden");
}

async function openLogsModal() {
  if (!CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(CURRENT_STEM)}/logs`);
    if ($("preview-modal-title")) {
      $("preview-modal-title").textContent = `Nhật ký gọi Gemini (Logs) — ${CURRENT_STEM}.docx`;
    }
    const body = $("preview-modal-body");
    const logs = data.logs || [];
    if (!logs.length) {
      body.innerHTML = `<div class="muted">Chưa có log gọi Gemini nào cho file này (Thư mục: ${escapeHtml(data.log_dir || "")}).</div>`;
    } else {
      body.innerHTML =
        `<div class="muted" style="margin-bottom:8px">Thư mục trên ổ đĩa: <code>${escapeHtml(data.log_dir || "")}</code></div>` +
        logs
          .map((lg) => {
            const u = lg.usage || {};
            const tok = u.total_tokens ? `${u.total_tokens} tokens (in:${u.total_input_tokens || 0}, out:${u.total_output_tokens || 0}, think:${u.total_thought_tokens || 0})` : "";
            const stBadge = lg.error
              ? `<span style="color:#b91c1c;font-weight:700">LỖI: ${escapeHtml(lg.error)}</span>`
              : `<span style="color:#047857;font-weight:700">✓ Thành công</span>`;
            return `
              <details style="border:1px solid #cbd5e1;border-radius:6px;padding:8px 10px;margin-bottom:8px;background:#f8fafc">
                <summary style="cursor:pointer;font-size:13px">
                  <strong>[${escapeHtml(lg.started_at)}]</strong> Bước: <strong>${escapeHtml(lg.step)}</strong> (lần ${lg.attempt}, retry ${lg.net_try})
                  · Model: <strong style="color:#1d4ed8">${escapeHtml(lg.model)}</strong>
                  · Thời gian: <strong>${lg.duration_s}s</strong>
                  ${tok ? `· ${tok}` : ""} — ${stBadge}
                </summary>
                <div style="margin-top:8px">
                  <div style="font-weight:600;margin-bottom:4px">Phản hồi của Gemini:</div>
                  <pre class="ref-pre" style="max-height:220px;overflow-y:auto;background:#fff;border:1px solid #e2e8f0">${escapeHtml(lg.response_text || lg.error || "(trống)")}</pre>
                </div>
              </details>
            `;
          })
          .join("");
    }
    $("preview-modal").classList.remove("hidden");
  } catch (err) {
    showToast(err.message, true);
  }
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function moveFile(delta) {
  const list = filteredFiles();
  if (!list.length) return;
  const idx = list.findIndex((r) => r.file.replace(/\.docx$/i, "") === CURRENT_STEM);
  const nextIdx = Math.max(0, Math.min(list.length - 1, (idx < 0 ? 0 : idx) + delta));
  const stem = list[nextIdx].file.replace(/\.docx$/i, "");
  if (stem !== CURRENT_STEM) selectFile(stem);
}

async function uploadFiles(fileList, overwrite = false) {
  // Sao chép FileList sang mảng tĩnh ngay lập tức để không bị mất khi xóa input.value
  const filesArr = Array.from(fileList || []);
  if (!filesArr.length) return;
  $("file-upload-input").value = "";

  const runNow = $("chk-run-on-upload") ? $("chk-run-on-upload").checked : false;
  const useMock = $("chk-mock") ? $("chk-mock").checked : false;

  // Bước 1: Tải file vào input\ và inspect trước (rất nhanh, để hiện ngay danh sách file)
  const fd = new FormData();
  for (const f of filesArr) {
    fd.append("files", f);
  }
  fd.append("run_now", "false");
  fd.append("mock", useMock ? "true" : "false");
  fd.append("overwrite", overwrite ? "true" : "false");

  showToast(`Đang thêm ${filesArr.length} file vào danh sách...`);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || `Lỗi HTTP ${res.status}`);
    }
    if (data.need_overwrite) {
      if (window.confirm(data.message)) {
        await uploadFiles(filesArr, true);
      } else {
        await loadFileList(true);
      }
      return;
    }

    // Hiện ngay toàn bộ các file vừa thêm lên cột Chờ duyệt
    await loadFileList(true);
    if (data.stems && data.stems.length > 0) {
      await selectFile(data.stems[0]);
    }

    // Bước 2: Nếu có tick "Tự động chạy AI ngay khi thêm file", chạy cuốn chiếu từng file vừa thêm
    if (runNow && data.stems && data.stems.length > 0) {
      await runQueueOnStems(data.stems, useMock);
    } else if (data.errors && data.errors.length > 0) {
      showToast(`Đã thêm (${data.added.length} file), có lưu ý: ${data.errors[0]}`, true);
    } else {
      showToast(`Đã thêm ${data.added.length} file vào cột Chờ duyệt.`);
    }
  } catch (err) {
    showToast(err.message, true);
  }
}

function setBatchProgress(text) {
  const box = $("batch-progress");
  const stopBtn = $("btn-stop-batch");
  const runBtn = $("btn-run-pending");
  if (!box) return;
  if (text) {
    box.textContent = text;
    box.classList.remove("hidden");
    if (stopBtn) stopBtn.classList.remove("hidden");
    if (runBtn) runBtn.disabled = true;
  } else {
    box.classList.add("hidden");
    if (stopBtn) stopBtn.classList.add("hidden");
    if (runBtn) runBtn.disabled = false;
  }
}

async function runQueueOnStems(stems, useMock) {
  if (!stems || !stems.length) return;
  STOP_BATCH_REQUESTED = false;
  let doneCount = 0;
  let errCount = 0;
  let startedCount = 0;
  const concurrency = 4; // Chạy song song 4 file cùng lúc trên Paid Tier 1 (1.000 RPM)
  let nextIdx = 0;

  const updateProgressBanner = () => {
    const activeList = Array.from(RUNNING_STEMS).map((s) => `${s}.docx`).join(", ");
    setBatchProgress(
      `⏳ Đang chạy song song (${doneCount + errCount}/${stems.length} xong) — Đang xử lý: ${activeList || "..."}`
    );
  };

  async function worker() {
    while (!STOP_BATCH_REQUESTED) {
      const i = nextIdx++;
      if (i >= stems.length) break;
      const stem = stems[i];
      startedCount++;
      RUNNING_STEMS.add(stem);
      renderFileList();
      updateProgressBanner();

      try {
        await api(`/api/file/${encodeURIComponent(stem)}/rerun`, {
          method: "POST",
          body: JSON.stringify({ step: "all", mock: useMock, ignore_mismatch: false }),
        });
        doneCount++;
      } catch (err) {
        errCount++;
        showToast(`${stem}.docx: ${err.message}`, true);
      } finally {
        RUNNING_STEMS.delete(stem);
      }

      await loadFileList(true);
      updateProgressBanner();
      if (CURRENT_STEM === stem || doneCount === 1) {
        await selectFile(stem);
      }
    }
  }

  const workers = Array.from({ length: Math.min(concurrency, stems.length) }, () => worker());
  await Promise.all(workers);

  RUNNING_STEMS.clear();
  setBatchProgress("");
  await loadFileList(true);
  if (STOP_BATCH_REQUESTED) {
    showToast(`Đã dừng theo yêu cầu (đã xong ${doneCount}/${stems.length} file).`);
  } else {
    showToast(
      `Hoàn tất chạy AI: thành công ${doneCount}/${stems.length} file` +
        (errCount ? ` (${errCount} file lỗi).` : "!")
    );
  }
}

async function runPendingFiles() {
  const useMock = $("chk-mock") ? $("chk-mock").checked : false;
  await loadFileList(true);
  const todo = FILES
    .filter((r) => r.status === "chua_xu_ly" || r.status === "loi" || r.status === "can_xu_ly_tay")
    .map((r) => r.file.replace(/\.docx$/i, ""));
  if (!todo.length) {
    showToast("Tất cả các file trong danh sách đều đã ở trạng thái 'đã có nháp' hoặc 'đã duyệt'!");
    return;
  }
  await runQueueOnStems(todo, useMock);
}

async function deleteFiles(mode, stems = [], confirmMsg = "") {
  if (confirmMsg && !window.confirm(confirmMsg)) return;
  try {
    const data = await api("/api/delete-files", {
      method: "POST",
      body: JSON.stringify({ mode, stems }),
    });
    for (const f of data.deleted || []) {
      const st = f.replace(/\.docx$/i, "");
      DONE_SELECTED.delete(st);
      if (CURRENT_STEM === st) {
        CURRENT_STEM = null;
        CURRENT_STATE = null;
        CURRENT_UNITS = null;
      }
    }
    await loadFileList(false);
    if (!FILES.length) {
      $("editor-pane").classList.add("hidden");
      $("empty-state").classList.remove("hidden");
    }
    showToast(`Đã xóa ${data.count} file.`);
  } catch (err) {
    showToast(err.message, true);
  }
}

window.addEventListener("DOMContentLoaded", () => {
  $("btn-add-files").addEventListener("click", () => $("file-upload-input").click());
  $("file-upload-input").addEventListener("change", (e) => uploadFiles(e.target.files, false));
  $("btn-run-pending").addEventListener("click", runPendingFiles);
  if ($("btn-clear-all")) {
    $("btn-clear-all").addEventListener("click", () => {
      if (!FILES.length) return;
      deleteFiles(
        "all",
        [],
        `Bạn có chắc muốn XÓA TẤT CẢ ${FILES.length} file (cả Chờ duyệt và Đã duyệt) để bắt đầu ngày làm việc mới?`
      );
    });
  }
  if ($("btn-clear-pending")) {
    $("btn-clear-pending").addEventListener("click", () => {
      const n = FILES.filter((r) => !isApprovedStatus(r.status)).length;
      if (!n) return;
      deleteFiles("pending", [], `Bạn có chắc muốn xóa tất cả ${n} file trong cột Chờ duyệt?`);
    });
  }
  if ($("btn-clear-approved")) {
    $("btn-clear-approved").addEventListener("click", () => {
      const n = approvedFiles().length;
      if (!n) return;
      deleteFiles(
        "approved",
        [],
        `Bạn có chắc muốn xóa tất cả ${n} file trong cột Đã duyệt (hãy đảm bảo bạn đã tải file ZIP về máy)?`
      );
    });
  }
  if ($("btn-stop-batch")) {
    $("btn-stop-batch").addEventListener("click", () => {
      STOP_BATCH_REQUESTED = true;
      showToast("Đang dừng sau khi xong file hiện tại...");
    });
  }
  $("btn-download-zip").addEventListener("click", downloadDoneZip);
  $("chk-done-all").addEventListener("change", (e) => {
    const doneStems = FILES
      .filter((r) => r.status === "da_xuat" || r.status === "da_duyet")
      .map((r) => r.file.replace(/\.docx$/i, ""));
    if (e.target.checked) {
      doneStems.forEach((s) => DONE_SELECTED.add(s));
    } else {
      doneStems.forEach((s) => DONE_SELECTED.delete(s));
    }
    renderDoneList();
  });
  $("filter-status").addEventListener("change", renderFileList);
  $("sel-style").addEventListener("change", scheduleCheck);
  $("txt-extract").addEventListener("input", () => {
    CUSTOM_EDITED = true;
    scheduleCheck();
  });
  $("txt-abstract").addEventListener("input", scheduleCheck);
  $("btn-rebuild-extract").addEventListener("click", assembleFromSelections);
  if ($("btn-save")) $("btn-save").addEventListener("click", saveDraft);
  $("btn-approve").addEventListener("click", () => approveAndExport(false));
  if ($("btn-logs")) $("btn-logs").addEventListener("click", openLogsModal);
  if ($("btn-self-update")) {
    $("btn-self-update").addEventListener("click", async () => {
      showToast("Đang kiểm tra và tải bản cập nhật mới nhất...");
      try {
        const res = await api("/api/self-update", { method: "POST", body: "{}" });
        showToast("Đã cập nhật xong! Đang tải lại giao diện...");
        setTimeout(() => window.location.reload(), 900);
      } catch (err) {
        showToast(err.message, true);
      }
    });
  }
  $("btn-preview").addEventListener("click", openPreviewModal);
  $("btn-close-preview").addEventListener("click", () => $("preview-modal").classList.add("hidden"));
  $("preview-close-bg").addEventListener("click", () => $("preview-modal").classList.add("hidden"));

  document.querySelectorAll("button[data-rerun]").forEach((btn) => {
    btn.addEventListener("click", () => rerunStep(btn.dataset.rerun));
  });

  window.addEventListener("keydown", (e) => {
    if (e.altKey && e.key === "ArrowDown") {
      e.preventDefault();
      moveFile(1);
    } else if (e.altKey && e.key === "ArrowUp") {
      e.preventDefault();
      moveFile(-1);
    }
  });

  loadFileList(false);
});
