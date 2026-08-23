# -*- coding: utf-8 -*-
"""素材封装器节点：真素材（图片/文本/视频/音频）→ 素材对象（透传封装 + 角色/标签/反推元数据）。

透传：数据原样带在素材对象里，不做任何内容处理（不反推、不编码、不描述）——
「怎么使用素材」由增强器按基座提示词（常规文生图.txt 素材使用说明）决定。
反推只是标记：增强器按此决定是否看图反推（当前仅图片有意义）。
"""

角色选项 = ["风格参考", "角色参考", "构图参考", "动作参考", "环境参考", "音频参考", "道具参考"]


class Material:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "角色": (
                    角色选项,
                    {"default": "角色参考", "tooltip": "素材在生成中扮演的角色（常规文生图.txt 素材使用说明定义各角色用法）"},
                ),
                "标签": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "如 主体/画风/光线；生成模型里作为 <标签> 参考标记",
                        "tooltip": "角色标签：生成模型里用作 <标签> 参考匹配标记",
                    },
                ),
                "反推": (
                    ["是", "否"],
                    {
                        "default": "否",
                        "tooltip": "标记：图片是否需增强器看图反推成描述；否=仅角色定义（<标签> 标记），不处理图像内容",
                    },
                ),
            },
            "optional": {
                "图片": ("IMAGE", {"tooltip": "图片素材（透传，不处理内容）"}),
                "文本": ("STRING", {"multiline": True, "default": "", "tooltip": "文本素材（透传）"}),
                "视频": ("VIDEO", {"tooltip": "视频素材（透传；按补充说明使用，未提及则不输出）"}),
                "音频": ("AUDIO", {"tooltip": "音频素材（透传；按补充说明使用，未提及则不输出）"}),
            },
        }

    RETURN_TYPES = ("MATERIAL",)
    RETURN_NAMES = ("素材",)
    FUNCTION = "wrap"
    CATEGORY = "bsawang/素材"

    def wrap(self, 角色, 标签, 反推, 图片=None, 文本=None, 视频=None, 音频=None, **kw):
        if 图片 is not None:
            类型, 数据 = "图片", 图片
        elif 文本 is not None:
            类型, 数据 = "文本", (文本 or "").strip()
        elif 视频 is not None:
            类型, 数据 = "视频", 视频
        elif 音频 is not None:
            类型, 数据 = "音频", 音频
        else:
            raise ValueError("[素材] 需要接 图片/文本/视频/音频 任一输入。")
        素材 = {
            "类型": 类型,
            "角色": 角色,
            "标签": (标签 or "").strip(),
            "反推": 反推 == "是",
            "数据": 数据,
        }
        return (素材,)


NODE_CLASS_MAPPINGS = {"Material": Material}
NODE_DISPLAY_NAME_MAPPINGS = {"Material": "素材封装器"}
