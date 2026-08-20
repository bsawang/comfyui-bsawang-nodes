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

// ---------- LLM token 用量显示（消费节点标题栏，只显示本次，轻量） ----------
function fmtTokens(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "k";
    return String(n);
}

function showUsageOnNode(node) {
    // 从后端 GET /llm_usage/last 读「本次」用量，追加到节点标题
    fetch("/llm_usage/last").then(r => r.json()).then(u => {
        if (!u || !node) return;
        const tin = Number(u["本次输入token"]) || 0;
        const tout = Number(u["本次输出token"]) || 0;
        if (tin === 0 && tout === 0) return;
        const label = `本次↑${fmtTokens(tin)}/↓${fmtTokens(tout)}`;
        // 追加到标题：把 token 记到节点标题栏右侧徽章
        const header = node.el?.querySelector?.(".lg-node-header");
        const row = header?.querySelector?.(".justify-between");
        if (!row) return;
        let badge = row.querySelector("[data-bsawang-usage]");
        if (!badge) {
            badge = document.createElement("span");
            badge.setAttribute("data-bsawang-usage", "1");
            badge.className = "flex h-5 shrink-0 items-center bg-component-node-widget-background p-1 text-xs rounded-full";
            badge.style.cssText = "margin-left:auto;white-space:nowrap;font-family:Arial,sans-serif;color:#a0a0a0;";
            row.appendChild(badge);
        }
        badge.textContent = label;
        badge.style.display = "";
    });
}

// ---------- 面板构建 ----------
function createPanel(node, nodeData) {
    if (typeof node.addDOMWidget !== "function") return false;

    // 篮子 meta 来自 state widget 的 bsawang.basketMeta（后端注入 options/互斥/冲突/condition/tasks）
    const required = nodeData?.input?.required || {};
    const stateDef = required["bsawang_basket_state"];
    let metaMap = stateDef?.[1]?.["bsawang.basketMeta"] || {};
    let basketKeys = Object.keys(metaMap);
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
        fontSize: "12px", userSelect: "none", padding: "3px 4px 2px", overflow: "visible",
    });

    const style = make("style");
    style.textContent = `
      .bsa-tabs{display:flex;flex-wrap:wrap;gap:3px;margin-bottom:5px}
      .bsa-tab{padding:3px 10px;border-radius:9px;cursor:pointer;font-size:11px;line-height:16px;border:1px solid #2d4255;background:#14202c;color:#9fb4c5;transition:background .12s}
      .bsa-tab:hover{border-color:#0aa4d6}
      .bsa-tab.active{background:#0aa4d6;border-color:#0aa4d6;color:#06131b;font-weight:600}
      .bsa-basket{display:none;flex-wrap:wrap;gap:4px;padding:6px;border:1px solid #2d4255;border-radius:8px;background:#101b26}
      .bsa-basket.show{display:flex}
      .bsa-chip{display:inline-block;padding:2px 9px;border-radius:10px;cursor:pointer;font-size:11px;line-height:16px;border:1px solid #3a5060;background:#1d2731;color:#c9d8e4;transition:background .12s}
      .bsa-chip:hover{border-color:#0aa4d6}
      .bsa-chip.on{background:#0aa4d6;border-color:#0aa4d6;color:#06131b;font-weight:600}
      .bsa-chip.disabled{background:#0d141b;border-color:#26333d;color:#4a5863;cursor:not-allowed;pointer-events:none}
      .bsa-btns{display:flex;gap:6px;margin-bottom:4px}
      .bsa-btn{padding:3px 12px;border-radius:7px;cursor:pointer;font-size:11px;line-height:16px;border:1px solid #3a5060;background:#14202c;color:#9fb4c5;transition:background .12s}
      .bsa-btn:hover{border-color:#0aa4d6;background:#1a2a3a;color:#e1e9ef}
      .bsa-summary{margin-top:5px;padding:5px 7px;border:1px solid #2d4255;border-radius:6px;background:#0d141b;min-height:20px;line-height:1.5;display:flex;flex-wrap:wrap;gap:4px;align-items:center}
      .bsa-sumtag{display:inline-block;padding:1px 8px;border-radius:9px;font-size:10px;line-height:15px;background:#0aa4d6;color:#06131b;border:1px solid #0aa4d6;cursor:pointer}
      .bsa-empty{color:#4a5863;font-size:11px}
    `;
    root.appendChild(style);

    // 清空/随机按钮行
    const btnRow = make("div", {}, "");
    btnRow.className = "bsa-btns";
    const btnClear = make("button", {}, "清空篮子");
    btnClear.className = "bsa-btn";
    btnClear.addEventListener("click", clearBaskets);
    const btnRandom = make("button", {}, "随机篮子");
    btnRandom.className = "bsa-btn";
    btnRandom.addEventListener("click", randomBaskets);
    btnRow.appendChild(btnClear);
    btnRow.appendChild(btnRandom);
    // 刷新字典：重读 baskets/ + dict.json，重建篮子 tab/chips（无需重启）
    const btnRefresh = make("button", {}, "刷新字典");
    btnRefresh.className = "bsa-btn";
    btnRefresh.title = "重读 baskets/ 与 dict.json，刷新篮子选项（无需重启）";
    btnRefresh.addEventListener("click", async () => {
        try {
            const res = await fetch("/bsawang/prompt_enhancer/dict");
            const data = await res.json();
            if (!data || !data.basket_meta) throw new Error("响应缺少 basket_meta");
            metaMap = data.basket_meta;
            renderBaskets();
        } catch (e) {
            console.error("[提示词增强器] 刷新字典失败：", e);
        }
    });
    btnRow.appendChild(btnRefresh);
    root.appendChild(btnRow);

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
            const visible = isTaskVisible(basketKeys[i]);
            t.style.display = visible ? "" : "none";
            basketEls[i].style.display = visible ? "" : "none";
        });
        // 当前激活的 tab 若被隐藏，切到第一个可见的
        if (!isTaskVisible(basketKeys[activeIdx])) {
            const firstVisible = basketKeys.findIndex((k, i) => isTaskVisible(k));
            if (firstVisible >= 0) switchTab(firstVisible);
        }
        if (domWidget) domWidget.setSize?.();
    }

    let switchTab = function (idx) {
        activeIdx = idx;
        tabs.forEach((t, i) => t.classList.toggle("active", i === idx));
        basketEls.forEach((el, i) => el.classList.toggle("show", i === idx));
        if (domWidget) domWidget.setSize?.();
    };

    // 按当前 metaMap 构建/重建篮子（tab 行 + chips）——「刷新字典」按钮重调
    function renderBaskets() {
        tabRow.innerHTML = "";
        for (const el of basketEls) el.remove();
        basketEls = [];
        basketRefreshers = [];
        tabs = [];
        activeIdx = 0;
        basketKeys = Object.keys(metaMap);
        if (basketKeys.length === 0) {
            renderSummary();
            return;
        }
        basketKeys.forEach((key, i) => {
            const meta = metaMap[key];
            const options = meta.options || [];
            // tab
            const tab = make("span", {}, key);
            tab.className = "bsa-tab" + (i === 0 ? " active" : "");
            tab.title = "点击切换";
            tab.addEventListener("click", () => switchTab(i));
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

    // 重新同步：ComfyUI 在节点创建（onNodeCreated）之后才应用保存的 widget 值，
    // 面板初建时读到的可能是默认 "{}"，值被应用后需重建 chips（覆盖切换工作流/重开清空问题）
    function syncFromWidget() {
        const fresh = readState();
        if (JSON.stringify(fresh) !== JSON.stringify(state)) {
            state = fresh;
            basketRefreshers.forEach((fn) => fn());
            renderSummary();
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

    // 把面板 widget 移到「用户提示词-2 / 用户提示词」后面（自定义控件紧跟用户输入，篮子面板在两个文本输入之后）
    const ws = node.widgets;
    const panelIdx = ws.indexOf(domWidget);
    let promptIdx = ws.findIndex((w) => w.name === "用户提示词-2");
    if (promptIdx < 0) promptIdx = ws.findIndex((w) => w.name === "用户提示词");
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
        const visible = basketEls[activeIdx];
        const chipCount = visible ? visible.querySelectorAll(".bsa-chip").length : 0;
        const rows = Math.max(1, Math.ceil(chipCount / 6));
        measured[1] = Math.max(measured[1] || 0, 30 + rows * 22 + 34);
        return measured;
    };
    return true;
}

// ---------- 注册 ----------
app.registerExtension({
    name: "bsawang.prompt_enhancer",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        const name = nodeData.name;
        if (name !== NODE && name !== "H3_API_PromptFormatter") return;
        const isEnhancer = name === NODE;
        const previous = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = previous?.apply(this, arguments);
            if (isEnhancer && !this._bsawangBasketReady && createPanel(this, nodeData)) {
                this._bsawangBasketReady = true;
            }
            // LLM 调用完成后：从后端读本次 token，追加到消费节点标题栏
            const prevExecuted = this.onExecuted;
            this.onExecuted = function (message) {
                if (typeof prevExecuted === "function") prevExecuted.apply(this, arguments);
                showUsageOnNode(this);
            };
            return result;
        };
    },
});
