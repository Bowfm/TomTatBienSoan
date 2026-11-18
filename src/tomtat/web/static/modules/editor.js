// Quản lý khung soạn thảo: hiển thị văn bản, chọn câu, kiểm tra cơ học, lưu nháp, chạy lại AI, duyệt xuất
import { loadFileList, registerSelectFileHandler, renderFileList } from "./sidebar.js";
import { store } from "./state.js";
import { $, api, escapeHtml, showToast } from "./utils.js";

let checkTimer = null;

export async function selectFile(stem) {
  try {
    const data = await api(`/api/file/${encodeURIComponent(stem)}`);
    store.CURRENT_STEM = stem;
    store.CURRENT_STATE = data.state;
    store.CURRENT_UNITS = data.units;
    store.CUSTOM_EDITED = Boolean((store.CURRENT_STATE.extract || {}).custom_edited);
    initSelectionsFromState();
    renderFileList();
    renderEditor(data.versions || []);
  } catch (err) {
    showToast(err.message, true);
  }
}

// Đăng ký handler với sidebar để tránh vòng lặp phụ thuộc trực tiếp
registerSelectFileHandler(selectFile);

export function initSelectionsFromState() {
  store.SELECTIONS = {};
  if (!store.CURRENT_UNITS) return;
  for (const d of store.CURRENT_UNITS.docs || []) {
    store.SELECTIONS[d.n] = new Set();
  }
  const saved = (store.CURRENT_STATE.extract || {}).selections || {};
  let hasAny = false;
  for (const [k, arr] of Object.entries(saved)) {
    const dn = Number(k);
    if (!store.SELECTIONS[dn]) store.SELECTIONS[dn] = new Set();
    for (const id of arr || []) {
      store.SELECTIONS[dn].add(Number(id));
      hasAny = true;
    }
  }
  if (!hasAny && store.CURRENT_STATE.checks && store.CURRENT_STATE.checks.matched_units) {
    for (const u of store.CURRENT_STATE.checks.matched_units) {
      if (!store.SELECTIONS[u.doc]) store.SELECTIONS[u.doc] = new Set();
      store.SELECTIONS[u.doc].add(Number(u.id));
    }
  }
}

export function renderEditor(versions) {
  $("empty-state").classList.add("hidden");
  $("editor-pane").classList.remove("hidden");

  const st = store.CURRENT_STATE;
  const u = store.CURRENT_UNITS;
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

  $("txt-extract").value = (st.extract || {}).text || "";
  $("txt-abstract").value = (st.abstract || {}).text || "";

  renderDocsUnits();
  renderChecks(st.checks);
  renderJudge(st.judge);
  renderVersions(versions);
}

export function renderDocsUnits() {
  const box = $("docs-container");
  box.innerHTML = "";
  for (const d of store.CURRENT_UNITS.docs || []) {
    const selSet = store.SELECTIONS[d.n] || new Set();
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
      const docObj = store.CURRENT_UNITS.docs.find((x) => x.n === dn);
      if (!docObj) return;
      store.SELECTIONS[dn] = act === "all" ? new Set(docObj.units.map((u) => u.id)) : new Set();
      assembleFromSelections();
    });
  });
}

export async function onToggleUnit(docN, unitId, isChecked) {
  if (!store.SELECTIONS[docN]) store.SELECTIONS[docN] = new Set();
  if (isChecked) store.SELECTIONS[docN].add(unitId);
  else store.SELECTIONS[docN].delete(unitId);
  await assembleFromSelections();
}

export function selectionsPayload() {
  const out = {};
  for (const [k, setObj] of Object.entries(store.SELECTIONS)) {
    out[k] = Array.from(setObj).sort((a, b) => a - b);
  }
  return out;
}

export async function assembleFromSelections() {
  if (!store.CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(store.CURRENT_STEM)}/assemble`, {
      method: "POST",
      body: JSON.stringify({
        selections: selectionsPayload(),
        style: $("sel-style").value,
        abstract: $("txt-abstract").value,
      }),
    });
    store.CUSTOM_EDITED = false;
    $("txt-extract").value = data.extract || "";
    renderDocsUnits();
    renderChecks(data.checks);
    autoSaveSilent();
  } catch (err) {
    showToast(err.message, true);
  }
}

export function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(async () => {
    await runLiveCheck();
    await autoSaveSilent();
  }, 300);
}

export async function runLiveCheck() {
  if (!store.CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(store.CURRENT_STEM)}/check`, {
      method: "POST",
      body: JSON.stringify({
        style: $("sel-style").value,
        extract: $("txt-extract").value,
        abstract: $("txt-abstract").value,
      }),
    });
    if (data.checks && data.checks.matched_units) {
      store.SELECTIONS = {};
      for (const d of store.CURRENT_UNITS.docs || []) store.SELECTIONS[d.n] = new Set();
      for (const u of data.checks.matched_units) {
        if (!store.SELECTIONS[u.doc]) store.SELECTIONS[u.doc] = new Set();
        store.SELECTIONS[u.doc].add(Number(u.id));
      }
      updateUnitHighlights();
    }
    renderChecks(data.checks);
  } catch (err) {
    showToast(err.message, true);
  }
}

export function updateUnitHighlights() {
  document.querySelectorAll(".unit-item").forEach((el) => {
    const dn = Number(el.dataset.doc);
    const uid = Number(el.dataset.uid);
    const on = store.SELECTIONS[dn] && store.SELECTIONS[dn].has(uid);
    el.classList.toggle("selected", Boolean(on));
    const cb = el.querySelector("input");
    if (cb) cb.checked = Boolean(on);
  });
}

export function renderChecks(chk) {
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
    const jAvg = store.CURRENT_STATE && store.CURRENT_STATE.judge && store.CURRENT_STATE.judge.average;
    const ge = jAvg !== undefined ? jAvg : m.geval_est;
    const suffix = jAvg !== undefined ? " (AI)" : " (tạm tính)";
    $("m-geval").textContent = ge !== undefined ? `${Number(ge).toFixed(2)}${suffix}` : "—";
    $("m-geval").style.color = ge !== undefined ? (ge >= 4.25 ? "#047857" : "#b91c1c") : "";
  }

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

export function renderJudge(j) {
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

export function renderVersions(versions) {
  const ul = $("versions-list");
  if (!ul) return;
  ul.innerHTML = "";
}

export async function autoSaveSilent() {
  if (!store.CURRENT_STEM) return;
  try {
    await api(`/api/file/${encodeURIComponent(store.CURRENT_STEM)}/save`, {
      method: "POST",
      body: JSON.stringify({
        style: $("sel-style").value,
        extract: $("txt-extract").value,
        abstract: $("txt-abstract").value,
        selections: selectionsPayload(),
        custom_edited: store.CUSTOM_EDITED,
      }),
    });
  } catch (_) {
    // Lưu ngầm không làm phiền người dùng
  }
}

export async function saveDraft() {
  if (!store.CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(store.CURRENT_STEM)}/save`, {
      method: "POST",
      body: JSON.stringify({
        style: $("sel-style").value,
        extract: $("txt-extract").value,
        abstract: $("txt-abstract").value,
        selections: selectionsPayload(),
        custom_edited: store.CUSTOM_EDITED,
      }),
    });
    store.CURRENT_STATE = data.state;
    renderEditor(data.versions || []);
    await loadFileList(true);
    showToast(`Đã lưu nháp (v${String(data.version).padStart(3, "0")}).`);
  } catch (err) {
    showToast(err.message, true);
  }
}

export function syncRerunButtonsState() {
  const isRunning = Boolean(store.CURRENT_STEM && store.RUNNING_STEMS.has(store.CURRENT_STEM));
  document.querySelectorAll("button[data-rerun]").forEach((b) => {
    b.disabled = isRunning;
  });
  const pill = $("cur-status");
  if (pill && isRunning) {
    pill.textContent = "⏳ Đang chạy AI...";
    pill.className = "status-pill st-running";
  }
}

export async function rerunStep(step) {
  if (!store.CURRENT_STEM) return;
  const stem = store.CURRENT_STEM;
  if (store.RUNNING_STEMS.has(stem)) return;
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

  store.RUNNING_STEMS.add(stem);
  renderFileList();
  syncRerunButtonsState();
  const pill = $("cur-status");
  if (pill && store.CURRENT_STEM === stem) {
    pill.textContent = `⏳ Đang chạy (${labelVi})...`;
    pill.className = "status-pill st-running";
  }
  showToast(`⏳ Đang chạy '${labelVi}' cho ${stem}.docx...`);

  try {
    const data = await api(`/api/file/${encodeURIComponent(stem)}/rerun`, {
      method: "POST",
      body: JSON.stringify(reqPayload),
    });
    store.RUNNING_STEMS.delete(stem);
    if (store.CURRENT_STEM === stem) {
      store.CURRENT_STATE = data.state;
      store.CURRENT_UNITS = data.units || store.CURRENT_UNITS;
      initSelectionsFromState();
      renderEditor(data.versions || []);
    }
    await loadFileList(true);
    showToast(`✓ Đã chạy xong '${labelVi}' cho ${stem}.docx!`);
  } catch (err) {
    store.RUNNING_STEMS.delete(stem);
    await loadFileList(true);
    if (store.CURRENT_STEM === stem) {
      await selectFile(stem);
    }
    showToast(err.message, true);
  } finally {
    syncRerunButtonsState();
  }
}

export async function restoreVersion(ver) {
  if (!store.CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(store.CURRENT_STEM)}/restore`, {
      method: "POST",
      body: JSON.stringify({ version: ver }),
    });
    store.CURRENT_STATE = data.state;
    initSelectionsFromState();
    renderEditor(data.versions || []);
    await loadFileList(true);
    showToast(`Đã khôi phục phiên bản v${String(ver).padStart(3, "0")}.`);
  } catch (err) {
    showToast(err.message, true);
  }
}

export async function approveAndExport(confirmWarnings = false) {
  if (!store.CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(store.CURRENT_STEM)}/approve`, {
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
    store.CURRENT_STATE = data.state;
    renderEditor(data.versions || []);
    await loadFileList(true);
    showToast(`Đã duyệt và ghi: ${data.fill.output}`);
  } catch (err) {
    showToast(err.message, true);
  }
}
