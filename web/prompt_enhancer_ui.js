// 提示词增强器 — 内嵌 DOM 面板（分类 Tab + tag 篮子多选 + 已选汇总框）
// 框架参考 Goohai-MiniMax-H3_Integration（beforeRegisterNodeDef + addDOMWidget 内嵌面板，非弹窗）。
// 状态存储：隐藏 bsawang_basket_state widget（JSON：{字段key: [tag]}），唯一序列化点；
//           basket 原生 widget 不生成（后端已改），splice 掉历史残留，节点高度只由可见 widget + 面板决定。
import { app } from "/scripts/app.js";

const NODE = "Prompt_Enhancer";
const PANEL_WIDTH = 500;

// ---------- 工具 ----------
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

// ---------- LLM token 用量显示（增强器面板信息栏展示，只显示本次，轻量） ----------
function fmtTokens(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "K";
    return String(n);
}

// ---------- 面板构建 ----------
function createPanel(node, nodeData) {
    if (typeof node.addDOMWidget !== "function") return false;

    // 篮子 meta 来自 state widget 的 bsawang.basketMeta（后端注入 options/互斥/冲突/condition/tasks）
    const required = nodeData?.input?.required || {};
    const stateDef = required["bsawang_basket_state"];
    let metaMap = stateDef?.[1]?.["bsawang.basketMeta"] || {};
    let basketKeys = Object.keys(metaMap);
    let presets = {};      // 预设池 {key: {label, description, tags}}，来自 /dict 接口
    let presetView = null; // 「预设」tab 的容器（index 0，独立于 basketEls）
    if (basketKeys.length === 0) return false;

    // 任务类型 widget（原生 combo）：监听变化，按 meta.tasks 动态显隐分类 tab
    const taskWidget = (node.widgets || []).find((w) => w.name === "任务类型");
    const currentTask = () => (taskWidget ? String(taskWidget.value || "") : "");

    // 找 state widget（存 JSON），splice 掉所有非必要的 basket 原生 widget 残留（旧工作流）
    let stateWidget = null;
    const keepWidgets = [];
    for (const w of node.widgets || []) {
        if (w.name === "bsawang_basket_state") {
            stateWidget = w;
            hideWidget(w); // 隐藏 state widget，不占空间但保留序列化
            keepWidgets.push(w);
        } else if (basketKeys.includes(w.name)) {
            // 旧版本残留的 basket STRING widget：隐藏并从数组移除（不占空间、不序列化）
            w.hidden = true;
        } else {
            keepWidgets.push(w);
        }
    }
    node.widgets = keepWidgets;
    if (!stateWidget) return false;

    // 解析状态：{字段key: [tag]}
    function readState() {
        try {
            const v = JSON.parse(stateWidget.value || "{}");
            return v && typeof v === "object" ? v : {};
        } catch { return {}; }
    }
    let state = readState();
    function persistState() {
        stateWidget.value = JSON.stringify(state);
    }
    function tagsOf(key) {
        const v = state[key];
        return Array.isArray(v) ? v.filter((t) => t && t !== "未设置") : [];
    }
    function setTags(key, tags) {
        state[key] = [...new Set(tags)];
        persistState();
    }

    // 清空所有篮子
    function clearBaskets() {
        state = {};
        persistState();
        for (const key of basketKeys) {
            const bi = basketKeys.indexOf(key);
            if (bi >= 0 && basketRefreshers[bi]) basketRefreshers[bi]();
        }
        renderSummary();
    }

    // 随机填充所有篮子：每个篮子随机选 1 个 tag
    function randomBaskets() {
        const rnd = (n) => Math.floor(Math.random() * n);
        for (const key of basketKeys) {
            const options = metaMap[key]?.options || [];
            if (options.length === 0) continue;
            state[key] = [options[rnd(options.length)]];
        }
        persistState();
        for (const key of basketKeys) {
            const bi = basketKeys.indexOf(key);
            if (bi >= 0 && basketRefreshers[bi]) basketRefreshers[bi]();
        }
        renderSummary();
    }

    const root = make("div", {
        position: "relative", width: `${PANEL_WIDTH}px`, maxWidth: "100%",
        boxSizing: "border-box", color: "#d7e3ef", fontFamily: "Arial,sans-serif",
        fontSize: "12px", userSelect: "none", padding: "8px", overflow: "visible",
        border: "1px solid #2d4255", borderRadius: "8px", background: "#101b26",
    });

    const style = make("style");
    style.textContent = `
      .bsa-tabs{display:flex;flex-wrap:wrap;gap:3px;margin-bottom:5px}
      .bsa-tab{padding:3px 10px;border-radius:8px 8px 0 0;cursor:pointer;font-size:11px;line-height:16px;border:1px solid #2d4255;background:#14202c;color:#9fb4c5;transition:background .12s}
      .bsa-tab:hover{border-color:#0aa4d6}
      .bsa-tab.active{background:#0aa4d6;border-color:#0aa4d6;color:#06131b;font-weight:600}
      .bsa-basket{display:none;flex-wrap:wrap;gap:4px;padding:6px;border-top:1px solid #2d4255;margin-top:6px;padding-top:6px}
      .bsa-basket.show{display:flex}
      .bsa-chip{display:inline-block;padding:2px 9px;border-radius:10px;cursor:pointer;font-size:11px;line-height:16px;border:1px solid #3a5060;background:#1d2731;color:#c9d8e4;transition:background .12s}
      .bsa-chip:hover{border-color:#0aa4d6}
      .bsa-chip.on{background:#0aa4d6;border-color:#0aa4d6;color:#06131b;font-weight:600}
      .bsa-chip.disabled{background:#0d141b;border-color:#26333d;color:#4a5863;cursor:not-allowed;pointer-events:none}
      .bsa-btns{display:flex;gap:6px;margin-bottom:4px}
      .bsa-btn{padding:3px 12px;border-radius:7px;cursor:pointer;font-size:11px;line-height:16px;border:1px solid #3a5060;background:#14202c;color:#9fb4c5;transition:background .12s;display:inline-flex;align-items:center;gap:4px}
      .bsa-btn:hover{border-color:#0aa4d6;background:#1a2a3a;color:#e1e9ef}
      .bsa-summary{margin-top:5px;padding:5px 7px;min-height:20px;line-height:1.5;display:flex;flex-wrap:wrap;gap:4px;align-items:center;border-top:1px solid #2d4255;margin-top:6px;padding-top:6px}
      .bsa-sumtag{display:inline-block;padding:1px 8px;border-radius:9px;font-size:10px;line-height:15px;background:#0aa4d6;color:#06131b;border:1px solid #0aa4d6;cursor:pointer}
      .bsa-empty{color:#4a5863;font-size:11px}
    `;
    root.appendChild(style);

    // 信息栏：顶部（图标 + 文本 + 底部横线）
    const statusIcon = make("span", {}, "ℹ"); statusIcon.style.cssText = "font-weight:600";
    const statusTextEl = make("span", { flex: "1" }, "就绪");
    const statusEl = make("div", {});
    statusEl.className = "bsa-status";
    statusEl.style.cssText = "display:flex;align-items:center;gap:6px;border-bottom:1px solid #2d4255;padding-bottom:4px;margin-bottom:6px;color:#8fd0a0;font-size:11px";
    statusEl.appendChild(statusIcon); statusEl.appendChild(statusTextEl);
    root.appendChild(statusEl);
    function setStatus(msg, ok) {
        statusTextEl.textContent = msg;
        statusIcon.textContent = ok === false ? "✗" : (ok ? "✓" : "ℹ");
        statusIcon.style.color = ok === false ? "#e06c75" : (ok ? "#8fd0a0" : "#7fb0c4");
    }
    // LLM 调用完成后：从后端读本次 token，展示在信息栏（报错态优先保留，不覆盖）
    function showUsage() {
        fetch("/llm_usage/last").then(r => r.json()).then(u => {
            if (!u || statusIcon.textContent === "✗") return; // 报错优先，不覆盖
            const tin = Number(u["本次输入token"]) || 0;
            const tout = Number(u["本次输出token"]) || 0;
            if (tin === 0 && tout === 0) return;
            const cin = Number(u["累计输入token"]) || 0;
            const cout = Number(u["累计输出token"]) || 0;
            setStatus(`↑ ${fmtTokens(tin)} ↓ ${fmtTokens(tout)} | ↑ ${fmtTokens(cin)} ↓ ${fmtTokens(cout)}`, true);
        }).catch(() => {}); // 用量非关键：读取失败静默
    }
    node._bsawangShowUsage = showUsage;

    // 前端组合自检：与后端 _collect_basket_errors 同规则（互斥/冲突），出错显示在信息栏
    function validateState() {
        const errs = [];
        for (const key of basketKeys) {
            const tags = tagsOf(key);
            if (!tags.length) continue;
            const meta = metaMap[key] || {};
            if (meta.mutually_exclusive && tags.length > 1)
                errs.push(`[${key}] 互斥：一次只能选一个，当前选了 ${tags.length} 个：${tags.join("、")}`);
            for (const pair of (meta.conflicts || [])) {
                if (tags.includes(pair[0]) && tags.includes(pair[1]))
                    errs.push(`[${key}] 冲突：『${pair[0]}』与『${pair[1]}』不能同时选`);
            }
        }
        return errs;
    }
    function reportValidation() {
        const errs = validateState();
        if (errs.length) {
            setStatus(errs.join("　"), false);
            return errs;
        }
        // 报错已解除（✗ 状态）→ 回中性「就绪」，否则保留原状态（成功消息等）
        if (statusIcon.textContent === "✗") setStatus("就绪");
        return [];
    }

    // 清空/随机按钮行
    const btnRow = make("div", {}, "");
    btnRow.className = "bsa-btns";
    const ICON_CLEAR = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>';
    const ICON_RANDOM = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/></svg>';
    const ICON_REFRESH = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 11-2.64-6.36M21 3v6h-6"/></svg>';
    const btnClear = make("button", {}); btnClear.innerHTML = ICON_CLEAR + " 清空";
    btnClear.className = "bsa-btn";
    btnClear.addEventListener("click", () => { clearBaskets(); setStatus("已清空所有篮子", true); });
    const btnRandom = make("button", {}); btnRandom.innerHTML = ICON_RANDOM + " 随机";
    btnRandom.className = "bsa-btn";
    btnRandom.addEventListener("click", () => { randomBaskets(); setStatus("已随机填充篮子", true); });
    btnRow.appendChild(btnClear);
    btnRow.appendChild(btnRandom);
    // 刷新字典：重读 baskets/ + dict.json，重建篮子 tab/chips（无需重启）
    async function refreshDict() {
        const res = await fetch("/bsawang/prompt_enhancer/dict");
        const data = await res.json();
        if (!data || !data.basket_meta) throw new Error("响应缺少 basket_meta");
        metaMap = data.basket_meta;
        presets = data.presets || {};
        renderBaskets();
        setStatus("字典已刷新", true);
    }
    const btnRefresh = make("button", {}); btnRefresh.innerHTML = ICON_REFRESH + " 刷新";
    btnRefresh.className = "bsa-btn";
    btnRefresh.title = "重读 baskets/ 与 dict.json，刷新篮子选项（无需重启）";
    btnRefresh.addEventListener("click", async () => {
        try { await refreshDict(); }
        catch (e) { setStatus("刷新字典失败：" + e.message, false); }
    });
    btnRow.appendChild(btnRefresh);
    root.appendChild(btnRow);

    // tag 库自动同步：后端 WebSocket 推送通知，收到即刷新（非轮询）
    app.api?.addEventListener?.("bsawang/tag_lib_changed", async () => {
        try { await refreshDict(); }
        catch (e) { setStatus("库自动同步失败：" + e.message, false); }
    });

    // 按 meta 顺序构建篮子（Tab 行 + 分类 chip）
    const tabRow = make("div", {}, "");
    tabRow.className = "bsa-tabs";
    let basketEls = [];
    let basketRefreshers = [];
    let tabs = [];
    let activeIdx = 0;
    let domWidget = null;

    const summaryEl = make("div", {}, "");
    summaryEl.className = "bsa-summary";

    function renderSummary() {
        const tags = [];
        for (const key of basketKeys) tags.push(...tagsOf(key));
        summaryEl.innerHTML = "";
        if (tags.length === 0) {
            summaryEl.innerHTML = '<span class="bsa-empty">—</span>';
            return;
        }
        for (const t of tags) {
            const chip = make("span", {}, t);
            chip.className = "bsa-sumtag";
            chip.title = "点击反选";
            chip.addEventListener("click", () => {
                for (const key of basketKeys) {
                    if (tagsOf(key).includes(t)) {
                        setTags(key, tagsOf(key).filter((x) => x !== t));
                        const bi = basketKeys.indexOf(key);
                        if (bi >= 0 && basketRefreshers[bi]) basketRefreshers[bi]();
                    }
                }
                renderSummary();
            });
            summaryEl.appendChild(chip);
        }
    }

    // 按当前任务类型过滤分类 tab：meta.tasks 为空=全任务适用；否则仅在适用任务显示
    function isTaskVisible(key) {
        const tasks = metaMap[key]?.tasks;
        if (!tasks || tasks.length === 0) return true;
        const cur = currentTask();
        return tasks.includes(cur);
    }
    function applyTaskFilter() {
        tabs.forEach((t, i) => {
            if (i === 0) { t.style.display = ""; return; }  // 「预设」tab 始终可见
            const visible = isTaskVisible(basketKeys[i - 1]);
            t.style.display = visible ? "" : "none";
            basketEls[i - 1].style.display = visible ? "" : "none";
        });
        // 当前激活的 tab 若被隐藏，切到第一个可见的
        if (activeIdx > 0 && !isTaskVisible(basketKeys[activeIdx - 1])) {
            const firstVisible = basketKeys.findIndex((k) => isTaskVisible(k));
            if (firstVisible >= 0) switchTab(firstVisible + 1);
            else switchTab(0);
        }
        if (domWidget) domWidget.setSize?.();
    }

    let switchTab = function (idx) {
        activeIdx = idx;
        tabs.forEach((t, i) => t.classList.toggle("active", i === idx));
        if (idx === 0) {
            if (presetView) presetView.classList.add("show");
            basketEls.forEach((el) => el.classList.remove("show"));
        } else {
            if (presetView) presetView.classList.remove("show");
            basketEls.forEach((el, i) => el.classList.toggle("show", i + 1 === idx));
        }
        if (domWidget) domWidget.setSize?.();
    };

    // 渲染「预设」tab 的 chips（点预设 → 覆盖所涉篮子）
    function renderPresetChips() {
        if (!presetView) return;
        presetView.innerHTML = "";
        const names = Object.keys(presets);
        if (names.length === 0) {
            const empty = make("span", {}, "无预设 — 用「Tag 反推 / Tag 库编辑」存预设，点此应用（覆盖所涉篮子）");
            empty.className = "bsa-empty";
            presetView.appendChild(empty);
            return;
        }
        for (const n of names) {
            const p = presets[n];
            const chip = make("span", {}, n);
            chip.className = "bsa-chip";
            chip.title = p.guidance ? p.guidance.slice(0, 60) + "…" : `应用「${n}」：覆盖所涉篮子（可再微调）`;
            chip.addEventListener("click", () => {
                const tags = p.tags || {};
                for (const key of Object.keys(tags)) {
                    const bi = basketKeys.indexOf(key);
                    if (bi >= 0) {
                        setTags(key, tags[key]);
                        if (basketRefreshers[bi]) basketRefreshers[bi]();
                    }
                }
                // 预设引导语注入 system prompt（存进 state 隐藏字段，enhance 时读取）
                if (p.guidance) state["__preset_guidance"] = p.guidance;
                else delete state["__preset_guidance"];
                persistState();
                renderSummary();
                // 组合自检：预设若含互斥/冲突组合，出错显示在信息栏（不自动改，用户手动解除）
                const errs = reportValidation();
                if (!errs.length) setStatus(`已应用预设「${n}」`, true);
            });
            presetView.appendChild(chip);
        }
    }

    // 按当前 metaMap 构建/重建篮子（tab 行 + chips）；「预设」tab 恒为 index 0 ——「刷新字典」按钮重调
    function renderBaskets() {
        tabRow.innerHTML = "";
        for (const el of basketEls) el.remove();
        if (presetView) presetView.remove();
        basketEls = [];
        basketRefreshers = [];
        tabs = [];
        activeIdx = 0;
        basketKeys = Object.keys(metaMap);

        // 「预设」tab（index 0，始终可见）
        const presetTab = make("span", {}, "预设");
        presetTab.className = "bsa-tab";
        presetTab.title = "一键应用预设（覆盖所涉篮子，可再微调）";
        presetTab.addEventListener("click", () => switchTab(0));
        tabs.push(presetTab);
        tabRow.appendChild(presetTab);
        presetView = make("div", {}, "");
        presetView.className = "bsa-basket" + (basketKeys.length === 0 ? " show" : "");
        renderPresetChips();
        root.insertBefore(presetView, summaryEl);

        if (basketKeys.length === 0) {
            renderSummary();
            if (domWidget) domWidget.setSize?.();
            return;
        }
        basketKeys.forEach((key, i) => {
            const meta = metaMap[key];
            const options = meta.options || [];
            // tab（index = i + 1，0 被「预设」占用）
            const tab = make("span", {}, key);
            tab.className = "bsa-tab" + (i === 0 ? " active" : "");
            tab.title = "点击切换";
            tab.addEventListener("click", () => switchTab(i + 1));
            tabs.push(tab);
            tabRow.appendChild(tab);

            // 篮子容器
            const box = make("div", {}, "");
            box.className = "bsa-basket" + (i === 0 ? " show" : "");
            basketRefreshers[i] = function () {
                box.querySelectorAll(".bsa-chip").forEach((c) => c.remove());
                const selected = tagsOf(key);
                const disabled = new Set();
                if (meta.mutually_exclusive && selected.length > 0) {
                    for (const opt of options) {
                        if (!selected.includes(opt)) disabled.add(opt);
                    }
                }
                for (const pair of (meta.conflicts || [])) {
                    if (selected.includes(pair[0])) disabled.add(pair[1]);
                    if (selected.includes(pair[1])) disabled.add(pair[0]);
                }
                for (const opt of options) {
                    const isOn = selected.includes(opt);
                    const isDisabled = disabled.has(opt) && !isOn;
                    const chip = make("span", {}, opt);
                    chip.className = "bsa-chip" + (isOn ? " on" : "") + (isDisabled ? " disabled" : "");
                    chip.title = isDisabled ? "与已选 tag 冲突，禁选" : (isOn ? "点击取消" : "点击选择");
                    chip.addEventListener("click", () => {
                        if (isDisabled) return;
                        const cur = tagsOf(key);
                        const idx = cur.indexOf(opt);
                        if (idx >= 0) cur.splice(idx, 1);
                        else cur.push(opt);
                        setTags(key, cur);
                        basketRefreshers[i]();
                        renderSummary();
                        reportValidation(); // 手动改完即复查：解除报错后信息栏回「就绪」
                    });
                    box.appendChild(chip);
                }
            };
            basketRefreshers[i]();
            root.insertBefore(box, summaryEl);
            basketEls.push(box);
        });
        applyTaskFilter();
        renderSummary();
        if (domWidget) domWidget.setSize?.();
    }

    // 统一按 tab 行 → 篮子容器 → 汇总框 顺序 append
    root.appendChild(tabRow);
    root.appendChild(summaryEl);
    renderBaskets();
    // 异步拉取预设池（初始 metaMap 来自 widget；预设需走 /dict 接口）
    fetch("/bsawang/prompt_enhancer/dict").then((r) => r.json()).then((d) => {
        if (d && d.presets) {
            presets = d.presets;
            renderPresetChips();
            if (domWidget) domWidget.setSize?.();
        }
    }).catch(() => {});

    // 重新同步：ComfyUI 在节点创建（onNodeCreated）之后才应用保存的 widget 值，
    // 面板初建时读到的可能是默认 "{}"，值被应用后需重建 chips（覆盖切换工作流/重开清空问题）
    function syncFromWidget() {
        const fresh = readState();
        if (JSON.stringify(fresh) !== JSON.stringify(state)) {
            state = fresh;
            basketRefreshers.forEach((fn) => fn());
            renderSummary();
            reportValidation(); // 载入工作流若带坏状态（旧预设残留等），信息栏即时提示
        }
    }
    // 1) ComfyUI 按名赋值 widget 时会触发 callback
    stateWidget.callback = syncFromWidget;
    // 2) 兜底：onNodeCreated 后延迟一拍再同步（覆盖不触发 callback 的赋值路径）
    setTimeout(syncFromWidget, 0);

    domWidget = node.addDOMWidget("bsawang_basket_panel", "bsawang_basket_panel", root, {
        serialize: false,
        hideOnZoom: false,
    });
    domWidget.options = domWidget.options || {};
    domWidget.options.serialize = false;
    domWidget.options.getMinHeight = () => 0;
    domWidget.options.getHeight = () => "100%";

    // 把面板 widget 移到「用户提示词」后面（自定义控件紧跟用户输入，篮子面板在文本输入之后）
    const ws = node.widgets;
    const panelIdx = ws.indexOf(domWidget);
    const promptIdx = ws.findIndex((w) => w.name === "用户提示词");
    if (panelIdx >= 0 && promptIdx >= 0) {
        ws.splice(panelIdx, 1);
        ws.splice(promptIdx + 1, 0, domWidget);
    }

    // 监听任务类型变化 → 动态显隐分类 tab；初始应用一次
    if (taskWidget) {
        taskWidget.callback = function () {
            applyTaskFilter();
        };
    }
    applyTaskFilter();

    // 高度自适应：tab 行 + 当前篮子 chip 行数 + 汇总框；隐藏 widget 已 splice 不占空间
    const baseComputeSize = node.computeSize.bind(node);
    node.computeSize = function (out) {
        const measured = baseComputeSize(out);
        measured[0] = PANEL_WIDTH;
        const visible = activeIdx === 0 ? presetView : basketEls[activeIdx - 1];
        const chipCount = visible ? visible.querySelectorAll(".bsa-chip").length : 0;
        const rows = Math.max(1, Math.ceil(chipCount / 6));
        measured[1] = Math.max(measured[1] || 0, 30 + rows * 22 + 34);
        return measured;
    };
    return true;
}

// H3 格式化节点：原无面板，补最小信息栏展示 token 用量（与增强器信息栏同风格）
function createH3Panel(node) {
    if (typeof node.addDOMWidget !== "function") return false;
    const root = make("div", {
        position: "relative", width: "100%", boxSizing: "border-box",
        color: "#d7e3ef", fontFamily: "Arial,sans-serif", fontSize: "12px",
        padding: "6px 8px", border: "1px solid #2d4255", borderRadius: "8px", background: "#101b26",
    });
    const statusIcon = make("span", {}, "ℹ"); statusIcon.style.cssText = "font-weight:600";
    const statusTextEl = make("span", { flex: "1" }, "就绪");
    const statusEl = make("div", {});
    statusEl.style.cssText = "display:flex;align-items:center;gap:6px;color:#7fb0c4;font-size:11px";
    statusEl.appendChild(statusIcon); statusEl.appendChild(statusTextEl);
    root.appendChild(statusEl);
    function showUsage() {
        fetch("/llm_usage/last").then(r => r.json()).then(u => {
            if (!u) return;
            const tin = Number(u["本次输入token"]) || 0;
            const tout = Number(u["本次输出token"]) || 0;
            if (tin === 0 && tout === 0) return;
            const cin = Number(u["累计输入token"]) || 0;
            const cout = Number(u["累计输出token"]) || 0;
            statusTextEl.textContent = `↑ ${fmtTokens(tin)} ↓ ${fmtTokens(tout)} | ↑ ${fmtTokens(cin)} ↓ ${fmtTokens(cout)}`;
            statusIcon.textContent = "✓";
            statusIcon.style.color = "#8fd0a0";
        }).catch(() => {}); // 用量非关键：读取失败静默
    }
    node._bsawangShowUsage = showUsage;
    node.addDOMWidget("bsawang_h3_usage", "bsawang_h3_usage", root, { serialize: false, hideOnZoom: false });
    return true;
}

// 从显示节点沿输入连线回溯，找挂了 token 展示的消费节点（增强器/H3）
function findUpstreamUsageNode(node, visited) {
    if (!node) return null;
    if (typeof node._bsawangShowUsage === "function") return node;
    visited = visited || new Set();
    if (visited.has(node.id)) return null;
    visited.add(node.id);
    for (const input of node.inputs || []) {
        const link = app.graph?.links?.[input.link];
        if (!link) continue;
        const src = app.graph?.getNodeById?.(link.origin_id)
            || app.graph?.nodes?.find?.((n) => String(n.id) === String(link.origin_id));
        const found = findUpstreamUsageNode(src, visited);
        if (found) return found;
    }
    return null;
}

// ---------- 素材口自增长 ----------
// 只显示「已连接的素材口 + 下一个空口」；连上后自动补下一个（后端已声明 素材1..素材16）
// 安全策略：只移除尾部未连接的口（其后无已连接，不破坏连线索引）
function setupMaterialPorts(node) {
    if (node._bsawangMatInited) return;
    node._bsawangMatInited = true;
    const CAP = 16;
    const isMat = (n) => n && /^素材\d+$/.test(n.name);
    const matNum = (n) => parseInt(n.name.replace("素材", ""), 10);
    function sync() {
        if (!node.inputs) return;
        let maxConnected = 0;
        for (const inp of node.inputs) if (isMat(inp) && inp.link != null) maxConnected = Math.max(maxConnected, matNum(inp));
        const maxVisible = Math.min(maxConnected + 1, CAP);
        // 移除尾部未连接的素材口（安全：仅动 >maxVisible 的，其后必无已连接）
        node.inputs = node.inputs.filter((inp) => !isMat(inp) || matNum(inp) <= maxVisible);
        // 补足 1..maxVisible 缺失的口（addInput 追加到末尾，保持素材口顺序在尾部）
        for (let i = 1; i <= maxVisible; i++) {
            if (!node.inputs.some((inp) => inp.name === `素材${i}`)) {
                try { node.addInput(`素材${i}`, "MATERIAL", null, { optional: true }); } catch (e) { /* 忽略 */ }
            }
        }
        node.setDirtyCanvas?.(true, true);
    }
    node._bsawangMatSync = sync;
    // 初始：工作流加载后连线已恢复时再同步（onConnectionsChange 加载期也会触发）
    setTimeout(sync, 50);
    const prevConn = node.onConnectionsChange;
    node.onConnectionsChange = function (type, index, connected, link) {
        if (typeof prevConn === "function") prevConn.apply(this, arguments);
        if (type === LiteGraph.INPUT && node._bsawangMatSync) node._bsawangMatSync();
    };
}

// ---------- 注册 ----------
app.registerExtension({
    name: "bsawang.prompt_enhancer",
    async setup() {
        // executed 事件只发显示节点（PreviewAny 等），中间节点（增强器/H3）不触发——
        // 从显示节点回溯上游找挂 token 展示的消费节点（与 Tag_Reverse 同款方案）
        const api = app.api || window.comfyAPI?.api;
        api?.addEventListener?.("executed", ({ detail }) => {
            const nid = detail?.node ?? detail?.node_id;
            if (nid == null) return;
            const node = app.graph?.getNodeById?.(nid)
                || app.graph?.nodes?.find?.((n) => String(n.id) === String(nid));
            if (!node) return;
            const usageNode = findUpstreamUsageNode(node);
            if (usageNode) usageNode._bsawangShowUsage();
        });
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        const name = nodeData.name;
        if (name !== NODE && name !== "H3_API_PromptFormatter") return;
        const isEnhancer = name === NODE;
        const previous = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = previous?.apply(this, arguments);
            if (isEnhancer) {
                if (!this._bsawangBasketReady && createPanel(this, nodeData)) this._bsawangBasketReady = true;
                setupMaterialPorts(this);  // 素材口自增长：只显示已连接 + 下一个空口
            } else if (name === "H3_API_PromptFormatter" && !this._bsawangH3Ready) {
                this._bsawangH3Ready = createH3Panel(this) === true;
            }
            // LLM 调用完成后：从后端读本次 token，展示在增强器面板信息栏（报错态优先保留）
            const prevExecuted = this.onExecuted;
            this.onExecuted = function (message) {
                if (typeof prevExecuted === "function") prevExecuted.apply(this, arguments);
                if (typeof this._bsawangShowUsage === "function") this._bsawangShowUsage();
            };
            return result;
        };
    },
});
