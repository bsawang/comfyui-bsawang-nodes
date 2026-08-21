# -*- coding: utf-8 -*-
"""Tag 库编辑节点：持久库三层管理（tag / 篮子 / 预设）。

管理当前 tag 集合、篮子池（baskets/*.json）、预设（presets/*.json）：
应用预设 → 填集合；保存当前集合为预设；编辑篮子/tag。
Step1：与增强器（及 Tag 反推节点）通过 presets/ 文件级连接共享。
本版为控件可行性延续：mock，真实持久化后续接入。
"""
import json


class Tag_Library:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                # 当前 tag 集合（结构化 {篮子key: [tag]}）唯一序列化点
                "bsawang_tag_state": (
                    "STRING",
                    {"default": "{}", "multiline": True, "hidden": True},
                ),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("tag集合JSON",)
    FUNCTION = "process"
    CATEGORY = "bsawang/提示词增强器"

    def process(self, **kw):
        state = kw.get("bsawang_tag_state") or "{}"
        try:
            data = json.loads(state)
        except Exception:
            data = {}
        return (json.dumps(data, ensure_ascii=False),)


NODE_CLASS_MAPPINGS = {"Tag_Library": Tag_Library}
NODE_DISPLAY_NAME_MAPPINGS = {"Tag_Library": "Tag 库编辑"}
