# -*- coding: utf-8 -*-
"""
LLM API 设定器节点

把 LLM API 连接配置（接口格式 / 模型 / 基础 URL / key 环境变量 / 温度 / 最大 token）
封装成一个 LLM_CONFIG 对象输出，供提示词增强器等下游节点连线使用。

设计参考：ComfyUI-H3-APIPromptFormatter（assets/nodes/H3-API-格式化节点）。
与 H3 单节点内嵌配置的区别：这里把「连什么 LLM」解耦出来，下游节点可复用任意 LLM 连接。

输出为自定义类型 LLM_CONFIG（dict），ComfyUI 内连线类型安全，不落工作流明文（key 从环境变量读）。
"""
import os


class LLM_API_Configurator:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "接口格式": (
                    ["Anthropic 兼容", "OpenAI 兼容"],
                    {
                        "default": "Anthropic 兼容",
                        "tooltip": (
                            "Anthropic 兼容=/v1/messages + thinking disabled（DeepSeek 推荐："
                            "敏感内容可过、token 全给正文）；OpenAI 兼容=/chat/completions"
                        ),
                    },
                ),
                "模型": (
                    "STRING",
                    {
                        "default": "deepseek-v4-flash",
                        "tooltip": "模型名（账号可用模型可用 GET /models 查；DeepSeek 本机为 deepseek-v4-flash / deepseek-v4-pro）",
                    },
                ),
                "API基础URL": (
                    "STRING",
                    {"default": "https://api.deepseek.com/anthropic"},
                ),
                "APIKey环境变量": (
                    "STRING",
                    {
                        "default": "ANTHROPIC_AUTH_TOKEN",
                        "tooltip": "从环境变量读 key，不落工作流明文；为空时回退 ANTHROPIC_AUTH_TOKEN",
                    },
                ),
                "温度": ("FLOAT", {"default": 0.4, "min": 0.0, "max": 2.0, "step": 0.05}),
                "最大token": ("INT", {"default": 8192, "min": 256, "max": 65536, "step": 64}),
                "支持视觉": (
                    ["否", "是"],
                    {
                        "default": "否",
                        "tooltip": "该 LLM 是否支持图片输入（GLM-4V / Qwen-VL / Gemini 等视觉模型选「是」；deepseek-v4 纯文本选「否」）。增强器接图时以此判断是否传图",
                    },
                ),
            }
        }

    RETURN_TYPES = ("LLM_CONFIG",)
    RETURN_NAMES = ("LLM",)
    FUNCTION = "create"
    CATEGORY = "bsawang/提示词增强器"

    def create(self, 接口格式, 模型, API基础URL, APIKey环境变量, 温度, 最大token, 支持视觉):
        api_key_env = (APIKey环境变量 or "").strip() or "ANTHROPIC_AUTH_TOKEN"
        # 设定时验证 key 环境变量存在（提前暴露配置错误，不等到下游调用才报）
        api_key = os.environ.get(api_key_env) or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if not api_key:
            raise ValueError(
                f"[LLM设定器] 未找到 API key：环境变量「{api_key_env}」为空，"
                "回退 ANTHROPIC_AUTH_TOKEN 也为空。请在 ComfyUI 启动环境设置，或改 widget 里的环境变量名。"
            )
        cfg = {
            "接口格式": 接口格式,
            "模型": (模型 or "").strip(),
            "API基础URL": (API基础URL or "").strip(),
            "APIKey环境变量": api_key_env,
            "温度": 温度,
            "最大token": 最大token,
            "支持视觉": 支持视觉 == "是",
        }
        if not cfg["模型"]:
            raise ValueError("[LLM设定器] 模型为空：请在 widget 里填写模型名。")
        if not cfg["API基础URL"]:
            raise ValueError("[LLM设定器] API基础URL 为空：请填写 API 服务地址。")
        return (cfg,)


NODE_CLASS_MAPPINGS = {
    "LLM_API_Configurator": LLM_API_Configurator,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "LLM_API_Configurator": "LLM API 设定器",
}
