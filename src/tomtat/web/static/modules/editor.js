// Quản lý khung soạn thảo: hiển thị văn bản, chọn câu, kiểm tra cơ học, lưu nháp, chạy lại AI, duyệt xuất
import {
  isApprovedStatus,
  loadFileList,
  pendingFiles,
  registerSelectFileHandler,
  registerStopFileHandler,
  registerSyncRerunButtonsHandler,
  renderFileList,
  renderRpdStatus,
  sortFiles,
  syncActiveFileItem,
  syncRunningProgressBar,
} from "./sidebar.js";
import { isStemBusy, isStemQueued, isStemRunning, store } from "./state.js";
import { $, api, escapeHtml, showToast, withPreservedScroll } from "./utils.js";

let checkTimer = null;
let _previewVersionHandler = null;
let _ensureTrackingHandler = null;
let _lastRenderedStem = null;

export const STEP_NAMES_VI = {
  inspect: "Đọc cấu trúc",
  label: "Gán nhãn",
  extract: "Trích chọn",
  extract_abstract: "Trích chọn & Biên soạn",
  abstract: "Biên soạn",
  judge: "Chấm G-Eval",
  all: "Chạy lại cả bài",
};

const STALE_PART_NAMES_VI = {
  extract: "Bản trích chọn",
  abstract: "Bản biên soạn",
  judge: "Điểm G-Eval",
};

export const VERSION_SOURCE_VI = {
  "ai:pipeline": "🤖 AI toàn bài",
  "ai:label": "🤖 AI gán nhãn",
  "ai:extract": "🤖 AI trích chọn",
  "ai:extract_abstract": "🤖 AI trích chọn & biên soạn",
  "ai:abstract": "🤖 AI biên soạn",
  "ai:judge": "🤖 AI chấm G-Eval",
  user: "👤 Lưu thủ công",
  approve: "✅ Đã duyệt",
  restore: "↩ Khôi phục",
  "auto:before_ai": "🛡 Tự lưu trước AI",
  "auto:before_restore": "🛡 Tự lưu trước rollback",
};

export function registerPreviewVersionHandler(fn) {
  _previewVersionHandler = fn;
}

export function registerEnsureTrackingHandler(fn) {
  _ensureTrackingHandler = fn;
}

export function formatVersionTime(isoStr) {
  if (!isoStr) return "";
  const m = String(isoStr).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2}:\d{2})/);
  if (m) return `${m[4]} ${m[3]}/${m[2]}`;
  return String(isoStr);
}

function formatVerTag(ver) {
  return `v${String(Number(ver || 0)).padStart(3, "0")}`;
}

function versionSourceClass(src) {
  const s = String(src || "");
  if (s.startsWith("ai:")) return "ver-badge-src-ai";
  if (s === "user") return "ver-badge-src-user";
  if (s === "approve") return "ver-badge-src-approve";
  if (s === "restore") return "ver-badge-src-restore";
  if (s.startsWith("auto:")) return "ver-badge-src-auto";
  return "";
}

function normalizeSelectionsSig(selObj) {
  if (!selObj || typeof selObj !== "object") return "";
  return Object.keys(selObj)
    .sort()
    .map((k) => {
      const nums = Array.from(selObj[k] || [])
        .map((x) => Number(x))
        .filter((n) => !Number.isNaN(n))
        .sort((a, b) => a - b);
      return nums.length ? `${k}:${nums.join(",")}` : "";
    })
    .filter(Boolean)
    .join(";");
}

function currentEditorContentSig() {
  const styleEl = $("sel-style");
  const extEl = $("txt-extract");
  const absEl = $("txt-abstract");
  const judge = store.CURRENT_STATE ? store.CURRENT_STATE.judge || null : null;
  const selObj = {};
  for (const [k, setObj] of Object.entries(store.SELECTIONS || {})) {
    selObj[k] = Array.from(setObj || []);
  }
  return [
    (styleEl ? styleEl.value : "").trim(),
    normalizeSelectionsSig(selObj),
    (extEl ? extEl.value : "").trim(),
    (absEl ? absEl.value : "").trim(),
    judge ? JSON.stringify(judge) : "",
  ].join("\n§\n");
}

function snapshotContentSig(snap) {
  if (!snap) return "";
  const lb = (snap.label || {}).value || "";
  const ex = snap.extract || {};
  const ab = snap.abstract || {};
  const judge = snap.judge || null;
  return [
    String(lb).trim(),
    normalizeSelectionsSig(ex.selections || {}),
    String(ex.text || "").trim(),
    String(ab.text || "").trim(),
    judge ? JSON.stringify(judge) : "",
  ].join("\n§\n");
}

function editorHasContent() {
  const styleVal = ($("sel-style") ? $("sel-style").value : "").trim();
  const extVal = ($("txt-extract") ? $("txt-extract").value : "").trim();
  const absVal = ($("txt-abstract") ? $("txt-abstract").value : "").trim();
  const hasSel = Object.values(store.SELECTIONS || {}).some((s) => s && s.size > 0);
  const hasJudge = Boolean(store.CURRENT_STATE && store.CURRENT_STATE.judge);
  return Boolean(styleVal || extVal || absVal || hasSel || hasJudge);
}

export function hasUnsavedVersionChanges() {
  if (!store.CURRENT_STEM) return false;
  const versions = store.CURRENT_VERSIONS || [];
  if (!versions.length) return editorHasContent();
  return currentEditorContentSig() !== snapshotContentSig(versions[0]);
}

export function computePrevRollbackTarget() {
  const versions = store.CURRENT_VERSIONS || [];
  if (!versions.length) return null;
  const curSig = currentEditorContentSig();
  if (curSig !== snapshotContentSig(versions[0])) {
    return versions[0];
  }
  const curV = Number((store.CURRENT_STATE || {}).current_version || 0);
  let older = curV > 0 ? versions.filter((v) => Number(v.version || 0) < curV) : versions.slice(1);
  if (!older.length) older = versions.slice(1);
  if (!older.length) return null;
  for (const v of older) {
    if (snapshotContentSig(v) !== curSig) return v;
  }
  return older[0];
}

export async function selectFile(stem, opts = {}) {
  if (!stem) return;
  clearTimeout(checkTimer);
  try {
    const data = await api(`/api/file/${encodeURIComponent(stem)}`);
    if (opts.onlyIfCurrent && store.CURRENT_STEM && store.CURRENT_STEM !== stem) {
      return;
    }
    store.CURRENT_STEM = stem;
    store.CURRENT_STATE = data.state;
    store.CURRENT_UNITS = data.units;
    store.CUSTOM_EDITED = Boolean((store.CURRENT_STATE.extract || {}).custom_edited);
    initSelectionsFromState();
    if (!opts.skipSidebarRender) {
      if (!syncActiveFileItem()) {
        renderFileList();
      }
    }
    renderEditor(data.versions || []);
  } catch (err) {
    if (!opts.silent) {
      showToast(err.message, true);
    }
  }
}

// Đăng ký handler với sidebar để tránh vòng lặp phụ thuộc trực tiếp
registerSelectFileHandler(selectFile);
registerSyncRerunButtonsHandler(syncRerunButtonsState);
registerStopFileHandler(stopFileAI);

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

function buildStalePromptHtml(staleSteps, staleReason, isRunning) {
  const hasExt = staleSteps.includes("extract");
  const hasAbs = staleSteps.includes("abstract");
  const hasJudge = staleSteps.includes("judge");
  const reasonPrefix = staleReason ? `${escapeHtml(staleReason)}. ` : "";
  const disAttr = isRunning ? "disabled" : "";

  let question = "";
  const buttons = [];
  if (hasExt && hasAbs) {
    question = `${reasonPrefix}Bạn có muốn AI chỉnh lại luôn <strong>Bản trích chọn</strong> và <strong>Bản biên soạn</strong> không?`;
    buttons.push(
      `<button type="button" class="btn btn-sm btn-warn-action" data-stale-rerun="extract_abstract" ${disAttr}>🤖 Chỉnh lại Bản trích chọn &amp; Bản biên soạn</button>`
    );
    buttons.push(
      `<button type="button" class="btn btn-sm" data-stale-rerun="abstract" ${disAttr}>🤖 Chỉ chỉnh lại Bản biên soạn</button>`
    );
  } else if (hasAbs) {
    question = `${reasonPrefix}Bạn có muốn AI chỉnh lại luôn <strong>Bản biên soạn</strong> không?`;
    buttons.push(
      `<button type="button" class="btn btn-sm btn-warn-action" data-stale-rerun="abstract" ${disAttr}>🤖 Chỉnh lại Bản biên soạn</button>`
    );
  } else if (hasExt) {
    question = `${reasonPrefix}Bạn có muốn AI chỉnh lại luôn <strong>Bản trích chọn</strong> không?`;
    buttons.push(
      `<button type="button" class="btn btn-sm btn-warn-action" data-stale-rerun="extract" ${disAttr}>🤖 Chỉnh lại Bản trích chọn</button>`
    );
  } else if (hasJudge) {
    question = `${reasonPrefix}Bạn có muốn AI chấm lại <strong>G-Eval</strong> không?`;
    buttons.push(
      `<button type="button" class="btn btn-sm btn-warn-action" data-stale-rerun="judge" ${disAttr}>🤖 Chấm lại G-Eval</button>`
    );
  } else {
    const partNames = staleSteps.map((s) => STALE_PART_NAMES_VI[s] || STEP_NAMES_VI[s] || s);
    question = `${reasonPrefix}Cần kiểm tra/chỉnh sửa lại những phần khác: ${escapeHtml(partNames.join(", "))}.`;
  }
  buttons.push(
    `<button type="button" class="btn btn-sm btn-stale-dismiss" data-stale-dismiss="1" ${disAttr}>Giữ nguyên (không sửa)</button>`
  );
  return `
    <div class="stale-action-box">
      <div>• ${question}</div>
      <div class="stale-action-buttons">${buttons.join("")}</div>
    </div>
  `;
}

function bindStalePromptActions(container) {
  if (!container) return;
  container.querySelectorAll("button[data-stale-rerun]").forEach((btn) => {
    btn.addEventListener("click", () => rerunStep(btn.dataset.staleRerun));
  });
  container.querySelectorAll("button[data-stale-dismiss]").forEach((btn) => {
    btn.addEventListener("click", () => dismissStalePrompt());
  });
}

export function renderFlagsAndStale() {
  const st = store.CURRENT_STATE;
  if (!st) return;
  const staleSteps = Array.isArray(st.stale_steps) ? st.stale_steps : [];
  const staleSet = new Set(staleSteps);
  const isRunning = isStemBusy(store.CURRENT_STEM);

  const syncStaleCard = (cardId, badgeId, stepKey) => {
    const card = $(cardId);
    const badge = $(badgeId);
    const isStale = staleSet.has(stepKey);
    if (card) card.classList.toggle("card-stale", isStale);
    if (badge) badge.classList.toggle("hidden", !isStale);
  };
  syncStaleCard("card-extract", "stale-badge-extract", "extract");
  syncStaleCard("card-abstract", "stale-badge-abstract", "abstract");
  syncStaleCard("judge-card", "stale-badge-judge", "judge");

  const normalFlags = [...(st.flags || [])];
  const fbox = $("flags-box");
  if (fbox) {
    if (normalFlags.length || staleSteps.length) {
      fbox.classList.remove("hidden");
      const flagsHtml = normalFlags.map((x) => `<div>• ${escapeHtml(x)}</div>`).join("");
      const staleHtml = staleSteps.length ? buildStalePromptHtml(staleSteps, st.stale_reason, isRunning) : "";
      fbox.innerHTML = flagsHtml + staleHtml;
      bindStalePromptActions(fbox);
    } else {
      fbox.classList.add("hidden");
      fbox.innerHTML = "";
    }
  }

  const inlinePrompt = $("label-stale-prompt");
  if (inlinePrompt) {
    if (staleSet.has("extract") || staleSet.has("abstract")) {
      inlinePrompt.classList.remove("hidden");
      inlinePrompt.innerHTML = buildStalePromptHtml(staleSteps, st.stale_reason, isRunning);
      bindStalePromptActions(inlinePrompt);
    } else {
      inlinePrompt.classList.add("hidden");
      inlinePrompt.innerHTML = "";
    }
  }
}

export async function dismissStalePrompt() {
  if (!store.CURRENT_STEM) return;
  await autoSaveSilent({ dismissStale: true, refreshFlags: true });
}

export async function onStyleChange() {
  clearTimeout(checkTimer);
  syncVersionControls();
  await runLiveCheck();
  await autoSaveSilent({ refreshFlags: true, refreshLabel: true });
}

function setStepBadge(elId, modelName) {
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
}

export function renderSummaryMeta(st, u) {
  if (!st || !u || !$("cur-meta")) return;
  const nDocs = (u.docs || []).length;
  const lb = st.label || {};
  const isManualLabel = Boolean(lb.manual_edited || lb.confidence === "thu_cong");
  const mLabel = isManualLabel ? "" : lb.model_used;
  const mExt = (st.extract || {}).model_used;
  const mAbs = (st.abstract || {}).model_used;
  const mJudge = (st.judge || {}).model_used;
  const modelParts = [];
  if (mLabel) modelParts.push(`Nhãn: ${mLabel}`);
  else if (isManualLabel && (mExt || mAbs)) modelParts.push("Nhãn: thủ công");
  if (mExt) modelParts.push(`Trích: ${mExt}`);
  if (mAbs) modelParts.push(`Biên soạn: ${mAbs}`);
  if (mJudge) modelParts.push(`G-Eval: ${mJudge}`);
  const modelSummary = modelParts.length
    ? ` · 🤖 Model đã chạy: ${modelParts.join(" | ")}`
    : st.model_used
      ? ` · 🤖 Model: ${st.model_used}`
      : "";
  $("cur-meta").textContent = `${nDocs > 1 ? "Đa văn bản" : "Đơn văn bản"} (${nDocs} văn bản, ${u.budget.total_words} từ gốc)${modelSummary}`;
}

export function renderLabelSection(st, opts = {}) {
  const lb = (st && st.label) || {};
  const isManual = Boolean(lb.manual_edited || lb.confidence === "thu_cong");
  setStepBadge("model-badge-label", isManual ? "" : lb.model_used);
  if (!opts.skipSelectValue && $("sel-style")) {
    $("sel-style").value = lb.value || "";
  }
  const confEl = $("label-conf");
  if (confEl) {
    if (isManual) {
      confEl.textContent = "👤 Người duyệt chỉnh sửa";
      confEl.title = lb.ai_confidence ? `Độ chắc chắn ban đầu của AI: ${lb.ai_confidence}` : "";
    } else {
      confEl.textContent = lb.confidence ? `Độ chắc chắn: ${lb.confidence}` : "Chưa có độ chắc chắn";
      confEl.title = "";
    }
  }
  if ($("label-reason")) {
    $("label-reason").textContent = lb.reason || "";
  }
  const perDocDiv = $("label-per-doc");
  if (perDocDiv) {
    if (!isManual && lb.per_doc && lb.per_doc.length > 1) {
      perDocDiv.innerHTML = lb.per_doc
        .map((p) => `<div>- Văn bản ${p.doc} (${p.words} từ): <strong>${escapeHtml(p.label)}</strong> — ${escapeHtml(p.reason || "")}</div>`)
        .join("");
    } else {
      perDocDiv.innerHTML = "";
    }
  }
}

export function renderEditor(versions) {
  $("empty-state").classList.add("hidden");
  $("editor-pane").classList.remove("hidden");

  const st = store.CURRENT_STATE;
  const u = store.CURRENT_UNITS;
  $("cur-filename").textContent = st.file;
  const mExt = (st.extract || {}).model_used;
  const mAbs = (st.abstract || {}).model_used;
  renderSummaryMeta(st, u);

  const pill = $("cur-status");
  pill.textContent = st.status_vi;
  pill.className = `status-pill st-${st.status}`;
  syncRerunButtonsState();

  renderFlagsAndStale();

  $("query-box").textContent = u.query || "(Không có câu truy vấn)";
  $("budget-hint").textContent =
    `Ngân sách trích chọn: ${u.budget.extract_min}–${u.budget.extract_max} từ (nhắm ${u.budget.extract_target_low}–${u.budget.extract_target_high} từ, lý tưởng ~${u.budget.extract_target_mid} từ)`;

  $("ai-extract-ref").textContent = u.ai_extract || "(trống)";
  $("ai-abstract-ref").textContent = u.ai_abstract || "(trống)";

  setStepBadge("model-badge-extract", mExt);
  setStepBadge("model-badge-abstract", mAbs);
  renderLabelSection(st);

  $("txt-extract").value = (st.extract || {}).text || "";
  $("txt-abstract").value = (st.abstract || {}).text || "";
  const stemChanged = _lastRenderedStem !== st.stem;
  if (stemChanged) {
    if ($("version-note")) $("version-note").value = "";
    _lastRenderedStem = st.stem;
  }

  renderDocsUnits({ preserveScroll: !stemChanged });
  renderChecks(st.checks);
  renderJudge(st.judge);
  renderVersions(versions, { resetScroll: stemChanged });
  syncRerunButtonsState();
}

export function renderDocsUnits(opts = {}) {
  const box = $("docs-container");
  if (!box) return;
  withPreservedScroll(
    box,
    () => {
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
    },
    { reset: !opts.preserveScroll }
  );
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
    renderDocsUnits({ preserveScroll: true });
    renderChecks(data.checks);
    syncVersionControls();
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
    syncVersionControls();
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
  const approveBtn = $("btn-approve");
  if (approveBtn) {
    approveBtn.disabled = isStemBusy(store.CURRENT_STEM) || (chk.n_errors || 0) > 0;
  }
}

export function renderJudge(j) {
  const card = $("judge-card");
  const box = $("judge-content");
  setStepBadge("model-badge-judge", j && j.model_used);
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

export function syncVersionControls() {
  const versions = store.CURRENT_VERSIONS || [];
  const curVer = Number((store.CURRENT_STATE || {}).current_version || 0);
  const dirty = hasUnsavedVersionChanges();
  const prevTarget = computePrevRollbackTarget();
  const isRunning = isStemBusy(store.CURRENT_STEM);

  const badge = $("cur-version-badge");
  if (badge) {
    badge.textContent = curVer > 0 ? formatVerTag(curVer) : versions.length ? formatVerTag(versions[0].version) : "Chưa lưu";
  }
  const dirtyEl = $("version-dirty-badge");
  if (dirtyEl) {
    dirtyEl.classList.toggle("hidden", !dirty);
  }
  const cntEl = $("versions-count");
  if (cntEl) cntEl.textContent = String(versions.length);
  const cntBtnEl = $("versions-count-btn");
  if (cntBtnEl) cntBtnEl.textContent = String(versions.length);

  const btnSave = $("btn-save");
  if (btnSave) btnSave.disabled = isRunning || !store.CURRENT_STEM;

  const btnPrev = $("btn-rollback-prev");
  if (btnPrev) {
    if (prevTarget) {
      const targetTag = formatVerTag(prevTarget.version);
      btnPrev.disabled = isRunning;
      btnPrev.textContent = `↩ Rollback về ${targetTag}`;
      btnPrev.title = `Quay lại phiên bản ${targetTag} (${VERSION_SOURCE_VI[prevTarget.source] || prevTarget.source || ""})`;
    } else {
      btnPrev.disabled = true;
      btnPrev.textContent = "↩ Rollback version trước";
      btnPrev.title = "Chưa có phiên bản trước đó để rollback";
    }
  }

  const ul = $("versions-list");
  if (ul) {
    ul.querySelectorAll("button[data-ver-restore]").forEach((btn) => {
      const vNum = Number(btn.dataset.verRestore || 0);
      const isExactCurrent = !dirty && curVer > 0 && vNum === curVer;
      btn.disabled = isRunning || isExactCurrent;
      btn.textContent = isExactCurrent ? "Đang dùng" : "↩ Rollback";
    });
  }
}

export function renderVersions(versions, opts = {}) {
  store.CURRENT_VERSIONS = Array.isArray(versions) ? versions : [];
  const ul = $("versions-list");
  if (!ul) {
    syncVersionControls();
    return;
  }
  withPreservedScroll(
    ul,
    () => {
      ul.innerHTML = "";
      const list = store.CURRENT_VERSIONS;
      const curVer = Number((store.CURRENT_STATE || {}).current_version || 0);
      const dirty = hasUnsavedVersionChanges();

      if (!list.length) {
        ul.innerHTML = `<li class="muted" style="padding:8px 4px">Chưa có version nào được lưu cho file này. Bấm <strong>💾 Lưu version</strong> hoặc chạy AI để tạo mốc đầu tiên.</li>`;
        syncVersionControls();
        return;
      }

      for (const v of list) {
        const vNum = Number(v.version || 0);
        const vTag = formatVerTag(vNum);
        const isExactCurrent = !dirty && curVer > 0 && vNum === curVer;
        const srcLabel = VERSION_SOURCE_VI[v.source] || v.source || "Nháp";
        const srcCls = versionSourceClass(v.source);
        const extraApproveBadge =
          v.approved && v.source !== "approve"
            ? '<span class="badge ver-badge-src ver-badge-src-approve">✅ Đã duyệt</span>'
            : "";
        const lbVal = (v.label || {}).value || "(chưa nhãn)";
        const ew = (v.extract || {}).words ?? 0;
        const aw = (v.abstract || {}).words ?? 0;
        const chk = v.checks_summary || {};
        const chkTxt = chk.ok === true ? "✓ Đạt" : chk.ok === false ? `✕ ${chk.n_errors || 0} lỗi` : "—";
        const modelTxt = v.model_used || (v.abstract || {}).model_used || (v.extract || {}).model_used || "";
        const noteTxt = v.note ? ` · "${v.note}"` : "";

        const li = document.createElement("li");
        if (isExactCurrent) li.className = "ver-active";
        li.innerHTML = `
          <div class="ver-item-main">
            <div class="ver-item-head">
              <strong>${escapeHtml(vTag)}</strong>
              ${isExactCurrent ? '<span class="badge ver-badge-cur">Đang dùng</span>' : ""}
              <span class="badge ver-badge-src ${srcCls}">${escapeHtml(srcLabel)}</span>
              ${extraApproveBadge}
              <span class="muted">${escapeHtml(formatVersionTime(v.saved_at))}</span>
            </div>
            <div class="ver-item-meta">
              ${escapeHtml(lbVal)} · Trích ${ew}w · BSoạn ${aw}w · ${escapeHtml(chkTxt)}${modelTxt ? ` · 🤖 ${escapeHtml(modelTxt)}` : ""}${escapeHtml(noteTxt)}
            </div>
          </div>
          <div class="ver-item-actions">
            <button type="button" class="btn btn-sm" data-ver-preview="${vNum}" title="Xem nội dung &amp; so sánh với bản hiện tại">👁 Xem</button>
            <button type="button" class="btn btn-sm" data-ver-restore="${vNum}" ${isExactCurrent ? "disabled" : ""}>${isExactCurrent ? "Đang dùng" : "↩ Rollback"}</button>
          </div>
        `;
        const btnPrev = li.querySelector("button[data-ver-preview]");
        if (btnPrev) {
          btnPrev.addEventListener("click", () => {
            if (_previewVersionHandler) _previewVersionHandler(vNum);
          });
        }
        const btnRest = li.querySelector("button[data-ver-restore]");
        if (btnRest) {
          btnRest.addEventListener("click", () => restoreVersion(vNum));
        }
        ul.appendChild(li);
      }
      syncVersionControls();
    },
    { reset: Boolean(opts.resetScroll) }
  );
}

export async function autoSaveSilent(opts = {}) {
  if (!store.CURRENT_STEM) return;
  if (isStemBusy(store.CURRENT_STEM)) return;
  try {
    const body = {
      style: $("sel-style").value,
      extract: $("txt-extract").value,
      abstract: $("txt-abstract").value,
      selections: selectionsPayload(),
      custom_edited: store.CUSTOM_EDITED,
      auto_save: true,
      create_version: false,
    };
    if (opts.dismissStale) body.dismiss_stale = true;
    const data = await api(`/api/file/${encodeURIComponent(store.CURRENT_STEM)}/save`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (
      data &&
      data.state &&
      store.CURRENT_STATE &&
      store.CURRENT_STATE.stem === store.CURRENT_STEM &&
      !isStemBusy(store.CURRENT_STEM)
    ) {
      const prevStale = JSON.stringify(store.CURRENT_STATE.stale_steps || []);
      const prevReason = store.CURRENT_STATE.stale_reason || null;
      const prevFlags = JSON.stringify(store.CURRENT_STATE.flags || []);
      const prevLabelSig = JSON.stringify(store.CURRENT_STATE.label || {});
      store.CURRENT_STATE.updated_at = data.state.updated_at;
      store.CURRENT_STATE.status = data.state.status;
      store.CURRENT_STATE.status_vi = data.state.status_vi;
      store.CURRENT_STATE.checks = data.state.checks;
      store.CURRENT_STATE.flags = data.state.flags || [];
      store.CURRENT_STATE.stale_steps = data.state.stale_steps || [];
      store.CURRENT_STATE.stale_reason = data.state.stale_reason || null;
      const curSelectVal = $("sel-style") ? $("sel-style").value : "";
      const respLabelVal = (data.state.label || {}).value || "";
      if (curSelectVal === respLabelVal) {
        store.CURRENT_STATE.label = data.state.label;
      }
      if (Array.isArray(data.versions)) {
        store.CURRENT_VERSIONS = data.versions;
      }
      syncVersionControls();
      const nextStale = JSON.stringify(store.CURRENT_STATE.stale_steps || []);
      const nextFlags = JSON.stringify(store.CURRENT_STATE.flags || []);
      const nextLabelSig = JSON.stringify(store.CURRENT_STATE.label || {});
      if (opts.refreshLabel || prevLabelSig !== nextLabelSig) {
        renderLabelSection(store.CURRENT_STATE, { skipSelectValue: true });
        renderSummaryMeta(store.CURRENT_STATE, store.CURRENT_UNITS);
      }
      if (
        opts.refreshFlags ||
        prevStale !== nextStale ||
        prevReason !== store.CURRENT_STATE.stale_reason ||
        prevFlags !== nextFlags
      ) {
        renderFlagsAndStale();
      }
    }
  } catch (_) {
    // Lưu ngầm không làm phiền người dùng
  }
}

export async function saveDraft() {
  if (!store.CURRENT_STEM) return;
  if (isStemBusy(store.CURRENT_STEM)) {
    showToast(`File ${store.CURRENT_STEM}.docx đang chạy hoặc đang chờ lượt AI, vui lòng đợi hoàn tất.`, true);
    return;
  }
  clearTimeout(checkTimer);
  const noteInput = $("version-note");
  const noteVal = noteInput ? noteInput.value.trim() : "";
  try {
    const data = await api(`/api/file/${encodeURIComponent(store.CURRENT_STEM)}/save`, {
      method: "POST",
      body: JSON.stringify({
        style: $("sel-style").value,
        extract: $("txt-extract").value,
        abstract: $("txt-abstract").value,
        selections: selectionsPayload(),
        custom_edited: store.CUSTOM_EDITED,
        note: noteVal,
        auto_save: false,
        create_version: true,
      }),
    });
    if (noteInput) noteInput.value = "";
    store.CURRENT_STATE = data.state;
    renderEditor(data.versions || []);
    await loadFileList(true);
    showToast(`✓ Đã lưu phiên bản ${formatVerTag(data.version)}.`);
  } catch (err) {
    showToast(err.message, true);
  }
}

export function applyRerunOutcome(stem, step, labelVi, data) {
  if (!data) return;
  if (data.cancelled) {
    showToast(`⏹ Đã dừng '${labelVi}' cho ${stem}.docx.`, "warn");
  } else if (data.error) {
    showToast(data.error, true);
  } else if (step === "label") {
    const stale = (data.state && data.state.stale_steps) || data.stale_steps || [];
    if (data.label_changed && stale.length) {
      const partNames = stale.map((s) => STALE_PART_NAMES_VI[s] || STEP_NAMES_VI[s] || s);
      const prevLb = data.previous_label || "(trống)";
      const newLb = data.new_label || ((data.state && data.state.label && data.state.label.value) || "");
      showToast(
        `⚠️ Nhãn đổi từ '${prevLb}' sang '${newLb}' — cần chỉnh sửa lại: ${partNames.join(", ")}!`,
        "warn"
      );
    } else if (!data.label_changed) {
      const curLb = data.new_label || ((data.state && data.state.label && data.state.label.value) || "");
      showToast(
        curLb
          ? `✓ Đã chạy xong 'Gán nhãn' cho ${stem}.docx (nhãn không đổi: '${curLb}').`
          : `✓ Đã chạy xong '${labelVi}' cho ${stem}.docx!`
      );
    } else {
      showToast(`✓ Đã chạy xong '${labelVi}' cho ${stem}.docx!`);
    }
  } else {
    showToast(`✓ Đã chạy xong '${labelVi}' cho ${stem}.docx!`);
  }
}

export function syncRerunButtonsState() {
  const isRunning = isStemRunning(store.CURRENT_STEM);
  const isQueued = isStemQueued(store.CURRENT_STEM);
  const isBusy = isRunning || isQueued;
  const isStopping = Boolean(store.CURRENT_STEM && isBusy && store.STOPPING_STEMS.has(store.CURRENT_STEM));
  document.querySelectorAll("button[data-rerun], button[data-stale-rerun], button[data-stale-dismiss]").forEach((b) => {
    b.disabled = isBusy;
  });
  const stopFileBtn = $("btn-stop-file-ai");
  if (stopFileBtn) {
    stopFileBtn.classList.toggle("hidden", !isBusy);
    stopFileBtn.disabled = !isBusy || isStopping;
    stopFileBtn.textContent = isStopping ? "⏳ Đang dừng..." : isQueued ? "⏹ Hủy chờ AI" : "⏹ Dừng AI";
  }
  const approveBtn = $("btn-approve");
  if (approveBtn && store.CURRENT_STATE) {
    const nErr = ((store.CURRENT_STATE.checks || {}).n_errors || 0);
    approveBtn.disabled = isBusy || nErr > 0;
  }
  syncRunningProgressBar();
  syncVersionControls();
  const pill = $("cur-status");
  if (!pill) return;
  if (isRunning) {
    if (isStopping) {
      pill.textContent = "⏳ Đang dừng...";
    } else {
      const curStep =
        (store.RUNNING_STEPS && store.RUNNING_STEPS[store.CURRENT_STEM]) ||
        (store.CURRENT_STATE && store.CURRENT_STATE.current_step) ||
        "";
      const stepVi = STEP_NAMES_VI[curStep] || "";
      pill.textContent = stepVi ? `⏳ Đang chạy (${stepVi})...` : "⏳ Đang chạy AI...";
    }
    pill.className = "status-pill st-running";
  } else if (isQueued) {
    if (isStopping) {
      pill.textContent = "⏳ Đang dừng...";
    } else {
      const qStep = (store.QUEUED_STEPS && store.QUEUED_STEPS[store.CURRENT_STEM]) || "";
      const stepVi = STEP_NAMES_VI[qStep] || "";
      const qPos = store.QUEUED_POSITIONS ? store.QUEUED_POSITIONS[store.CURRENT_STEM] : 0;
      pill.textContent = stepVi
        ? `⏳ Đang chờ lượt${qPos ? ` #${qPos}` : ""} (${stepVi})...`
        : `⏳ Đang chờ lượt${qPos ? ` #${qPos}` : ""}...`;
    }
    pill.className = "status-pill st-queued";
  } else if (store.CURRENT_STATE) {
    pill.textContent = store.CURRENT_STATE.status_vi;
    pill.className = `status-pill st-${store.CURRENT_STATE.status}`;
  }
}

export async function stopFileAI(targetStem = null) {
  const stem = typeof targetStem === "string" && targetStem ? targetStem : store.CURRENT_STEM;
  if (!stem) return;
  if (store.STOPPING_STEMS.has(stem)) return;
  store.STOPPING_STEMS.add(stem);
  renderFileList();
  syncRerunButtonsState();
  try {
    const res = await api(`/api/file/${encodeURIComponent(stem)}/stop`, { method: "POST" });
    if (res && res.was_queued) {
      const pend = store.PENDING_RERUN_JOBS.get(stem);
      if (pend && pend.jobId) store.SEEN_RERUN_JOB_IDS.add(pend.jobId);
      store.PENDING_RERUN_JOBS.delete(stem);
      store.STOPPING_STEMS.delete(stem);
      store.LOCAL_QUEUED_STEMS.delete(stem);
      store.QUEUED_STEMS.delete(stem);
      delete store.QUEUED_STEPS[stem];
      delete store.QUEUED_POSITIONS[stem];
      await loadFileList(true);
      if (store.CURRENT_STEM === stem) {
        await selectFile(stem, { silent: true });
      }
      showToast(res.message || `Đã hủy chờ lượt chạy AI cho ${stem}.docx.`, "warn");
    } else if (res && res.stopped) {
      showToast(res.message || `Đã gửi yêu cầu dừng chạy AI cho ${stem}.docx.`, "warn");
    } else {
      const pend = store.PENDING_RERUN_JOBS.get(stem);
      if (pend && pend.jobId) store.SEEN_RERUN_JOB_IDS.add(pend.jobId);
      store.PENDING_RERUN_JOBS.delete(stem);
      store.STOPPING_STEMS.delete(stem);
      store.LOCAL_RERUN_STEMS.delete(stem);
      store.LOCAL_QUEUED_STEMS.delete(stem);
      store.RUNNING_STEMS.delete(stem);
      store.QUEUED_STEMS.delete(stem);
      delete store.RUNNING_STEPS[stem];
      delete store.QUEUED_STEPS[stem];
      delete store.QUEUED_POSITIONS[stem];
      await loadFileList(true);
      if (store.CURRENT_STEM === stem) {
        await selectFile(stem, { silent: true });
      }
      showToast((res && res.message) || `File ${stem}.docx hiện không còn chạy AI.`);
    }
  } catch (err) {
    store.STOPPING_STEMS.delete(stem);
    showToast(err.message, true);
  } finally {
    renderFileList();
    syncRerunButtonsState();
  }
}

export async function rerunStep(step) {
  if (!store.CURRENT_STEM) return;
  const stem = store.CURRENT_STEM;
  if (isStemBusy(stem)) {
    showToast(`File ${stem}.docx đang chạy hoặc đang chờ lượt AI, vui lòng đợi hoàn tất.`, true);
    return;
  }
  const labelVi = STEP_NAMES_VI[step] || step;

  const chkMock = $("chk-mock");
  const selConc = $("sel-concurrency");
  const rawConc = selConc ? selConc.value : "auto";
  const concurrencyVal = rawConc === "auto" ? "auto" : Number(rawConc) || "auto";
  const reqPayload = {
    step,
    restart: step === "all",
    async: true,
    concurrency: concurrencyVal,
    mock: chkMock ? chkMock.checked : false,
    style: $("sel-style").value,
    extract: $("txt-extract").value,
    abstract: $("txt-abstract").value,
    ignore_mismatch: true,
  };

  const maxAllowed = Math.max(1, Number(store.ALLOWED_WORKERS || 3));
  const looksFull = store.RUNNING_STEMS.size >= maxAllowed || store.QUEUED_STEMS.size > 0;
  const initStep = step === "all" ? "inspect" : step === "extract_abstract" ? "extract" : step;

  store.STOPPING_STEMS.delete(stem);
  if (looksFull) {
    store.LOCAL_QUEUED_STEMS.add(stem);
    store.QUEUED_STEMS.add(stem);
    store.QUEUED_STEPS[stem] = step;
    store.QUEUED_POSITIONS[stem] = store.QUEUED_STEMS.size;
  } else {
    store.LOCAL_RERUN_STEMS.add(stem);
    store.RUNNING_STEMS.add(stem);
    store.RUNNING_STEPS[stem] = initStep;
  }
  renderFileList();
  syncRerunButtonsState();

  try {
    const data = await api(`/api/file/${encodeURIComponent(stem)}/rerun`, {
      method: "POST",
      body: JSON.stringify(reqPayload),
    });
    store.LOCAL_RERUN_STEMS.delete(stem);
    store.LOCAL_QUEUED_STEMS.delete(stem);

    if (data && data.accepted) {
      const recent = data.batch_status && data.batch_status.recent_rerun_results
        ? data.batch_status.recent_rerun_results[stem]
        : null;
      if (recent && recent.job_id === data.job_id) {
        store.SEEN_RERUN_JOB_IDS.add(data.job_id);
        store.RUNNING_STEMS.delete(stem);
        store.QUEUED_STEMS.delete(stem);
        delete store.RUNNING_STEPS[stem];
        delete store.QUEUED_STEPS[stem];
        delete store.QUEUED_POSITIONS[stem];
        if (data.batch_status && data.batch_status.rpd_status) {
          renderRpdStatus(data.batch_status.rpd_status);
        }
        await loadFileList(true);
        if (store.CURRENT_STEM === stem) {
          await selectFile(stem, { silent: true, onlyIfCurrent: true, skipSidebarRender: true });
        }
        applyRerunOutcome(stem, step, labelVi, recent);
        return;
      }

      store.PENDING_RERUN_JOBS.set(stem, {
        jobId: data.job_id,
        step,
        labelVi,
        notifiedStart: !data.queued,
      });
      if (data.queued) {
        store.RUNNING_STEMS.delete(stem);
        delete store.RUNNING_STEPS[stem];
        store.QUEUED_STEMS.add(stem);
        store.QUEUED_STEPS[stem] = step;
        store.QUEUED_POSITIONS[stem] = data.queue_position || 1;
        const activeW = Number(data.active_workers || store.ALLOWED_WORKERS || 3);
        const allowW = Number(data.allowed_workers || store.ALLOWED_WORKERS || 3);
        showToast(
          `⏳ Đang chạy đủ ${activeW}/${allowW} luồng AI — '${labelVi}' cho ${stem}.docx đã vào hàng chờ (#${store.QUEUED_POSITIONS[stem]}) và sẽ tự chạy khi 1 luồng hoàn thành.`,
          "warn"
        );
      } else {
        store.QUEUED_STEMS.delete(stem);
        delete store.QUEUED_STEPS[stem];
        delete store.QUEUED_POSITIONS[stem];
        store.RUNNING_STEMS.add(stem);
        store.RUNNING_STEPS[stem] = initStep;
        showToast(`⏳ Đang chạy '${labelVi}' cho ${stem}.docx...`);
      }
      renderFileList();
      syncRerunButtonsState();
      if (_ensureTrackingHandler) {
        _ensureTrackingHandler();
      }
      return;
    }

    // Fallback xử lý đồng bộ (giữ tương thích nếu server trả về trực tiếp kết quả)
    if (data.rpd_status) {
      renderRpdStatus(data.rpd_status);
    }
    store.STOPPING_STEMS.delete(stem);
    store.RUNNING_STEMS.delete(stem);
    store.QUEUED_STEMS.delete(stem);
    delete store.RUNNING_STEPS[stem];
    delete store.QUEUED_STEPS[stem];
    delete store.QUEUED_POSITIONS[stem];
    if (store.CURRENT_STEM === stem && data.state) {
      store.CURRENT_STATE = data.state;
      store.CURRENT_UNITS = data.units || store.CURRENT_UNITS;
      store.CUSTOM_EDITED = Boolean((store.CURRENT_STATE.extract || {}).custom_edited);
      initSelectionsFromState();
      renderEditor(data.versions || []);
    }
    await loadFileList(true);
    applyRerunOutcome(stem, step, labelVi, data);
  } catch (err) {
    store.STOPPING_STEMS.delete(stem);
    store.LOCAL_RERUN_STEMS.delete(stem);
    store.LOCAL_QUEUED_STEMS.delete(stem);
    const alreadyRunning = err && (err.status === 409 || (err.data && err.data.already_running));
    if (!alreadyRunning) {
      store.RUNNING_STEMS.delete(stem);
      store.QUEUED_STEMS.delete(stem);
      delete store.RUNNING_STEPS[stem];
      delete store.QUEUED_STEPS[stem];
      delete store.QUEUED_POSITIONS[stem];
    }
    await loadFileList(true);
    if (store.CURRENT_STEM === stem) {
      await selectFile(stem, { silent: true });
    }
    showToast(err.message, true);
  } finally {
    syncRerunButtonsState();
  }
}

export async function restoreVersion(ver = null) {
  if (!store.CURRENT_STEM) return false;
  if (isStemBusy(store.CURRENT_STEM)) {
    showToast(`File ${store.CURRENT_STEM}.docx đang chạy hoặc đang chờ lượt AI, vui lòng đợi hoàn tất.`, true);
    return false;
  }
  clearTimeout(checkTimer);
  const payload = { version: ver };
  if (hasUnsavedVersionChanges()) {
    payload.style = $("sel-style").value;
    payload.extract = $("txt-extract").value;
    payload.abstract = $("txt-abstract").value;
    payload.selections = selectionsPayload();
    payload.custom_edited = store.CUSTOM_EDITED;
  }
  try {
    const data = await api(`/api/file/${encodeURIComponent(store.CURRENT_STEM)}/restore`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    store.CURRENT_STATE = data.state;
    store.CUSTOM_EDITED = Boolean((store.CURRENT_STATE.extract || {}).custom_edited);
    initSelectionsFromState();
    renderEditor(data.versions || []);
    await loadFileList(true);
    const restoredTag = formatVerTag(data.restored_from || ver);
    const newTag = formatVerTag(data.version);
    showToast(`✓ Đã rollback về ${restoredTag} (ghi thành ${newTag}).`);
    return true;
  } catch (err) {
    showToast(err.message, true);
    return false;
  }
}

export async function rollbackPreviousVersion() {
  if (!store.CURRENT_STEM) return false;
  const target = computePrevRollbackTarget();
  return await restoreVersion(target ? target.version : null);
}

export async function approveAndExport(confirmWarnings = false) {
  if (!store.CURRENT_STEM) return;
  if (isStemBusy(store.CURRENT_STEM)) {
    showToast(`File ${store.CURRENT_STEM}.docx đang chạy hoặc đang chờ lượt AI, vui lòng đợi hoàn tất.`, true);
    return;
  }
  clearTimeout(checkTimer);
  const approvedStem = store.CURRENT_STEM;
  const prevPendingStems = pendingFiles().map((r) => r.file.replace(/\.docx$/i, ""));
  const prevIdx = prevPendingStems.indexOf(approvedStem);

  try {
    const data = await api(`/api/file/${encodeURIComponent(approvedStem)}/approve`, {
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
    await loadFileList(true);

    // Tự động chuyển sang file tiếp theo cần duyệt trong danh sách Chờ duyệt
    const nextPendingStems = pendingFiles()
      .map((r) => r.file.replace(/\.docx$/i, ""))
      .filter((s) => s !== approvedStem);

    let nextStem = null;
    if (nextPendingStems.length > 0) {
      const targetIdx = prevIdx >= 0 ? Math.min(prevIdx, nextPendingStems.length - 1) : 0;
      nextStem = nextPendingStems[targetIdx];
    } else {
      // Nếu bộ lọc trạng thái hiện tại hết file nhưng vẫn còn file chờ duyệt ở trạng thái khác
      const allUnapproved = sortFiles(store.FILES.filter((r) => !isApprovedStatus(r.status)))
        .map((r) => r.file.replace(/\.docx$/i, ""))
        .filter((s) => s !== approvedStem);
      if (allUnapproved.length > 0) {
        if ($("filter-status") && $("filter-status").value) {
          $("filter-status").value = "";
          renderFileList();
        }
        nextStem = allUnapproved[0];
      }
    }

    if (nextStem) {
      await selectFile(nextStem);
      const activeItem = document.querySelector("#file-list .file-item.active");
      if (activeItem) {
        activeItem.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      showToast(`Đã duyệt ${approvedStem}.docx → Chuyển sang ${nextStem}.docx`);
    } else {
      renderEditor(data.versions || []);
      showToast(`Đã duyệt và ghi: ${data.fill.output} (Đã hết file chờ duyệt!)`);
    }
  } catch (err) {
    showToast(err.message, true);
  }
}
