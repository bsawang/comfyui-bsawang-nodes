# -*- coding: utf-8 -*-
"""Tag 反推匹配节点：图片/反推文字 → 结构化 tag 集合 + 扩展建议。

双匹配模式（widget「匹配模式」切换）：
- 文字匹配：反推文字（或先图片反推）→ LLM 对照已加载 tag 池 → 匹配
- 图片直匹配：图 + tag池 一次调用直接匹配（A/B 实验，比「图片理解 vs 文字理解」准确度）

系统提示词按功能模块内部加载（templates/*.txt，见模块顶部常量），不向外暴露；
自定义 = 直接改 templates/ 下对应 txt 文件。三输出：
tag集合JSON（matched，喂下游增强器/预设）+ 反推结果（完整 matched+suggestions+引导词，供面板直出渲染）
+ 反推文字（多维描述全文）。
采纳/存预设由前端调 /bsawang/tag/* 路由写持久层（tag_store.py）。
中间节点，结果随工作流输出直出，后端不存反推状态。

模块化拆分：通用能力（LLM/HTTP/图片/JSON）→ llm_utils.py；tag 数据 → tag_store.py；
本文件只留 tag 匹配领域逻辑。
"""
import json
from pathlib import Path

from . import llm_utils
from . import tag_store

NODE_DIR = Path(__file__).parent
TEMPLATE_PATH = NODE_DIR / "templates" / "Tag反推结构化.txt"
# 图片反推/图片直匹配模板：本地覆盖宽规则（llm_utils.pick_template，任意后缀变体优先），不依赖具体后缀字面
IMG_REVERSE_TEMPLATE_PATH = llm_utils.pick_template(NODE_DIR / "templates", "图片多维分析")
IMAGE_MATCH_TEMPLATE_PATH = llm_utils.pick_template(NODE_DIR / "templates", "图片直匹配")

_ERROR_PREFIX = "[Tag反推] "


def _pool_block() -> str:
    """全量 tag 池（dict.json + baskets/ 全部篮子 options），按篮子分组。"""
    pool_rows = []
    for section in tag_store._get_dict()["sections"]:
        for f in section["fields"]:
            if f.get("type") == "basket":
                pool_rows.append(f"{f['key']}：{'、'.join(f.get('options', []))}")
    return "\n".join(pool_rows)


def _validate_parsed(parsed) -> tuple:
    """校验解析结果：matched 只留池内存在的 tag；suggestions 的篮子必须在池内。返回 (matched, suggestions, 反推文字)。"""
    text = (parsed.get("反推文字") or "").strip()
    valid_matched = {}
    for basket, tags in (parsed.get("matched") or {}).items():
        opts = tag_store._basket_options(basket)
        if opts is None:
            continue
        keep = [t for t in tags if t in opts]
        if keep:
            valid_matched[basket] = keep
    clean_suggestions = []
    for s in parsed.get("suggestions") or []:
        if not isinstance(s, dict):
            continue
        bk = (s.get("篮子") or "").strip()
        tag = (s.get("tag") or "").strip()
        opts = tag_store._basket_options(bk)
        # 建议必须是「库中不存在」的新 tag：库中已有的（如 跪趴撅臀/私处/ahegao）过滤掉
        if bk and tag and opts is not None and tag not in opts:
            clean_suggestions.append({"篮子": bk, "tag": tag, "reason": (s.get("reason") or "").strip()})
    return valid_matched, clean_suggestions, text


def _run_match(config, text):
    """文字匹配：反推文字 → 结构化 matched + suggestions。返回 (matched, suggestions, text)。"""
    system = llm_utils.read_system_prompt(str(TEMPLATE_PATH), error_prefix=_ERROR_PREFIX)
    user_msg = f"【反推文字】\n{text}\n\n【已加载tag】\n{_pool_block()}"
    # 匹配是分类任务：强制低温（0.0）减少飘逸，结果更稳定
    content, _ = llm_utils.call_llm(
        llm_utils.stable_config(config, 0.0), system, user_msg, error_prefix=_ERROR_PREFIX
    )
    valid_matched, clean_suggestions, _ = _validate_parsed(
        llm_utils.parse_json(content, error_prefix=_ERROR_PREFIX)
    )
    return valid_matched, clean_suggestions, text


def _run_image_match(config, image):
    """图片直匹配：图 + tag池 一次调用 → matched + suggestions + 反推文字。返回 (matched, suggestions, text)。"""
    if not config.get("支持视觉", False):
        raise ValueError(
            f"{_ERROR_PREFIX}图片直匹配需要多模态模型：请在「LLM API 设定器」里选视觉模型"
            "（如 deepseek-v4-flash-vision-exp）并把「支持视觉」设为「是」。"
        )
    image_b64 = llm_utils.image_to_base64(image)
    system = llm_utils.read_system_prompt(str(IMAGE_MATCH_TEMPLATE_PATH), error_prefix=_ERROR_PREFIX)
    user_msg = f"【已加载tag】\n{_pool_block()}\n\n请分析图片并对照 tag 池，只输出严格 JSON。"
    content, _ = llm_utils.call_llm(
        llm_utils.stable_config(config, 0.0), system, user_msg,
        image_b64=image_b64, error_prefix=_ERROR_PREFIX,
    )
    valid_matched, clean_suggestions, text = _validate_parsed(
        llm_utils.parse_json(content, error_prefix=_ERROR_PREFIX)
    )
    return valid_matched, clean_suggestions, text


def _reverse_image(config, image):
    """图片反推：多模态 LLM 按多维分析模板把图片转成结构化描述文本（反推文字）。"""
    if not config.get("支持视觉", False):
        raise ValueError(
            f"{_ERROR_PREFIX}图片反推需要多模态模型：请在「LLM API 设定器」里选视觉模型"
            "（如 deepseek-v4-flash-vision-exp）并把「支持视觉」设为「是」。"
        )
    image_b64 = llm_utils.image_to_base64(image)
    system = llm_utils.read_system_prompt(str(IMG_REVERSE_TEMPLATE_PATH), error_prefix=_ERROR_PREFIX)
    user_msg = "请按系统提示词要求，完整、结构化地分析这张图片。"
    content, _ = llm_utils.call_llm(
        llm_utils.stable_config(config, 0.2), system, user_msg,
        image_b64=image_b64, error_prefix=_ERROR_PREFIX,
    )
    return content.strip()


def _generate_guidance(config, text):
    """独立调用：把反推文字归纳成一段预设引导词（纯文本输出，无 JSON 解析风险）。"""
    system = (
        "你是 AI 图像生成场景归纳助手。把用户给的场景描述归纳成一段预设引导词（1-2 句），"
        "概括核心场景：主体/环境/构图/光线/氛围/风格。直接输出引导词本身，中文，"
        "不要任何前缀、解释、markdown 或引号包裹。"
    )
    user_msg = f"【场景描述】\n{text}"
    content, _ = llm_utils.call_llm(
        llm_utils.stable_config(config, 0.2), system, user_msg, error_prefix=_ERROR_PREFIX
    )
    return content.strip().strip('"').strip("'")


class Tag_Reverse:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "反推文字": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "VL 反推输出 / 多维描述（如 AILab_QwenVL 反推结果）；接「图片」时此项可空",
                    },
                ),
                "图片": (
                    "IMAGE",
                    {"tooltip": "可选：接图时用多模态 LLM 直接反推（需 LLM 设定器「支持视觉=是」），生成反推文字后走匹配；与「反推文字」二选一"},
                ),
                "匹配模式": (
                    ["文字匹配", "图片直匹配"],
                    {
                        "default": "文字匹配",
                        "tooltip": "文字匹配=反推文字/图片反推后按文字匹配（现状）；图片直匹配=图+tag池一次调用直接匹配（A/B 实验，比「图片理解 vs 文字理解」的匹配准确度）",
                    },
                ),
                "LLM": ("LLM_CONFIG", {}),
                # 系统提示词按功能模块内部加载（templates/*.txt，见模块顶部常量），不向外暴露；
                # 自定义 = 直接改 templates/ 下对应 txt 文件（改 txt 即生效）
                # 当前 tag 集合（matched，随工作流序列化；面板写，反推结果覆盖）
                "bsawang_tag_state": (
                    "STRING",
                    {"default": "{}", "multiline": True, "hidden": True},
                ),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("tag集合JSON", "反推结果", "反推文字")
    FUNCTION = "process"
    CATEGORY = "bsawang/提示词增强器"

    def process(self, 反推文字, LLM=None, 图片=None, 匹配模式="文字匹配", **kw):
        if not isinstance(LLM, dict) or not LLM.get("模型"):
            raise ValueError(f"{_ERROR_PREFIX}需要接「LLM API 设定器」的输出。")

        if 匹配模式 == "图片直匹配":
            # A/B 实验臂：图 + tag池 一次调用直接匹配（匹配阶段真看图）
            if 图片 is None:
                raise ValueError(f"{_ERROR_PREFIX}图片直匹配模式需要接「图片」输入。")
            valid_matched, clean_suggestions, text = _run_image_match(LLM, 图片)
        else:
            # 图片反推优先：有图 → 多模态 LLM 直接反推；无图 → 用反推文字输入
            if 图片 is not None:
                text = _reverse_image(LLM, 图片)
            else:
                text = (反推文字 or "").strip()
            if not text:
                raise ValueError(f"{_ERROR_PREFIX}反推文字为空：请填「反推文字」或接「图片」输入。")
            valid_matched, clean_suggestions, _ = _run_match(LLM, text)

        # 两模式都归纳引导词（基于反推文字）
        try:
            guidance = _generate_guidance(LLM, text)
        except Exception:
            guidance = ""  # 引导词归纳失败不阻塞主匹配
        full = {"matched": valid_matched, "suggestions": clean_suggestions, "引导词": guidance, "反推文字": text}
        return (
            json.dumps(valid_matched, ensure_ascii=False),
            json.dumps(full, ensure_ascii=False),
            text,
        )


NODE_CLASS_MAPPINGS = {"Tag_Reverse": Tag_Reverse}
NODE_DISPLAY_NAME_MAPPINGS = {"Tag_Reverse": "Tag 反推"}
