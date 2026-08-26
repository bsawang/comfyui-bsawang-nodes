// 视频对比预览节点：信息栏（视频预览正下方显示各路视频信息）
// 机制（前端 1.48+ Vue 节点）：
//  1. 后端 ui 字符串会被执行器逐字符拆成数组（execution.py 合并 ui 时遍历展开），
//     后端返回「行列表」，这里 join 成文本。
//  2. 视频预览 = Vue video-preview 组件（在 lg-node-content 内），不在 node.widgets 里；
//     信息栏必须做成纯 DOM div 插到 video-preview 之后，才能显示在预览正下方。
//  3. 用 MutationObserver 防 Vue 重渲染把信息栏清掉。
import { app } from "/scripts/app.js";

const NODE = "VideoComparePreview";

function buildInfoEl() {
    const el = document.createElement("div");
    el.className = "bsawang-video-info";
    el.style.cssText = [
        "white-space: pre-wrap;",
        "box-sizing: border-box;",
        "font-family: var(--comfy-textarea-font-family, monospace);",
        "font-size: 12px;",
        "line-height: 1.5;",
        "color: var(--descrip-text, #888);",
        "padding: 4px 8px;",
        "border-top: 1px solid var(--border-color, rgba(255,255,255,0.12));",
        "user-select: text;",
    ].join(" ");
    return el;
}

function placeInfo(node, text) {
    const t = Array.isArray(text) ? text.join("\n") : String(text ?? "");
    const dom = document.querySelector(`.lg-node[data-node-id="${node.id}"]`);
    if (!dom) return false;
    const content = dom.querySelector(".lg-node-content");
    if (!content) return false;

    let el = node._bsawangInfoEl;
    if (!el) {
        el = buildInfoEl();
        node._bsawangInfoEl = el;
    }
    el.textContent = t;

    const vp = content.querySelector(".video-preview");
    if (vp) {
        if (vp.nextSibling !== el) vp.parentNode.insertBefore(el, vp.nextSibling);
    } else {
        if (content.lastChild !== el) content.appendChild(el);
    }

    // 防 Vue 重渲染清掉信息栏：子元素变化时保证信息栏在 video-preview 之后
    if (!node._bsawangInfoObserver) {
        node._bsawangInfoObserver = new MutationObserver(() => {
            const el2 = node._bsawangInfoEl;
            if (!el2) return;
            const vp2 = content.querySelector(".video-preview");
            if (vp2 && vp2.nextSibling !== el2) vp2.parentNode.insertBefore(el2, vp2.nextSibling);
        });
        node._bsawangInfoObserver.observe(content, { childList: true, subtree: true });
    }
    return true;
}

app.registerExtension({
    name: "bsawang.video_compare",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE) return;
        const prevOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = prevOnNodeCreated?.apply(this, arguments);
            const prevExecuted = this.onExecuted;
            this.onExecuted = function (m) {
                if (typeof prevExecuted === "function") prevExecuted.apply(this, arguments);
                this._bsawangInfoText = m?.video_info ?? "";
                if (this._bsawangInfoTimer) clearTimeout(this._bsawangInfoTimer);
                let tries = 0;
                const attempt = () => {
                    if (placeInfo(this, this._bsawangInfoText)) return;
                    if (++tries < 50) this._bsawangInfoTimer = setTimeout(attempt, 100);
                };
                attempt();
            };
            return r;
        };
    },
});
