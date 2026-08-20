# -*- coding: utf-8 -*-
"""
H3 API 提示词格式化节点

把反推/原始描述通过 API LLM 格式化为 MiniMax H3 完整提示词。
替代闭源 TE_H3_Prompt_Enhancer：system prompt 明文可改（h3_system_prompt.txt），
不会凭空添加 <Video N> / 丢时间戳，规则完全可控。

默认走 DeepSeek Anthropic 兼容接口 + thinking disabled：
  - 本机 key 是 DeepSeek，模型 deepseek-v4-flash 是推理模型，关闭 thinking 才能把 token 全给正文；
  - DeepSeek 的 OpenAI 兼容接口会拒敏感内容，Anthropic 接口 + 角色框架 system prompt 可正常处理。
模型名默认 deepseek-v4-flash（账号实际模型，可用 GET /models 查；deepseek-chat 是旧别名）。
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import llm_usage

NODE_DIR = Path(__file__).parent


def _load_default_system_prompt() -> str:
    try:
        return (NODE_DIR / "h3_system_prompt.txt").read_text(encoding="utf-8")
    except Exception:
        return (
            "你是 MiniMax H3 视频生成模型的提示词专家。"
            "把用户输入改写为符合 H3 Prompting Guidance 的完整提示词。"
        )


H3_SYSTEM_PROMPT = _load_default_system_prompt()


def _read_system_prompt(path_str: str) -> str:
    """从本地文件读 system prompt。路径为空 → 用内置默认；路径给了但读不到 → 显式报错（透传到 ComfyUI UI）。"""
    path_str = (path_str or "").strip()
    if not path_str:
        return H3_SYSTEM_PROMPT
    try:
        with open(path_str, encoding="utf-8") as f:
            content = f.read().strip()
    except Exception as e:
        raise RuntimeError(f"[H3-API] 系统提示词文件读取失败：{path_str}（{e}）")
    if not content:
        raise RuntimeError(f"[H3-API] 系统提示词文件为空：{path_str}")
    return content

TASK_TYPES = [
    "全参考模式(Reference to Video)",
    "文生视频(T2VA)",
    "首帧图生视频(I2VA)",
    "首尾帧图生视频(FL2VA)",
    "尾帧图生视频(L2VA)",
]


def _strip_code_fences(content: str) -> str:
    if content.startswith("```"):
        content = content.split("\n", 1)[-1] if "\n" in content else ""
        if content.endswith("```"):
            content = content[:-3].rstrip()
    return content.strip()


class H3_API_PromptFormatter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "LLM": ("LLM_CONFIG", {"tooltip": "接「LLM API 设定器」输出的 LLM 连接"}),
                "text": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "反推/原始描述文本（如节点 AILab 反推输出）",
                    },
                ),
                "任务类型": (TASK_TYPES, {"default": TASK_TYPES[0]}),
                "视频时长": ("FLOAT", {"default": 10.0, "min": 1.0, "max": 30.0, "step": 0.5}),
                "系统提示词文件": (
                    "STRING",
                    {
                        "default": str(NODE_DIR / "h3_system_prompt.txt"),
                        "tooltip": "本地文件路径，读取 H3 system prompt；文件缺失/读取失败时回落内置默认",
                    },
                ),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("提示词",)
    FUNCTION = "format"
    CATEGORY = "bsawang/提示词增强器"

    def format(
        self,
        LLM,
        text,
        任务类型,
        视频时长,
        系统提示词文件,
    ):
        text = (text or "").strip()
        if not text:
            return ("",)

        # LLM 连接由「LLM API 设定器」注入，本节点只管格式化
        if not isinstance(LLM, dict) or not LLM.get("模型"):
            raise ValueError("[H3-API] LLM 连接无效：请接「LLM API 设定器」的输出。")
        接口格式 = LLM.get("接口格式", "Anthropic 兼容")
        模型 = LLM["模型"]
        API基础URL = LLM.get("API基础URL", "")
        APIKey环境变量 = LLM.get("APIKey环境变量", "ANTHROPIC_AUTH_TOKEN")
        温度 = LLM.get("温度", 0.4)
        最大token = LLM.get("最大token", 8192)

        系统提示词 = _read_system_prompt(系统提示词文件)

        api_key = os.environ.get(APIKey环境变量.strip()) or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if not api_key:
            raise ValueError(
                f"[H3-API] 未找到 API key：环境变量「{APIKey环境变量}」为空，"
                "回退 ANTHROPIC_AUTH_TOKEN 也为空。请在 ComfyUI 启动环境设置，或改 widget 里的环境变量名。"
            )

        # 任务类型 → 输出结构映射已外置到 h3_system_prompt.txt「UI 任务类型 → 输出结构映射」段，
        # 源码只做拼接：把任务类型/时长/文本传给 LLM，LLM 按 txt 的映射规则输出。
        user_msg = (
            f"任务类型：{任务类型}\n"
            f"视频时长：{视频时长} 秒\n\n"
            "请将下面给出的原始描述改写为符合上述规范的完整 H3 提示词。"
            "直接输出结果本身，不要任何前言、解释或 markdown 代码块。\n\n"
            f"{text}"
        )

        if 接口格式.startswith("Anthropic"):
            content, usage_snap = self._call_anthropic(
                api_key, 系统提示词, user_msg, 模型, API基础URL, 温度, 最大token
            )
        else:
            content, usage_snap = self._call_openai(
                api_key, 系统提示词, user_msg, 模型, API基础URL, 温度, 最大token
            )
        return (content,)

    # ---- Anthropic 兼容（/v1/messages + thinking disabled）----
    def _call_anthropic(
        self, api_key, system, user_msg, model, base_url, temperature, max_tokens
    ):
        url = base_url.strip().rstrip("/")
        if not url.endswith("/v1/messages"):
            url += "/v1/messages"
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": user_msg}],
        }
        # 部分兼容实现不接受 temperature，失败时去掉重试
        payload["temperature"] = temperature
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        data = self._post(url, headers, payload, strip_temperature_on_error=True)
        text_parts = [
            c.get("text", "")
            for c in data.get("content", [])
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        content = _strip_code_fences("".join(text_parts))
        if not content:
            raise RuntimeError(
                "[H3-API] Anthropic 接口返回空 text。请检查「最大token」是否太小、"
                "「API基础URL」是否为 Anthropic 兼容地址（.../anthropic）。"
            )
        usage = data.get("usage") or {}
        snap = llm_usage.record(usage.get("input_tokens"), usage.get("output_tokens"))
        return content, snap

    # ---- OpenAI 兼容（/chat/completions）----
    def _call_openai(
        self, api_key, system, user_msg, model, base_url, temperature, max_tokens
    ):
        url = base_url.strip().rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        data = self._post(url, headers, payload, strip_temperature_on_error=False)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"[H3-API] 响应结构异常：{json.dumps(data, ensure_ascii=False)[:500]}")
        content = _strip_code_fences((msg.get("content") or "").strip())
        if not content:
            reasoning = (msg.get("reasoning_content") or "").strip()
            hint = f"（reasoning 长 {len(reasoning)} 字符，可能被 max_tokens 截断）" if reasoning else ""
            raise RuntimeError(
                f"[H3-API] 模型返回空 content{hint}：推理模型 reasoning 占用 token，"
                "请改用「Anthropic 兼容」接口格式（thinking disabled）或调大「最大token」。"
            )
        usage = data.get("usage") or {}
        snap = llm_usage.record(usage.get("prompt_tokens"), usage.get("completion_tokens"))
        return content, snap

    # ---- 通用 HTTP POST ----
    def _post(self, url, headers, payload, strip_temperature_on_error):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            if strip_temperature_on_error and e.code in (400, 422) and "temperature" in body:
                payload.pop("temperature", None)
                req = urllib.request.Request(
                    url, data=json.dumps(payload).encode("utf-8"), headers=headers
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            raise RuntimeError(f"[H3-API] HTTP {e.code}：{body[:500]}")
        except Exception as e:
            raise RuntimeError(f"[H3-API] 请求失败：{e}")


NODE_CLASS_MAPPINGS = {
    "H3_API_PromptFormatter": H3_API_PromptFormatter,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3_API_PromptFormatter": "H3 API 提示词格式化",
}
