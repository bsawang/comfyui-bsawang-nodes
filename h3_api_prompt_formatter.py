# -*- coding: utf-8 -*-
"""
H3 API 提示词格式化节点

把反推/原始描述通过 API LLM 格式化为 MiniMax H3 完整提示词。
替代闭源 TE_H3_Prompt_Enhancer：system prompt 明文可改（templates/H3视频提示词格式化.txt），
不会凭空添加 <Video N> / 丢时间戳，规则完全可控。

默认走 DeepSeek Anthropic 兼容接口 + thinking disabled（llm_utils.call_llm 统一处理）：
  - 本机 key 是 DeepSeek，模型 deepseek-v4-flash 是推理模型，关闭 thinking 才能把 token 全给正文；
  - DeepSeek 的 OpenAI 兼容接口会拒部分内容，Anthropic 接口 + 角色框架 system prompt 可正常处理。
模块化拆分：通用能力（LLM 调用 / 模板读取）→ llm_utils.py，本文件只留 H3 格式化逻辑。
"""
from pathlib import Path

from . import llm_utils

NODE_DIR = Path(__file__).parent


def _pick_h3_template() -> Path:
    """H3 提示词模板：本地覆盖宽规则（任意后缀变体优先），不依赖具体后缀字面。"""
    return llm_utils.pick_template(NODE_DIR / "templates", "H3视频提示词格式化")


def _load_default_system_prompt() -> str:
    try:
        return _pick_h3_template().read_text(encoding="utf-8")
    except Exception:
        return (
            "你是 MiniMax H3 视频生成模型的提示词专家。"
            "把用户输入改写为符合 H3 Prompting Guidance 的完整提示词。"
        )


H3_SYSTEM_PROMPT = _load_default_system_prompt()

TASK_TYPES = [
    "全参考模式(Reference to Video)",
    "文生视频(T2VA)",
    "首帧图生视频(I2VA)",
    "首尾帧图生视频(FL2VA)",
    "尾帧图生视频(L2VA)",
]


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
                        "default": str(_pick_h3_template()),
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

        系统提示词 = llm_utils.read_system_prompt(
            系统提示词文件, default=H3_SYSTEM_PROMPT, error_prefix="[H3-API] "
        )

        # 任务类型 → 输出结构映射已外置到 templates/H3视频提示词格式化.txt「UI 任务类型 → 输出结构映射」段，
        # 源码只做拼接：把任务类型/时长/文本传给 LLM，LLM 按 txt 的映射规则输出。
        user_msg = (
            f"任务类型：{任务类型}\n"
            f"视频时长：{视频时长} 秒\n\n"
            "请将下面给出的原始描述改写为符合上述规范的完整 H3 提示词。"
            "直接输出结果本身，不要任何前言、解释或 markdown 代码块。\n\n"
            f"{text}"
        )

        content, _usage = llm_utils.call_llm(LLM, 系统提示词, user_msg, error_prefix="[H3-API] ")
        return (content,)


NODE_CLASS_MAPPINGS = {
    "H3_API_PromptFormatter": H3_API_PromptFormatter,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3_API_PromptFormatter": "H3 API 提示词格式化",
}
