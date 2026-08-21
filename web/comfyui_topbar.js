// bsawang-nodes 顶部运行栏定制：仅向运行按钮区插入「重启」按钮。
// 点击 → POST /manager/reboot（复用 ComfyUI-Manager 重启接口，os.execv 同命令重 exec）。
import { app } from "/scripts/app.js";

function restartServer() {
    fetch("/manager/reboot", { method: "POST" })
        .then((r) => {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json().catch(() => ({}));
        })
        .then((d) => console.log("[bsawang] 重启", d))
        .catch((e) => console.error("[bsawang] 重启请求失败", e));
}

app.registerExtension({
    name: "bsawang.topbar",
    async setup() {
        function addRestartButton() {
            const runArea = document.querySelector('[data-testid="action-bar-buttons"]')
                || document.querySelector('[data-testid="queue-button"]')?.parentElement;
            if (!runArea) return false;
            if (runArea.querySelector(".bsawang-restart-btn")) return true;
            const btn = document.createElement("button");
            btn.className = "bsawang-restart-btn";
            btn.textContent = "重启";
            btn.title = "重启 ComfyUI 服务（os.execv 同命令重 exec，py/js 改动生效）";
            btn.style.cssText =
                "padding:2px 12px;border-radius:6px;cursor:pointer;font-size:12px;line-height:18px;" +
                "border:1px solid #3a5060;background:#14202c;color:#e1c88a;margin-left:6px";
            btn.addEventListener("click", () => {
                btn.disabled = true;
                btn.textContent = "重启中…";
                restartServer();
            });
            runArea.appendChild(btn);
            return true;
        }

        if (!addRestartButton()) {
            const obs = new MutationObserver(() => {
                if (addRestartButton()) obs.disconnect();
            });
            obs.observe(document.body, { childList: true, subtree: true });
        }
    },
});
