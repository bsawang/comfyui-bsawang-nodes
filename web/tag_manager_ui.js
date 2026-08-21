// Tag 管理器 — 两个节点真实版前端：
//   ① Tag_Reverse  「Tag 反推」  执行时 LLM 匹配 → matched + 建议（逐条采纳写持久层）→ 可存为预设
//   ② Tag_Library  「Tag 库编辑」 篮子/预设两层（横排 tab 编辑）：CRUD + 引导语 + tag 选择器，实时落盘
// 后端：GET /bsawang/tag/dict · POST save/delete_preset · POST add_basket_option（反推结果走节点输出直出）
import { app } from "/scripts/app.js";

const PANEL_WIDTH = 500;

function make(tag, css = {}, text = "") {
    const el = document.createElement(tag);
    Object.assign(el.style, css);
    if (text) el.textContent = text;
    return el;
}
function hideWidget(w) {
    if (!w) return;
    w.hidden = true;
    w.options = w.options || {};
    w.options.hidden = true;
    w.computeSize = () => [0, -4];
    w.serialize = true;
}
async function api(url, body) {
    const opts = body
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
        : {};
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
}
function injectStyle(root) {
    const style = make("style");
    style.textContent = `
      .btm-tabs{display:flex;flex-wrap:wrap;gap:3px;margin-bottom:5px}
      .btm-tab{padding:3px 10px;border-radius:8px 8px 0 0;cursor:pointer;font-size:11px;line-height:16px;border:1px solid #2d4255;background:#14202c;color:#9fb4c5}
      .btm-tab.active{background:#0aa4d6;border-color:#0aa4d6;color:#06131b;font-weight:600}
      .btm-tab-lg{padding:5px 18px;font-size:12px;font-weight:600}
      .btm-view{display:none}
      .btm-view.show{display:block}
      .btm-sec{margin:4px 0 2px;color:#7fb0c4;font-size:11px;font-weight:600}
      .btm-chips{display:flex;flex-wrap:wrap;gap:4px;padding:6px;background:transparent;margin-bottom:6px}
      .btm-chip{display:inline-block;padding:2px 9px;border-radius:10px;cursor:pointer;font-size:11px;line-height:16px;border:1px solid #3a5060;background:#1d2731;color:#c9d8e4}
      .btm-chip.on{background:#0aa4d6;border-color:#0aa4d6;color:#06131b;font-weight:600}
      .btm-sugg{display:flex;align-items:center;gap:6px;padding:4px 6px;border:1px solid #26333d;border-radius:7px;background:#101b26;margin-bottom:4px}
      .btm-sugg-tag{font-weight:600;color:#e1c88a;white-space:nowrap}
      .btm-sugg-reason{flex:1;color:#8fa3b2;font-size:11px;min-width:0;overflow:hidden;text-overflow:ellipsis}
      .btm-sugg-empty{color:#4a5863;font-size:11px;padding:4px}
      .btm-btn{padding:3px 12px;border-radius:7px;cursor:pointer;font-size:11px;line-height:16px;border:1px solid #3a5060;background:#14202c;color:#9fb4c5}
      .btm-btn:hover{border-color:#0aa4d6;background:#1a2a3a}
      .btm-btn.primary{background:#0aa4d6;border-color:#0aa4d6;color:#06131b;font-weight:600}
      .btm-icon-btn{padding:3px 6px;display:inline-flex;align-items:center;justify-content:center;line-height:0}
      .btm-btn.green{background:#3ba55d;border-color:#3ba55d;color:#fff}
      .btm-btn.red{background:#d64545;border-color:#d64545;color:#fff}
      .btm-input{width:150px;background:#0d141b;color:#d7e3ef;border:1px solid #3a5060;border-radius:6px;padding:5px 9px;font-size:11px}
      .btm-textarea{width:100%;box-sizing:border-box;background:#0d141b;color:#d7e3ef;border:1px solid #3a5060;border-radius:6px;padding:5px 9px;font-size:11px;font-family:inherit;resize:vertical;min-height:52px}
      .btm-row{display:flex;gap:6px;align-items:center;margin-bottom:6px}
      .btm-box{border-top:1px solid #2d4255;margin-top:6px;padding-top:6px;margin-bottom:6px}
      .btm-frame{padding:0;margin:0}
      .btm-frame > .btm-tabs{border-bottom:1px solid #2d4255;padding-bottom:5px;margin-bottom:6px}
      .btm-item{display:flex;align-items:center;gap:6px;padding:4px 6px;border:1px solid #26333d;border-radius:7px;background:#101b26;margin-bottom:4px}
      .btm-note{color:#7fb0c4;font-size:11px;margin:4px 0}
      .btm-status{color:#8fd0a0;font-size:11px;margin-top:4px;min-height:16px}
      .btm-tags{display:flex;flex-wrap:wrap;gap:4px}
    `;
    root.appendChild(style);
}
function findStateWidget(node) {
    let stateWidget = null;
    const keep = [];
    for (const w of node.widgets || []) {
        if (w.name === "bsawang_tag_state") { stateWidget = w; hideWidget(w); keep.push(w); }
        else if (w.name === "bsawang_tag_instance") { hideWidget(w); keep.push(w); }
        else keep.push(w);
    }
    node.widgets = keep;
    return stateWidget;
}

// ============================================================
// ① Tag_Reverse「Tag 反推」：两块布局（匹配结果+存预设 / 建议批准+全部采纳）
//    直出：节点执行 → 输出「反推结果」→ onExecuted / executed 事件 → 面板渲染。
//    不轮询、不读后端状态（中间节点，结果随工作流走）。
// ============================================================
function createReversePanel(node, nodeData) {
    if (typeof node.addDOMWidget !== "function") return false;
    const stateWidget = findStateWidget(node);
    if (!stateWidget) return false;

    function readState() {
        try { const v = JSON.parse(stateWidget.value || "{}"); return v && typeof v === "object" ? v : {}; }
        catch { return {}; }
    }
    let state = {};          // 匹配结果不跨刷新恢复：启动即空，只来自本次运行 applyResult / 交互
    stateWidget.value = "{}";
    let hasRunResult = false;  // 本次会话是否已运行过（运行后面板状态优先，不再被旧 widget 值覆盖）
    function persistState() { stateWidget.value = JSON.stringify(state); }
    function tagsOf(key) { return Array.isArray(state[key]) ? state[key].filter((t) => t) : []; }
    function setTags(key, tags) { state[key] = [...new Set(tags)]; persistState(); }

    let sugList = [];       // [{篮子, tag, reason}]
    let statusText = "";

    const root = make("div", {
        position: "relative", width: PANEL_WIDTH + "px", maxWidth: "100%",
        boxSizing: "border-box", color: "#d7e3ef", fontFamily: "Arial,sans-serif",
        fontSize: "12px", userSelect: "none", padding: "8px", overflow: "visible",
        border: "1px solid #2d4255", borderRadius: "8px", background: "#101b26",
    });
    injectStyle(root);

    // 信息栏：顶部（参考管理器）
    const statusIcon = make("span", {}, "ℹ"); statusIcon.style.cssText = "font-weight:600";
    const statusTextEl = make("span", { flex: "1" }, "就绪");
    const statusEl = make("div", {});
    statusEl.className = "btm-status";
    statusEl.style.cssText = "display:flex;align-items:center;gap:6px;border-bottom:1px solid #2d4255;padding-bottom:4px;margin-bottom:6px";
    statusEl.appendChild(statusIcon); statusEl.appendChild(statusTextEl);
    root.appendChild(statusEl);

    // ── 第一块：匹配结果 + 保存预设 ──
    const sec1 = make("div", {}, "匹配结果（来自反推，点击取消）"); sec1.className = "btm-sec";
    root.appendChild(sec1);
    const matchedBox = make("div", {}, ""); matchedBox.className = "btm-chips";
    const matchedSec = make("div", {}); matchedSec.className = "btm-box"; matchedSec.appendChild(matchedBox);
    root.appendChild(matchedSec);

    const saveRow = make("div", {}, ""); saveRow.className = "btm-row";
    const nameInput = make("input", {}, ""); nameInput.type = "text"; nameInput.placeholder = "预设名称"; nameInput.className = "btm-input";
    const btnSave = make("button", {}, "保存为预设"); btnSave.className = "btm-btn green";
    btnSave.addEventListener("click", async () => {
        const name = (nameInput.value || "").trim();
        if (!name) { statusText = "请先输入预设名称"; renderStatus(); return; }
        try {
            await api("/bsawang/tag/save_preset", { name, guidance: (guidIn.value || "").trim(), tags: JSON.parse(JSON.stringify(state)) });
            statusText = `已保存预设「${name}」→ presets/${name}.json`;
        } catch (e) { statusText = "保存失败：" + e.message; }
        nameInput.value = "";
        renderStatus();
    });
    saveRow.appendChild(nameInput); saveRow.appendChild(btnSave);
    root.appendChild(saveRow);

    // 引导词：VL 反推自动归纳，多行可编辑；保存预设时写入 guidance
    const guidSec = make("div", {}, "引导词（多行编辑，保存预设时写入）"); guidSec.className = "btm-sec";
    root.appendChild(guidSec);
    const guidIn = make("textarea", {}); guidIn.className = "btm-textarea"; guidIn.placeholder = "场景引导词（VL 反推自动归纳，可编辑）";
    root.appendChild(guidIn);

    // ── 分隔线 ──
    const divider = make("div", {}, "");
    divider.style.cssText = "border-top:1px solid #2d4255;margin:8px 0 6px";
    root.appendChild(divider);

    // ── 第二块：建议批准 + 全部采纳 ──
    const sec2 = make("div", {}, "tag 建议批准（采纳 = 写入篮子库 + 并入匹配）"); sec2.className = "btm-sec";
    root.appendChild(sec2);
    const suggBox = make("div", {}, "");
    const suggSec = make("div", {}); suggSec.className = "btm-box"; suggSec.appendChild(suggBox);
    root.appendChild(suggSec);
    const btnAll = make("button", {}, "全部采纳"); btnAll.className = "btm-btn primary";
    btnAll.addEventListener("click", async () => {
        for (const s of [...sugList]) {
            try { await api("/bsawang/tag/add_basket_option", { basket: s.篮子, tag: s.tag }); } catch (e) { /* 已存在等，忽略 */ }
            const cur = tagsOf(s.篮子);
            if (!cur.includes(s.tag)) cur.push(s.tag);
            setTags(s.篮子, cur);
        }
        sugList = [];
        statusText = "已全部采纳 → 写入篮子库并并入匹配";
        renderMatched(); renderSug(); renderStatus();
    });
    root.appendChild(btnAll);

    function renderMatched() {
        matchedBox.innerHTML = "";
        const keys = Object.keys(state);
        if (keys.length === 0) {
            matchedBox.appendChild(make("span", { color: "#4a5863" }, "无匹配 — 运行工作流触发反推"));
            return;
        }
        for (const k of keys) {
            for (const t of tagsOf(k)) {
                const chip = make("span", {}, `${k}>${t}`); chip.className = "btm-chip on";
                chip.title = "点击取消";
                chip.addEventListener("click", () => { setTags(k, tagsOf(k).filter((x) => x !== t)); renderMatched(); });
                matchedBox.appendChild(chip);
            }
        }
    }
    function renderSug() {
        suggBox.innerHTML = "";
        if (sugList.length === 0) {
            suggBox.appendChild(make("div", { color: "#4a5863" }, "— 无待采纳建议 —")).className = "btm-sugg-empty";
            return;
        }
        for (const s of sugList) {
            const row = make("div", {}, ""); row.className = "btm-sugg";
            const tagEl = make("span", { whiteSpace: "nowrap" }, `${s.篮子}>${s.tag}`); tagEl.className = "btm-chip";
            const reasonEl = make("span", {}, "↳ " + (s.reason || "")); reasonEl.className = "btm-sugg-reason";
            const btn = make("button", {}, "采纳"); btn.className = "btm-btn primary";
            btn.addEventListener("click", async () => {
                try { await api("/bsawang/tag/add_basket_option", { basket: s.篮子, tag: s.tag }); }
                catch (e) { statusText = "写入失败：" + e.message; renderStatus(); return; }
                const cur = tagsOf(s.篮子);
                if (!cur.includes(s.tag)) cur.push(s.tag);
                setTags(s.篮子, cur);
                sugList = sugList.filter((x) => x !== s);
                statusText = `采纳「${s.tag}」→ 写入篮子「${s.篮子}」`;
                renderMatched(); renderSug(); renderStatus();
            });
            row.appendChild(tagEl); row.appendChild(reasonEl); row.appendChild(btn);
            suggBox.appendChild(row);
        }
    }
    function renderStatus() { statusTextEl.textContent = statusText; }

    // 直出：节点输出的「反推结果」（完整 {matched, suggestions, 反推文字}）→ 渲染两块
    function applyResult(full) {
        hasRunResult = true;
        console.log("[bsawang] applyResult", !!full, "| guidIn?", !!guidIn);
        if (guidIn) guidIn.value = (full && full.引导词) || "";
        const matched = (full && full.matched) || {};
        for (const k of Object.keys(state)) if (!(k in matched)) delete state[k];
        for (const k of Object.keys(matched)) state[k] = matched[k];
        persistState();
        sugList = ((full && full.suggestions) || []).map((s) => ({ 篮子: s.篮子, tag: s.tag, reason: s.reason }));
        renderMatched(); renderSug();
        if (domWidget) domWidget.setSize?.();
    }
    node._bsawangApplyResult = applyResult;

    // 初始渲染（启动即空：匹配结果不跨刷新恢复）
    renderMatched(); renderSug();

    let domWidget = node.addDOMWidget("bsawang_tag_reverse_panel", "bsawang_tag_reverse_panel", root, {
        serialize: false, hideOnZoom: false,
    });
    domWidget.options = domWidget.options || {};
    domWidget.options.serialize = false;
    domWidget.options.getMinHeight = () => 0;
    domWidget.options.getHeight = () => "100%";

    // 系统提示词文件 widget 移到节点最底部（面板之后）——用户接受保存重载串位风险
    const spW = (node.widgets || []).find((w) => w.name === "系统提示词文件");
    if (spW) {
        node.widgets = node.widgets.filter((w) => w !== spW);
        node.widgets.push(spW);
    }

    function syncFromWidget() {
        // 已运行过：面板状态优先，不被旧 widget 值覆盖；未运行（跨刷新/加载）则清掉旧匹配
        if (!hasRunResult) {
            const v = (stateWidget.value || "").trim();
            if (v && v !== "{}") {
                stateWidget.value = "{}";
                state = {};
                renderMatched();
            }
        }
    }
    stateWidget.callback = syncFromWidget;
    setTimeout(syncFromWidget, 0);

    const baseComputeSize = node.computeSize.bind(node);
    node.computeSize = function (out) {
        const measured = baseComputeSize(out);
        measured[0] = PANEL_WIDTH;
        measured[1] = Math.max(measured[1] || 0, 60 + (root.offsetHeight || 140));
        return measured;
    };
    return true;
}
function createLibraryPanel(node, nodeData) {
    if (typeof node.addDOMWidget !== "function") return false;
    const stateWidget = findStateWidget(node);
    if (!stateWidget) return false;

    function readState() {
        try { const v = JSON.parse(stateWidget.value || "{}"); return v && typeof v === "object" ? v : {}; }
        catch { return {}; }
    }
    let state = readState();
    function persistState() { stateWidget.value = JSON.stringify(state); }
    function tagsOf(key) { return Array.isArray(state[key]) ? state[key].filter((t) => t) : []; }
    function setTags(key, tags) { state[key] = [...new Set(tags)]; persistState(); }

    let baskets = {};   // {key: {label, options, guidance, option_guidance, ...}}
    let presets = {};   // {key: {label, description, tags}}
    let statusText = "";
    const basketKeys = () => Object.keys(baskets);

    const root = make("div", {
        position: "relative", width: PANEL_WIDTH + "px", maxWidth: "100%",
        boxSizing: "border-box", color: "#d7e3ef", fontFamily: "Arial,sans-serif",
        fontSize: "12px", userSelect: "none", padding: "8px", overflow: "visible",
        border: "1px solid #2d4255", borderRadius: "8px", background: "#101b26",
    });
    injectStyle(root);

    // 信息栏：顶部，底部横线分隔（不单独加框）
    const statusIcon = make("span", {}, "ℹ"); statusIcon.style.cssText = "font-weight:600";
    const statusTextEl = make("span", { flex: "1" }, "就绪");
    const statusEl = make("div", {});
    statusEl.className = "btm-status";
    statusEl.style.cssText = "display:flex;align-items:center;gap:6px;border-bottom:1px solid #2d4255;padding-bottom:4px;margin-bottom:6px";
    statusEl.appendChild(statusIcon); statusEl.appendChild(statusTextEl);
    root.appendChild(statusEl);

    // 层切换：篮子 / 预设（btm-frame 整体边框，tab 行下方横线分隔；tab 用 btm-tab 样式）
    const layers = ["篮子", "预设"];
    const layerRow = make("div", {}); layerRow.className = "btm-tabs";
    const layerViews = {};
    let domWidget = null;
    const layerFrame = make("div", {}); layerFrame.className = "btm-frame";
    root.appendChild(layerFrame);
    layerFrame.appendChild(layerRow);
    layers.forEach((l, i) => {
        const span = make("span", {}, l); span.className = "btm-tab btm-tab-lg" + (i === 0 ? " active" : "");
        span.addEventListener("click", () => {
            layers.forEach((x, xi) => {
                layerRow.children[xi].classList.toggle("active", x === l);
                layerViews[x].classList.toggle("show", x === l);
            });
            if (domWidget) domWidget.setSize?.();
        });
        layerRow.appendChild(span);
        const lv = make("div", {}, ""); lv.className = "btm-view" + (i === 0 ? " show" : "");
        layerViews[l] = lv; layerFrame.appendChild(lv);
    });

    // 引导词编辑框去前缀（显示纯内容）；保存时拼接写入
    function stripBasketGuidance(text) {
        return String(text || "").replace(/^\*\*.*?\*\*\s*=\s*/, "");
    }
    function stripTagGuidance(text, tag) {
        const s = String(text || "");
        const esc = tag.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        return s.replace(new RegExp("^" + esc + "[:：]\\s*"), "");
    }

    function setStatus(msg, ok) {
        statusText = msg;
        statusTextEl.textContent = msg;
        statusIcon.textContent = ok === false ? "✗" : (ok ? "✓" : "ℹ");
        statusIcon.style.color = ok === false ? "#e06c75" : (ok ? "#8fd0a0" : "#7fb0c4");
    }
    async function refreshDict() {
        const dict = await api("/bsawang/tag/dict");
        baskets = dict.baskets || {};
        presets = dict.presets || {};
        renderBasketLayer(); renderPresetLayer();
        if (domWidget) domWidget.setSize?.();
    }
    const ICON_OK = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';
    const ICON_DEL = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>';
    function makeIconBtn(svg, title, action, confirmDel) {
        const b = make("button", {}); b.className = "btm-btn btm-icon-btn" + (confirmDel ? " red" : ""); b.innerHTML = svg; b.title = title;
        let armed = false;
        b.addEventListener("click", () => {
            if (confirmDel) {
                if (!armed) { armed = true; b.innerHTML = ICON_DEL; b.style.background = "#e06c75"; b.style.borderColor = "#e06c75"; setTimeout(() => { armed = false; b.innerHTML = svg; b.style.background = ""; b.style.borderColor = ""; }, 2000); return; }
            }
            action();
        });
        return b;
    }

    // ================= 篮子层 =================
    // basketTarget: {type:'edit', key} | {type:'add'}; tagTarget: {type:'edit', tag} | {type:'add'} | null
    let basketTarget = { type: "edit", key: null };
    let tagTarget = null;
    function renderBasketLayer() {
        const lv = layerViews["篮子"];
        lv.innerHTML = "";

        // 篮子横排 tab（显示 key；编辑/新增栏不显示 key，key 由 label 生成）
        const tabRow = make("div", {}, ""); tabRow.className = "btm-tabs";
        basketKeys().forEach((k) => {
            const tab = make("span", {}, k);
            tab.className = "btm-tab" + (basketTarget.type === "edit" && basketTarget.key === k ? " active" : "");
            tab.addEventListener("click", () => { basketTarget = { type: "edit", key: k }; tagTarget = null; renderBasketLayer(); });
            tabRow.appendChild(tab);
        });
        const addTab = make("span", {}, "+ 新增"); addTab.className = "btm-chip" + (basketTarget.type === "add" ? " on" : "");
        addTab.addEventListener("click", () => { basketTarget = { type: "add" }; tagTarget = null; renderBasketLayer(); });
        tabRow.appendChild(addTab);
        lv.appendChild(tabRow);

        // 编辑区：新增表单（key 由 label 自动生成）或 选中篮子的编辑
        if (basketTarget.type === "add") {
            const ar = make("div", {}); ar.className = "btm-row"; ar.style.cssText = "margin-bottom:6px";
            const aLabel = make("input", {}, ""); aLabel.type = "text"; aLabel.className = "btm-input"; aLabel.style.cssText = "flex:1"; aLabel.placeholder = "名称（key 自动生成）";
            const aOk = make("button", {}, "√"); aOk.className = "btm-btn btm-icon-btn green"; aOk.innerHTML = ICON_OK;
            aOk.addEventListener("click", async () => {
                const label = (aLabel.value || "").trim();
                if (!label) { setStatus("请填名称", false); return; }
                const key = label;   // key 由 label 自动生成
                const g = (aGuid.value || "").trim();
                try {
                    await api("/bsawang/tag/save_basket", { key, label, guidance: g ? `**${label}** = ${g}` : "" });
                    setStatus(`已建篮子「${key}」`, true); await refreshDict();
                    basketTarget = { type: "edit", key };
                } catch (e) { setStatus("失败：" + e.message, false); }
            });
            ar.appendChild(aLabel); ar.appendChild(aOk);
            const aGuid = make("textarea", {}); aGuid.className = "btm-textarea"; aGuid.placeholder = "引导语（多行）";
            const box = make("div", {}); box.className = "btm-box"; box.appendChild(ar); box.appendChild(aGuid);
            lv.appendChild(box);
        } else if (basketTarget.key && baskets[basketTarget.key]) {
            const k = basketTarget.key;
            const b = baskets[k] || {};
            const er = make("div", {}); er.className = "btm-row"; er.style.cssText = "margin-bottom:6px";
            const nameIn = make("input", {}, ""); nameIn.type = "text"; nameIn.className = "btm-input"; nameIn.style.cssText = "flex:1;min-width:100px"; nameIn.value = b.label || k;
            const btnSave = make("button", {}, "√"); btnSave.className = "btm-btn btm-icon-btn green"; btnSave.innerHTML = ICON_OK;
            btnSave.addEventListener("click", async () => {
                try {
                    const label = (nameIn.value || "").trim() || k;
                    const g = (guidIn.value || "").trim();
                    await api("/bsawang/tag/save_basket", { key: k, label, guidance: g ? `**${label}** = ${g}` : "" });
                    setStatus(`已存篮子「${k}」`, true); await refreshDict();
                } catch (e) { setStatus("失败：" + e.message, false); }
            });
            const btnDel = makeIconBtn(ICON_DEL, "删除", async () => {
                try { await api("/bsawang/tag/delete_basket", { key: k }); setStatus(`已删篮子「${k}」`, true); basketTarget = { type: "edit", key: null }; await refreshDict(); }
                catch (e) { setStatus("失败：" + e.message, false); }
            }, true);
            er.appendChild(nameIn); er.appendChild(btnSave); er.appendChild(btnDel);
            const guidIn = make("textarea", {}); guidIn.className = "btm-textarea"; guidIn.placeholder = "引导语（多行）"; guidIn.value = stripBasketGuidance((b.guidance || [])[0]);
            const box = make("div", {}); box.className = "btm-box"; box.appendChild(er); box.appendChild(guidIn);
            lv.appendChild(box);

            // tag 横排 tab + 编辑区（无 "tags" 标签）
            const tagRow = make("div", {}); tagRow.className = "btm-tabs";
            for (const t of (b.options || [])) {
                const tab = make("span", {}, t);
                tab.className = "btm-chip" + (tagTarget && tagTarget.type === "edit" && tagTarget.tag === t ? " on" : "");
                tab.addEventListener("click", () => { tagTarget = { type: "edit", tag: t }; renderBasketLayer(); });
                tagRow.appendChild(tab);
            }
            const addTagTab = make("span", {}, "+ 新增"); addTagTab.className = "btm-chip" + (tagTarget && tagTarget.type === "add" ? " on" : "");
            addTagTab.addEventListener("click", () => { tagTarget = { type: "add" }; renderBasketLayer(); });
            tagRow.appendChild(addTagTab);
            const tagBox = make("div", {}); tagBox.className = "btm-box"; tagBox.appendChild(tagRow);
            lv.appendChild(tagBox);

            // tag 编辑区
            if (tagTarget && tagTarget.type === "add") {
                const tar = make("div", {}); tar.className = "btm-row";
                const taName = make("input", {}, ""); taName.type = "text"; taName.className = "btm-input"; taName.style.cssText = "flex:1"; taName.placeholder = "名称";
                const taOk = make("button", {}, "√"); taOk.className = "btm-btn btm-icon-btn green"; taOk.innerHTML = ICON_OK;
                taOk.addEventListener("click", async () => {
                    const t = (taName.value || "").trim(); if (!t) { setStatus("请填名称", false); return; }
                    const g = (taGuid.value || "").trim();
                    try { await api("/bsawang/tag/save_option", { basket: k, tag: t, guidance: g ? `${t}：${g}` : "" }); setStatus(`已加 tag「${t}」`, true); await refreshDict(); tagTarget = { type: "edit", tag: t }; }
                    catch (e) { setStatus("失败：" + e.message, false); }
                });
                tar.appendChild(taName); tar.appendChild(taOk);
                tagBox.appendChild(tar);
                const taGuid = make("textarea", {}); taGuid.className = "btm-textarea"; taGuid.placeholder = "引导语（多行）";
                tagBox.appendChild(taGuid);
            } else if (tagTarget && tagTarget.type === "edit" && (b.options || []).includes(tagTarget.tag)) {
                const t = tagTarget.tag;
                const og = stripTagGuidance((b.option_guidance || {})[t], t);
                const tr = make("div", {}); tr.className = "btm-row";
                const tName = make("input", {}, ""); tName.type = "text"; tName.className = "btm-input"; tName.style.cssText = "flex:1;min-width:100px"; tName.value = t;
                const tSave = make("button", {}, "√"); tSave.className = "btm-btn btm-icon-btn green"; tSave.innerHTML = ICON_OK;
                tSave.addEventListener("click", async () => {
                    const newName = (tName.value || "").trim();
                    const g = (tGuid.value || "").trim();
                    try {
                        const gFull = g ? `${newName || t}：${g}` : "";
                        if (newName && newName !== t) {
                            await api("/bsawang/tag/save_option", { basket: k, tag: newName, guidance: gFull });
                            await api("/bsawang/tag/delete_option", { basket: k, tag: t });
                        } else {
                            await api("/bsawang/tag/save_option", { basket: k, tag: t, guidance: gFull });
                        }
                        setStatus(`已存 tag「${newName || t}」`, true); await refreshDict();
                    } catch (e) { setStatus("失败：" + e.message, false); }
                });
                const tDel = makeIconBtn(ICON_DEL, "删除", async () => {
                    try { await api("/bsawang/tag/delete_option", { basket: k, tag: t }); setStatus(`已删 tag「${t}」`, true); tagTarget = null; await refreshDict(); }
                    catch (e) { setStatus("失败：" + e.message, false); }
                }, true);
                tr.appendChild(tName); tr.appendChild(tSave); tr.appendChild(tDel);
                tagBox.appendChild(tr);
                const tGuid = make("textarea", {}); tGuid.className = "btm-textarea"; tGuid.placeholder = "引导语（多行）"; tGuid.value = og;
                tagBox.appendChild(tGuid);
            }
        } else {
            lv.appendChild(make("div", { color: "#4a5863" }, "点上方篮子 tab 编辑，或 + 新增"));
        }
    }

    // ================= 预设层 =================
    // presetTarget: {type:'edit', key} | {type:'add'}; selTagBasket: 每个预设选择器子 tab
    let presetTarget = { type: "edit", key: null };
    let selTagBasket = {};
    function renderPresetLayer() {
        const lv = layerViews["预设"];
        lv.innerHTML = "";

        const tabRow = make("div", {}); tabRow.className = "btm-tabs";
        Object.keys(presets).forEach((k) => {
            const tab = make("span", {}, k);
            tab.className = "btm-chip" + (presetTarget.type === "edit" && presetTarget.key === k ? " on" : "");
            tab.addEventListener("click", () => { presetTarget = { type: "edit", key: k }; renderPresetLayer(); });
            tabRow.appendChild(tab);
        });
        const addTab = make("span", {}, "+ 新增"); addTab.className = "btm-chip" + (presetTarget.type === "add" ? " on" : "");
        addTab.addEventListener("click", () => { presetTarget = { type: "add" }; renderPresetLayer(); });
        tabRow.appendChild(addTab);
        lv.appendChild(tabRow);

        if (presetTarget.type === "add") {
            const ar = make("div", {}); ar.className = "btm-row"; ar.style.cssText = "margin-bottom:6px";
            const aLabel = make("input", {}, ""); aLabel.type = "text"; aLabel.className = "btm-input"; aLabel.style.cssText = "flex:1"; aLabel.placeholder = "名称（key 自动生成）";
            const aOk = make("button", {}, "√"); aOk.className = "btm-btn btm-icon-btn green"; aOk.innerHTML = ICON_OK;
            aOk.addEventListener("click", async () => {
                const label = (aLabel.value || "").trim();
                if (!label) { setStatus("请填名称", false); return; }
                try {
                    await api("/bsawang/tag/save_preset", { name: label, label, guidance: aGuidance.value, tags: {} });
                    setStatus(`已建预设「${label}」`, true); await refreshDict();
                    presetTarget = { type: "edit", key: label };
                } catch (e) { setStatus("失败：" + e.message, false); }
            });
            ar.appendChild(aLabel); ar.appendChild(aOk);
            const aGuidance = make("textarea", {}); aGuidance.className = "btm-textarea"; aGuidance.placeholder = "引导词（多行）";
            lv.appendChild(ar);
            lv.appendChild(aGuidance);
        } else if (presetTarget.key && presets[presetTarget.key]) {
            const k = presetTarget.key;
            const p = presets[k] || {};
            const er = make("div", {}); er.className = "btm-row"; er.style.cssText = "margin-bottom:6px";
            const nameIn = make("input", {}, ""); nameIn.type = "text"; nameIn.className = "btm-input"; nameIn.style.cssText = "flex:1;min-width:100px"; nameIn.value = p.label || k;
            const btnSave = make("button", {}, "√"); btnSave.className = "btm-btn btm-icon-btn green"; btnSave.innerHTML = ICON_OK;
            btnSave.addEventListener("click", async () => {
                try { await api("/bsawang/tag/save_preset", { name: k, label: nameIn.value, guidance: guidIn.value, tags: p.tags || {} }); setStatus(`已存预设「${k}」`, true); await refreshDict(); }
                catch (e) { setStatus("失败：" + e.message, false); }
            });
            const btnDel = makeIconBtn(ICON_DEL, "删除", async () => {
                try { await api("/bsawang/tag/delete_preset", { name: k }); setStatus(`已删预设「${k}」`, true); presetTarget = { type: "edit", key: null }; await refreshDict(); }
                catch (e) { setStatus("失败：" + e.message, false); }
            }, true);
            er.appendChild(nameIn); er.appendChild(btnSave); er.appendChild(btnDel);
            lv.appendChild(er);
            const guidIn = make("textarea", {}); guidIn.className = "btm-textarea"; guidIn.placeholder = "引导词（多行）"; guidIn.value = p.guidance || "";
            lv.appendChild(guidIn);

            // tags（点击移除）
            const tagsBox = make("div", {}); tagsBox.className = "btm-chips";
            const pt = p.tags || {};
            let any = false;
            for (const bk of Object.keys(pt)) {
                for (const t of pt[bk]) {
                    any = true;
                    const chip = make("span", {}, `${bk}>${t}`); chip.className = "btm-chip on";
                    chip.title = "点击从预设移除";
                    chip.addEventListener("click", async () => {
                        const newTags = JSON.parse(JSON.stringify(p.tags || {}));
                        (newTags[bk] || []).splice(newTags[bk].indexOf(t), 1);
                        if (!newTags[bk].length) delete newTags[bk];
                        try { await api("/bsawang/tag/save_preset", { name: k, label: p.label, guidance: p.guidance, tags: newTags }); setStatus(`已移除「${bk}>${t}」`, true); await refreshDict(); }
                        catch (e) { setStatus("失败：" + e.message, false); }
                    });
                    tagsBox.appendChild(chip);
                }
            }
            if (!any) tagsBox.appendChild(make("span", { color: "#4a5863" }, "空"));
            const box = make("div", {}); box.className = "btm-box"; box.appendChild(tagsBox);
            lv.appendChild(box);

            // 选择器（子 tab + chips 点击追加；无文字标签，btm-box 分割线区块）
            const selSection = make("div", {}); selSection.className = "btm-box";
            const subTabRow = make("div", {}); subTabRow.className = "btm-tabs";
            const curSub = selTagBasket[k] || (basketKeys()[0] || "");
            basketKeys().forEach((bk) => {
                const st = make("span", {}, bk);
                st.className = "btm-chip" + (curSub === bk ? " on" : "");
                st.addEventListener("click", () => { selTagBasket[k] = bk; renderPresetLayer(); });
                subTabRow.appendChild(st);
            });
            selSection.appendChild(subTabRow);
            const selBox = make("div", {}); selBox.className = "btm-chips";
            const opts = baskets[curSub]?.options || [];
            let selAny = false;
            for (const t of opts) {
                if ((p.tags?.[curSub] || []).includes(t)) continue;
                selAny = true;
                const chip = make("span", {}, t); chip.className = "btm-chip";
                chip.title = `点击追加 ${curSub}>${t} 到预设`;
                chip.addEventListener("click", async () => {
                    const newTags = JSON.parse(JSON.stringify(p.tags || {}));
                    (newTags[curSub] = newTags[curSub] || []).push(t);
                    try { await api("/bsawang/tag/save_preset", { name: k, label: p.label, guidance: p.guidance, tags: newTags }); setStatus(`已追加「${curSub}>${t}」`, true); await refreshDict(); }
                    catch (e) { setStatus("失败：" + e.message, false); }
                });
                selBox.appendChild(chip);
            }
            if (!selAny) selBox.appendChild(make("span", { color: "#4a5863" }, "无可用 tag"));
            selSection.appendChild(selBox);
            lv.appendChild(selSection);
        } else {
            lv.appendChild(make("div", { color: "#4a5863" }, "点上方预设 tab 编辑，或 + 新增"));
        }
    }

    renderBasketLayer(); renderPresetLayer();

    async function loadFromBackend() {
        try {
            const dict = await api("/bsawang/tag/dict");
            baskets = dict.baskets || {};
            presets = dict.presets || {};
            if (!basketTarget.key) basketTarget = { type: "edit", key: basketKeys()[0] || null };
            if (!presetTarget.key) presetTarget = { type: "edit", key: Object.keys(presets)[0] || null };
            renderBasketLayer(); renderPresetLayer();
        } catch (e) {
            setStatus("后端加载失败：" + e.message, false);
        }
        if (domWidget) domWidget.setSize?.();
    }
    setTimeout(loadFromBackend, 0);

    domWidget = node.addDOMWidget("bsawang_tag_library_panel", "bsawang_tag_library_panel", root, {
        serialize: false, hideOnZoom: false,
    });
    domWidget.options = domWidget.options || {};
    domWidget.options.serialize = false;
    domWidget.options.getMinHeight = () => 0;
    domWidget.options.getHeight = () => "100%";

    function syncFromWidget() {
        const fresh = readState();
        if (JSON.stringify(fresh) !== JSON.stringify(state)) { state = fresh; }
    }
    stateWidget.callback = syncFromWidget;
    setTimeout(syncFromWidget, 0);

    const baseComputeSize = node.computeSize.bind(node);
    node.computeSize = function (out) {
        const measured = baseComputeSize(out);
        measured[0] = PANEL_WIDTH;
        const shown = layers.map((l) => layerViews[l]).find((v) => v.classList.contains("show"));
        measured[1] = Math.max(measured[1] || 0, 60 + (shown ? shown.offsetHeight : 100));
        return measured;
    };
    return true;
}
function extractFullResult(output) {
    if (!output) return null;
    let s = null;
    if (Array.isArray(output)) s = output[1];
    else if (output.text != null) s = Array.isArray(output.text) ? output.text[0] : output.text;
    else s = output["反推结果"];
    if (!s) return null;
    try { return JSON.parse(s); } catch (e) { return null; }
}

// executed 事件只发给「显示节点」（PreviewAny 等），中间节点不触发。
// 从显示节点沿输入连线递归回溯，找到挂了反推面板的 Tag_Reverse 节点。
function findUpstreamTagReverse(node, visited) {
    if (!node) return null;
    if (node._bsawangApplyResult) return node;
    visited = visited || new Set();
    if (visited.has(node.id)) return null;
    visited.add(node.id);
    for (const input of node.inputs || []) {
        const link = app.graph?.links?.[input.link];
        if (!link) continue;
        const src = app.graph?.getNodeById?.(link.origin_id)
            || app.graph?.nodes?.find?.((n) => String(n.id) === String(link.origin_id));
        const found = findUpstreamTagReverse(src, visited);
        if (found) return found;
    }
    return null;
}

app.registerExtension({
    name: "bsawang.tag_manager",
    async setup() {
        // 直出：任何节点的 executed 事件 → 从该节点回溯找 Tag_Reverse → 用其输出渲染面板
        const api = app.api || window.comfyAPI?.api;
        api?.addEventListener?.("executed", ({ detail }) => {
            const nid = detail?.node ?? detail?.node_id;
            if (nid == null) return;
            const node = app.graph?.getNodeById?.(nid)
                || app.graph?.nodes?.find?.((n) => String(n.id) === String(nid));
            if (!node) return;
            const revNode = findUpstreamTagReverse(node);
            console.log("[bsawang] executed", nid, "| 节点", node?.type, "| 上游反推?", !!revNode);
            if (!revNode) return;
            const full = extractFullResult(detail?.output ?? detail?.executed);
            console.log("[bsawang] full?", !!full, full && Object.keys(full));
            if (full) revNode._bsawangApplyResult(full);
        });
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        const name = nodeData.name;
        if (name === "Tag_Reverse") {
            const prev = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = prev?.apply(this, arguments);
                if (!this._bsawangTagRevReady) this._bsawangTagRevReady = createReversePanel(this, nodeData) === true;
                // 兜底：onExecuted 也触发一次（与 executed 事件双保险）
                const prevExec = this.onExecuted;
                this.onExecuted = function (message) {
                    if (typeof prevExec === "function") prevExec.apply(this, arguments);
                    const full = this._bsawangApplyResult && extractFullResult(message);
                    if (full) this._bsawangApplyResult(full);
                };
                return r;
            };
        } else if (name === "Tag_Library") {
            const prev = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = prev?.apply(this, arguments);
                if (!this._bsawangTagLibReady) this._bsawangTagLibReady = createLibraryPanel(this, nodeData) === true;
                return r;
            };
        }
    },
});
