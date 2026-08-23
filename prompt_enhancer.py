# -*- coding: utf-8 -*-
"""
提示词增强器节点（瘦身版）

接收 LLM 连接（LLM_API_Configurator 输出的 LLM_CONFIG）+ 用户提示词，
按四栏设置（类型选择 / 类型基础信息 / 内容设置 / 输出设置）增强为完整提示词。

模块化拆分：
  - 通用能力（LLM 调用 / HTTP / 图片 base64 / 模板读取 / 文本清理）→ llm_utils.py
  - tag 数据（dict/baskets/presets 持久层 + 路由）→ tag_store.py
  - 本文件只留增强节点自身的逻辑（任务模板 / 负面词库 / widget 构建 / 增强流程）。
"""
import json
from pathlib import Path

from . import llm_utils
from . import tag_store

NODE_DIR = Path(__file__).parent


def _load_tasks() -> dict:
    """读取任务类型模板 tasks.json：每任务类型的专属/隐藏控件 + 增强要点。"""
    tasks_path = NODE_DIR / "tasks.json"
    try:
        data = json.loads(tasks_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"[提示词增强器] 任务模板文件读取失败：{tasks_path}（{e}）")
    if not isinstance(data, dict) or not data:
        raise RuntimeError(f"[提示词增强器] 任务模板为空或结构缺失：{tasks_path}")
    return data


TASKS = _load_tasks()

# 默认负面词基础库（中文，按质量/解剖/安全分类）——LLM 生成负面词时先列这些，再补充本次内容特定项
DEFAULT_NEGATIVE = [
    "模糊", "低分辨率", "噪点", "JPEG伪影", "水印", "乱码", "变形", "比例失调", "过度锐化",
    "畸形", "多余手指", "多余肢体", "错误解剖", "残肢", "扭曲", "姿势不自然",
    "血腥", "血迹", "暴力", "恐怖", "惊悚", "未成年", "儿童",
]


def _load_default_system_prompt() -> str:
    try:
        return (NODE_DIR / "templates" / "常规文生图.txt").read_text(encoding="utf-8")
    except Exception:
        return (
            "你是专业的 AI 提示词增强专家。把用户输入的简单提示词，"
            "按用户给定的 tag 建议增强为完整、具体、可直接使用的生成提示词。"
        )


SYSTEM_PROMPT = _load_default_system_prompt()


def _read_system_prompt(path_str: str) -> str:
    """读系统提示词文件：路径为空回落内置默认；给路径读不到显式报错。"""
    return llm_utils.read_system_prompt(path_str, default=SYSTEM_PROMPT, error_prefix="[提示词增强器] ")


def _split_basket(value):
    """basket 值（逗号分隔）拆成 tag 列表；过滤「未设置」残留与空值。"""
    return [t.strip() for t in str(value or "").split(",") if t.strip() and t.strip() != "未设置"]


def _validate_basket(tags_by_field: dict):
    """自检：校验内容层组合合理性。检测互斥/冲突/跨字段矛盾，发现即 raise（报错拦截，不改数据）。

    tags_by_field: {字段key: [已选tag列表]}
    """
    errors = tag_store._collect_basket_errors(tags_by_field)
    if errors:
        raise ValueError("[提示词增强器] 内容组合自检失败：\n" + "\n".join("  - " + e for e in errors))


def _build_widget(field: dict):
    """把 dict.json 里的 field 转成 ComfyUI widget 定义。"""
    key = field["key"]
    ftype = field.get("type", "combo")
    label = field.get("label", key)
    default = field.get("default", "未设置")

    if ftype == "text":
        return (
            "STRING",
            {
                "multiline": True,
                "default": field.get("default", ""),
                "placeholder": field.get("placeholder", ""),
            },
        )
    if ftype == "int":
        widget = {
            "default": int(default),
            "min": int(field.get("min", 0)),
            "max": int(field.get("max", 2147483647)),
            "tooltip": label,
        }
        if field.get("control_after_generate"):
            # ComfyUI 标准：提供 fixed/randomize/increment/decrement（前端生成后处理）
            widget["control_after_generate"] = True
        return ("INT", widget)
    if ftype == "float":
        return (
            "FLOAT",
            {
                "default": float(default),
                "min": float(field.get("min", 1.0)),
                "max": float(field.get("max", 30.0)),
                "step": float(field.get("step", 0.5)),
                "tooltip": label + ("（仅对应任务类型时生效）" if "condition" in field else ""),
            },
        )
    if ftype == "basket":
        # 预制篮子：STRING widget 值 = 逗号分隔的已选 tag；前端用 bsawang.basket 标记渲染多选 chip
        # bsawang.basket 含 options + 约束规则（互斥/冲突对），前端据此做禁选
        tip = f"从预设里多选 {label}，逗号分隔"
        if "condition" in field:
            cond = field["condition"]
            cond_label = "，".join(f"{k}={v}" for k, v in cond.items())
            tip += f"（仅 {cond_label} 时生效）"
        return (
            "STRING",
            {
                "multiline": True,
                "default": field.get("default", ""),
                "placeholder": "点击下方选项选择",
                "tooltip": tip,
                "bsawang.basket": {
                    "options": field.get("options", []),
                    "mutually_exclusive": field.get("mutually_exclusive", False),
                    "conflicts": field.get("conflicts", []),
                },
            },
        )
    # combo 默认
    options = field.get("options", ["未设置"])
    tip = field.get("tooltip", label)
    if "condition" in field:
        cond = field["condition"]
        cond_label = "，".join(f"{k}={v}" for k, v in cond.items())
        tip += f"（仅 {cond_label} 时生效）"
    return (options, {"default": default, "tooltip": tip})


class Prompt_Enhancer:
    @classmethod
    def INPUT_TYPES(cls):
        required = {"LLM": ("LLM_CONFIG", {"tooltip": "接「LLM API 设定器」输出的 LLM 连接"})}
        # 从外部字典动态构建各栏 widget；系统提示词文件为固定控制项
        # basket 字段不生成原生 widget（前端 splice，避免占空间），meta 打进 bsawang.basketMeta 供前端渲染
        # meta.tasks = 篮子 condition 推导的适用任务类型列表（无 condition = 全部任务），前端据此显隐 tab
        basket_meta = {}
        for section in tag_store._get_dict()["sections"]:
            for field in section["fields"]:
                if field.get("type") == "basket":
                    basket_meta[field["key"]] = {
                        "options": field.get("options", []),
                        "mutually_exclusive": field.get("mutually_exclusive", False),
                        "conflicts": field.get("conflicts", []),
                        "condition": field.get("condition"),
                        "tasks": tag_store._tasks_from_condition(field.get("condition")),
                    }
                    continue
                required[field["key"]] = _build_widget(field)
        # 隐藏 state widget：存所有篮子已选 tag（JSON），唯一序列化点；前端读写它
        required["bsawang_basket_state"] = (
            "STRING",
            {
                "default": "{}",
                "multiline": True,
                "hidden": True,
                "bsawang.basketMeta": basket_meta,
            },
        )
        required["二次优化"] = (["否", "是"], {"default": "否", "tooltip": "开：第二次调用 LLM，把第一次输出与已选 tag 逐条核对——缺失补写、冲突/弱化以 tag 为准纠正，完整重写输出（多一次 LLM 调用）"})
        required["系统提示词文件"] = (
            "STRING",
            {"default": str(NODE_DIR / "templates" / "常规文生图.txt"), "tooltip": "增强规则 system prompt；文件缺失/读取失败时回落内置默认"},
        )
        optional = {
            "参考图": ("IMAGE", {"tooltip": "可选：图生图/图生视频的参考图。仅当 LLM 设定器「支持视觉=是」时生效；否则忽略（降级纯文本）"}),
        }
        return {"required": required, "optional": optional}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("提示词", "负面提示词")
    FUNCTION = "enhance"
    CATEGORY = "bsawang/提示词增强器"

    def enhance(self, LLM, 用户提示词, 系统提示词文件, 参考图=None, **kw):
        text = (用户提示词 or "").strip()
        if not text:
            return ("", "")
        # 用户提示词-2：可选场景环境输入（可空；有值则作为场景环境补充，不覆盖主体）
        场景环境 = (kw.get("用户提示词-2") or "").strip()

        # 从 LLM_CONFIG 取连接配置
        if not isinstance(LLM, dict) or not LLM.get("模型"):
            raise ValueError("[提示词增强器] LLM 连接无效：请接「LLM API 设定器」的输出。")
        支持视觉 = LLM.get("支持视觉", False)

        # 参考图 → base64（仅 LLM 支持视觉时；否则降级纯文本）
        参考图b64 = None
        if 参考图 is not None:
            if 支持视觉:
                try:
                    参考图b64 = llm_utils.image_to_base64(参考图)
                except Exception as e:
                    raise ValueError(f"[提示词增强器] 参考图转 base64 失败：{e}")
            # 不支持视觉：不报错，降级——LLM 看不到图，靠 text 描述

        system_prompt = _read_system_prompt(系统提示词文件)

        # 任务类型（必填）：决定视频时长等条件字段是否生效
        任务类型 = kw.get("任务类型", "文生图(T2I)")
        任务类型行 = f"任务类型：{任务类型}"

        # 从隐藏 state widget 解析篮子已选 tag（JSON：{字段key: [tag列表]}）；预设引导语单独提取
        preset_guidance = ""
        basket_tags = {}
        try:
            state = json.loads(kw.get("bsawang_basket_state") or "{}")
            if isinstance(state, dict):
                preset_guidance = (state.pop("__preset_guidance", "") or "").strip()
                for k, v in state.items():
                    basket_tags[k] = _split_basket(v if isinstance(v, str) else ",".join(v))
        except Exception:
            basket_tags = {}

        # 自检：校验互斥/冲突/跨字段矛盾（报错拦截）
        _validate_basket(basket_tags)

        # 按选中篮子注入 guidance / option_guidance（篮子文件定义的系统提示词补充，去重；维度规则本地注入）
        injected = []
        _seen = set()
        for _section in tag_store._get_dict()["sections"]:
            for _f in _section["fields"]:
                if _f.get("type") == "basket" and basket_tags.get(_f["key"]):
                    for _g in (_f.get("guidance") or []):
                        if _g and _g not in _seen:
                            _seen.add(_g)
                            injected.append(_g)
                    # 按选中 tag 注入对应行（主题风格/艺术风格特点词表等）
                    for _tag in basket_tags[_f["key"]]:
                        _row = (_f.get("option_guidance") or {}).get(_tag)
                        if _row and _row not in _seen:
                            _seen.add(_row)
                            injected.append(_row)
        if injected:
            system_prompt = system_prompt.rstrip() + "\n\n## 内容篮子注入规则\n" + "\n".join(injected)
        if preset_guidance:
            system_prompt = system_prompt.rstrip() + "\n\n## 预设场景引导\n" + preset_guidance

        # 收集内容层 tag 建议（除任务类型/输出格式外的所有字典字段）
        # basket 字段值 = 逗号分隔的已选 tag（可能含用户手动补充的），拆成列表；空篮子不传。
        # combo 字段单值；未设置不传，LLM 自动补全。
        tag_lines = []
        for section in tag_store._get_dict()["sections"]:
            if section["id"] == "output":
                continue  # 输出设置单独处理
            for field in section["fields"]:
                key = field["key"]
                if key in ("用户提示词", "用户提示词-2", "任务类型"):
                    continue
                # 条件字段（视频时长/镜头运动等）：仅当任务类型匹配时传
                if field.get("condition"):
                    cond = field["condition"]
                    if 任务类型 not in cond.get("任务类型", []):
                        continue
                ftype = field.get("type", "combo")
                label = field.get("label", key)
                if ftype == "basket":
                    tags = basket_tags.get(key, [])
                    if not tags:
                        continue  # 空篮子不传（篮子空 = 该维度不约束）
                    line = f"{label}：{'、'.join(tags)}"
                    if key == "艺术风格":
                        line += "（强约束：输出必须体现所选风格的核心视觉特征，按注入的风格特点词配 2-3 个视觉特点词，不得退化为写实）"
                    tag_lines.append(line)
                else:
                    val = kw.get(key, "")
                    if not val or val == "未设置":
                        continue  # 未设置不传该行，LLM 自动补全
                    tag_lines.append(f"{label}：{val}")

        # 输出设置
        输出格式 = kw.get("输出格式", "自然语言")
        输出结构 = kw.get("输出结构", "连贯一段")
        输出语言 = kw.get("输出语言", "中文")
        输出长度 = kw.get("输出长度", "标准")
        带负面提示词 = kw.get("带负面提示词", "否")
        二次优化 = kw.get("二次优化", "否")

        # 从 tasks.json 取当前任务类型的增强要点（外置可改）
        task_tpl = TASKS.get(任务类型, {})
        增强要点 = task_tpl.get("增强要点", "")

        # 组装 user_msg：tag 建议 + 输出设置 + 任务类型增强要点
        参数块 = "\n".join(x for x in ([任务类型行] + tag_lines) if x)
        output_lines = [
            f"输出格式：{输出格式}",
            f"输出结构：{输出结构}",
            f"输出语言：{输出语言}",
            f"输出长度：{输出长度}",
        ]
        负面说明 = (
            "最后单独输出一行负面提示词，格式为：『负面提示词: ...』。"
            "必须包含以下基础负面词："
            + "、".join(DEFAULT_NEGATIVE)
            + "；在此基础上补充针对本次内容的特定负面项（如具体部位的畸形、风格不符等）。"
            "输出用英文逗号分隔的标签，不使用则输出『负面提示词: 』。\n"
            if 带负面提示词 == "是"
            else ""
        )
        user_msg = (
            f"{参数块}\n\n"
            "【输出设置】\n"
            + "\n".join(output_lines)
            + "\n\n"
            "【本任务类型增强要点】\n"
            f"{增强要点}\n\n"
            "上面给出的 tag 是给 LLM 的建议，请结合用户提示词自由组织、合理增补细节。"
            "按系统提示词的「按输出格式组织」段与上方增强要点执行。"
            "输出结构为『分段标题』时，按 [主体]/[环境场景]/[构图]/[光线]/[氛围]/[风格] 分块（视频加 [时间轴]）；"
            "『连贯一段』时输出自然段落。"
            f"{负面说明}"
            "直接输出增强后的提示词本身，不要任何前言、解释或 markdown 代码块。\n\n"
            f"【用户提示词】\n{text}"
            + (f"\n\n【场景环境】\n{场景环境}" if 场景环境 else "")
        )

        种子 = int(kw.get("种子", 0) or 0)

        content, _usage = llm_utils.call_llm(
            LLM, system_prompt, user_msg,
            image_b64=参考图b64, seed=种子, error_prefix="[提示词增强器] ",
        )

        # 拆分负面提示词：主调用已让 LLM 输出「正文 + 负面提示词: ...」行
        负面提示词 = ""
        if 带负面提示词 == "是" and "负面提示词:" in content:
            # 按最后一个「负面提示词:」拆分
            idx = content.rfind("负面提示词:")
            content, neg_part = content[:idx].rstrip(), content[idx + len("负面提示词:"):].strip()
            负面提示词 = neg_part.split("\n")[0].strip()  # 只取第一行
            content = content.strip()

        # 压缩多余空行：连续 2+ 空行压成 1 个空行（分段标题间保持 1 空行）
        content = llm_utils.collapse_blank_lines(content)

        # 二次优化：把第一次输出与已选 tag 清单逐条核对，缺失补写、冲突/弱化以 tag 为准纠正，完整重写
        if 二次优化 == "是" and content.strip():
            try:
                content = self._second_pass_verify(LLM, tag_lines, content, text, 场景环境, 种子)
                content = llm_utils.collapse_blank_lines(content)
            except Exception:
                pass  # 二次优化失败不阻断，回落第一次输出

        return (content, 负面提示词)

    def _second_pass_verify(self, LLM, tag_lines, content, text, 场景环境, 种子):
        """二次优化：把第一次输出与已选 tag 清单逐条核对，缺失补写、冲突/弱化以 tag 为准纠正，完整重写。"""
        system = (
            "你是提示词质检员。把【第一次增强输出】与【已选 tag 清单】逐条核对，按以下规则修订：\n"
            "① 已正确体现的 tag → 原样保留，不改动；\n"
            "② 缺失的 tag → 按该维度语义补写进正文对应位置；\n"
            "③ 与 tag 冲突、被弱化的内容 → 一律以 tag 为准纠正（tag 优先级高于用户输入：如 tag 要求蝴蝶光，正文却写逆光 → 改为蝴蝶光）；\n"
            "④ 只修订 tag 相关部分，未涉及的内容保持不动；\n"
            "⑤ 完整重写并输出修订后的正文，保持第一次输出的格式/语言/长度/结构；\n"
            "⑥ 只输出修订后的正文本身，不要前言、解释、markdown 代码块，不要输出负面提示词。"
        )
        checklist = "\n".join(tag_lines) if tag_lines else "（无已选 tag）"
        user_msg = (
            f"【已选 tag 清单】\n{checklist}\n\n"
            f"【第一次增强输出】\n{content}\n\n"
            f"【用户提示词原文】\n{text}"
            + (f"\n\n【场景环境】\n{场景环境}" if 场景环境 else "")
            + "\n\n请逐条核对每个 tag 是否在输出中得到体现：缺失→补写、冲突/弱化→以 tag 为准纠正、已体现→保留。完整重写输出修订后的正文。"
        )
        revised, _usage = llm_utils.call_llm(
            LLM, system, user_msg, seed=种子, error_prefix="[提示词增强器] "
        )
        return revised.strip()


WEB_DIRECTORY = "./web"

NODE_CLASS_MAPPINGS = {
    "Prompt_Enhancer": Prompt_Enhancer,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "Prompt_Enhancer": "提示词增强器",
}
