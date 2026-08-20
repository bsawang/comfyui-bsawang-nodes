# -*- coding: utf-8 -*-
"""
提示词增强器节点

接收 LLM 连接（LLM_API_Configurator 输出的 LLM_CONFIG）+ 用户提示词，
按四栏设置（类型选择 / 类型基础信息 / 内容设置 / 输出设置）增强为完整提示词。

核心机制：
  - 外部字典 dict.json 定义所有下拉（tag 字典），源码动态加载渲染；
    加分类/加值 = 改 dict.json，不改代码（「UI 只做拼接器」原则）。
  - 字典选出的值（tag）是给 LLM 的建议；用户定制走 text（第一栏），LLM 自由发挥组织输出。
  - 输出格式：自然语言 / Tag / 混合，规则外置 txt「按输出格式组织」段。

HTTP 调用复用 H3 节点方案：urllib 无第三方依赖；Anthropic 兼容 /v1/messages + thinking disabled
（DeepSeek 兼容接口可过、token 全给正文）；OpenAI 兼容 /chat/completions（GLM/Gemini 用）。
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import llm_usage

NODE_DIR = Path(__file__).parent
DICT_PATH = NODE_DIR / "dict.json"
BASKETS_DIR = NODE_DIR / "baskets"
TASKS_PATH = NODE_DIR / "tasks.json"



def _load_tasks() -> dict:
    """读取任务类型模板 tasks.json：每任务类型的专属/隐藏控件 + 增强要点。"""
    try:
        data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"[提示词增强器] 任务模板文件读取失败：{TASKS_PATH}（{e}）")
    if not isinstance(data, dict) or not data:
        raise RuntimeError(f"[提示词增强器] 任务模板为空或结构缺失：{TASKS_PATH}")
    return data


TASKS = _load_tasks()

# 默认负面词基础库（中文，按质量/解剖/安全分类）——LLM 生成负面词时先列这些，再补充本次内容特定项
DEFAULT_NEGATIVE = [
    "模糊", "低分辨率", "噪点", "JPEG伪影", "水印", "乱码", "变形", "比例失调", "过度锐化",
    "畸形", "多余手指", "多余肢体", "错误解剖", "残肢", "扭曲", "姿势不自然",
    "血腥", "血迹", "暴力", "恐怖", "惊悚", "未成年", "儿童",
]


def _load_dict() -> dict:
    """读取外部字典 dict.json，并合并 baskets/ 目录下所有篮子文件（一个篮子一个文件）。
    缺失/损坏时显式报错（透传 UI），不静默回落。"""
    try:
        data = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"[提示词增强器] 字典文件读取失败：{DICT_PATH}（{e}）")
    if not data or not data.get("sections"):
        raise RuntimeError(f"[提示词增强器] 字典文件为空或结构缺失：{DICT_PATH}")
    _merge_baskets(data)
    return data


def _merge_baskets(data: dict) -> None:
    """把 baskets/*.json（一个篮子一个文件）合并进 data['sections']。

    按 section.id 定位段（不存在自动新建），fields 按 key 去重追加，再按 order 排序。
    本地存在的篮子文件才会被合并——增删篮子 = 增删文件。
    """
    sec_index = {s["id"]: i for i, s in enumerate(data["sections"])}
    for fp in sorted(BASKETS_DIR.glob("*.json")):
        try:
            f = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"[提示词增强器] 篮子文件读取失败：{fp}（{e}）")
        if not isinstance(f, dict):
            raise RuntimeError(f"[提示词增强器] 篮子文件不是 JSON 对象：{fp}")
        key = f.get("key")
        section_id = f.get("section")
        if not key or not section_id:
            raise RuntimeError(f"[提示词增强器] 篮子文件缺少 key/section：{fp}")

        # 定位或自动新建 section
        if section_id not in sec_index:
            data["sections"].append({
                "id": section_id,
                "title": f.get("section_title") or section_id,
                "fields": [],
            })
            sec_index[section_id] = len(data["sections"]) - 1
        sec = data["sections"][sec_index[section_id]]

        # section_title 一致性校验（提供了且与现有不同则报错）
        st = f.get("section_title")
        if st and sec.get("title") and sec["title"] != st and sec["title"] != section_id:
            raise RuntimeError(
                f"[提示词增强器] section「{section_id}」标题不一致：{sec['title']} vs {st}（{fp}）"
            )
        if st and not sec.get("title"):
            sec["title"] = st

        # fields 按 key 去重
        fields = sec.setdefault("fields", [])
        for existing in fields:
            if existing.get("key") == key:
                raise RuntimeError(f"[提示词增强器] 篮子 key 冲突：{key}（{fp}）")

        # 去掉 loader 元数据字段，其余进字段定义
        field = {k: v for k, v in f.items() if k not in ("section", "section_title")}
        field.setdefault("type", "basket")
        field.setdefault("default", "")
        fields.append(field)

    # 同 section 内：基座字段（无 order）保持 dict.json 原顺序在前，篮子字段按 order 排序追加在后
    for sec in data["sections"]:
        base = [f for f in sec["fields"] if "order" not in f]
        baskets = sorted(
            (f for f in sec["fields"] if "order" in f),
            key=lambda f: (f.get("order", 0), f.get("key", "")),
        )
        sec["fields"] = base + baskets


# 字典 mtime 缓存：dict.json + baskets/*.json 变化时自动重读（配合前端「刷新字典」按钮，无需重启）
_DICT_CACHE = {"stamp": None, "data": None}


def _dict_stamp():
    """返回 dict.json + baskets/*.json 的 (路径, mtime) 指纹，用于判断文件是否变化。"""
    stamps = [(str(DICT_PATH), DICT_PATH.stat().st_mtime)]
    for fp in sorted(BASKETS_DIR.glob("*.json")):
        stamps.append((str(fp), fp.stat().st_mtime))
    return tuple(stamps)


def _get_dict() -> dict:
    """读取字典（mtime 缓存）：文件没变返回缓存，变了才重新加载。"""
    try:
        stamp = _dict_stamp()
    except OSError:
        stamp = None
    if _DICT_CACHE["data"] is None or _DICT_CACHE["stamp"] != stamp:
        _DICT_CACHE["data"] = _load_dict()
        _DICT_CACHE["stamp"] = stamp
    return _DICT_CACHE["data"]


def _tasks_from_condition(condition):
    """从篮子 condition 推导适用任务类型（前端 tab 显隐 + 刷新接口共用）；无 condition = 全部任务。"""
    if not condition or not isinstance(condition, dict):
        return []
    return condition.get("任务类型", [])


DICT = _get_dict()


def _load_default_system_prompt() -> str:
    try:
        return (NODE_DIR / "prompt_enhancer_system.txt").read_text(encoding="utf-8")
    except Exception:
        return (
            "你是专业的 AI 提示词增强专家。把用户输入的简单提示词，"
            "按用户给定的 tag 建议增强为完整、具体、可直接使用的生成提示词。"
        )


SYSTEM_PROMPT = _load_default_system_prompt()


def _read_system_prompt(path_str: str) -> str:
    path_str = (path_str or "").strip()
    if not path_str:
        return SYSTEM_PROMPT
    try:
        with open(path_str, encoding="utf-8") as f:
            content = f.read().strip()
    except Exception as e:
        raise RuntimeError(f"[提示词增强器] 系统提示词文件读取失败：{path_str}（{e}）")
    if not content:
        raise RuntimeError(f"[提示词增强器] 系统提示词文件为空：{path_str}")
    return content


def _split_basket(value):
    """basket 值（逗号分隔）拆成 tag 列表；过滤「未设置」残留与空值。"""
    return [t.strip() for t in str(value or "").split(",") if t.strip() and t.strip() != "未设置"]


def _validate_basket(tags_by_field: dict):
    """自检：校验内容层组合合理性。检测互斥/冲突/跨字段矛盾，发现即 raise（报错拦截，不改数据）。

    tags_by_field: {字段key: [已选tag列表]}
    """
    errors = []
    for key, tags in tags_by_field.items():
        if not tags:
            continue
        # 1. 整栏互斥：一次只能选 1 个
        fld_mutex = None
        fld_conflicts = []
        for section in _get_dict()["sections"]:
            for fld in section["fields"]:
                if fld["key"] == key:
                    fld_mutex = fld.get("mutually_exclusive")
                    fld_conflicts = fld.get("conflicts", [])
                    break
        if fld_mutex and len(tags) > 1:
            errors.append(f"[{key}] 互斥：一次只能选一个，当前选了 {len(tags)} 个：{'、'.join(tags)}")
        # 2. 特定冲突对：同一栏内两个冲突 tag 同时出现
        for pair in fld_conflicts:
            if pair[0] in tags and pair[1] in tags:
                errors.append(f"[{key}] 冲突：『{pair[0]}』与『{pair[1]}』不能同时选")
    # 3. 跨字段矛盾：被 gate 的字段有值但 gate 字段未选（gate 关系由篮子文件定义）
    gate_map = {}
    for section in _get_dict()["sections"]:
        for f in section["fields"]:
            g = f.get("gate")
            if g:
                gate_map.setdefault(g, []).append(f["key"])
    for gate_key, gated_keys in gate_map.items():
        if not tags_by_field.get(gate_key):
            for k in gated_keys:
                if tags_by_field.get(k):
                    errors.append(
                        f"[跨字段] 矛盾：{gate_key} 未选，但「{k}」仍有选择（{'、'.join(tags_by_field[k])}），请先选 {gate_key} 或清空「{k}」"
                    )
    if errors:
        raise ValueError("[提示词增强器] 内容组合自检失败：\n" + "\n".join("  - " + e for e in errors))


def _image_to_base64(image) -> str:
    """把 ComfyUI IMAGE tensor 转成 base64 JPEG（data URI）。"""
    import base64
    import io

    from PIL import Image

    img = image[0]  # 取第一帧
    arr = img.detach().cpu().numpy()
    if arr.shape[-1] == 4:  # RGBA → RGB
        arr = arr[..., :3]
    arr = (arr * 255).clip(0, 255).astype("uint8")
    pil = Image.fromarray(arr)
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _strip_code_fences(content: str) -> str:
    if content.startswith("```"):
        content = content.split("\n", 1)[-1] if "\n" in content else ""
        if content.endswith("```"):
            content = content[:-3].rstrip()
    return content.strip()


def _collapse_blank_lines(text: str) -> str:
    """把连续 2+ 空行压缩成 1 个空行（保留分段标题间的单个空行，去掉多余空行）。"""
    lines = text.split("\n")
    result = []
    prev_blank = False
    for line in lines:
        if line.strip() == "":
            if not prev_blank:
                result.append("")
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    # 去掉首尾空行
    while result and result[0].strip() == "":
        result.pop(0)
    while result and result[-1].strip() == "":
        result.pop()
    return "\n".join(result)


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
        for section in _get_dict()["sections"]:
            for field in section["fields"]:
                if field.get("type") == "basket":
                    basket_meta[field["key"]] = {
                        "options": field.get("options", []),
                        "mutually_exclusive": field.get("mutually_exclusive", False),
                        "conflicts": field.get("conflicts", []),
                        "condition": field.get("condition"),
                        "tasks": _tasks_from_condition(field.get("condition")),
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
        required["系统提示词文件"] = (
            "STRING",
            {"default": str(NODE_DIR / "prompt_enhancer_system.txt"), "tooltip": "增强规则 system prompt；文件缺失/读取失败时回落内置默认"},
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

        # tag 对照模式：系统提示词文件名含 tag_match 时，附带全量已加载篮子选项（供 LLM 对比）
        loaded_tag_block = ""
        if "tag_match" in str(系统提示词文件).lower():
            _tag_rows = []
            for _sec in _get_dict()["sections"]:
                for _f in _sec["fields"]:
                    if _f.get("type") == "basket":
                        _tag_rows.append(f"{_f['key']}：{'、'.join(_f.get('options', []))}")
            loaded_tag_block = "\n\n【已加载tag】\n" + "\n".join(_tag_rows)

        # 从 LLM_CONFIG 取连接配置
        if not isinstance(LLM, dict) or not LLM.get("模型"):
            raise ValueError("[提示词增强器] LLM 连接无效：请接「LLM API 设定器」的输出。")
        接口格式 = LLM.get("接口格式", "Anthropic 兼容")
        模型 = LLM["模型"]
        API基础URL = LLM.get("API基础URL", "")
        APIKey环境变量 = LLM.get("APIKey环境变量", "ANTHROPIC_AUTH_TOKEN")
        温度 = LLM.get("温度", 0.4)
        最大token = LLM.get("最大token", 8192)
        支持视觉 = LLM.get("支持视觉", False)

        # 参考图 → base64（仅 LLM 支持视觉时；否则降级纯文本）
        参考图b64 = None
        if 参考图 is not None:
            if not 支持视觉:
                # 不报错，降级：LLM 看不到图，靠 text 描述
                pass
            else:
                try:
                    参考图b64 = _image_to_base64(参考图)
                except Exception as e:
                    raise ValueError(f"[提示词增强器] 参考图转 base64 失败：{e}")

        api_key = os.environ.get(APIKey环境变量) or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if not api_key:
            raise ValueError(
                f"[提示词增强器] 未找到 API key：环境变量「{APIKey环境变量}」为空，"
                "回退 ANTHROPIC_AUTH_TOKEN 也为空。请在 LLM 设定器里修正环境变量名。"
            )

        system_prompt = _read_system_prompt(系统提示词文件)

        # 任务类型（必填）：决定视频时长等条件字段是否生效
        任务类型 = kw.get("任务类型", "文生图(T2I)")
        任务类型行 = f"任务类型：{任务类型}"

        # 从隐藏 state widget 解析篮子已选 tag（JSON：{字段key: [tag列表]}）
        basket_tags = {}
        try:
            state = json.loads(kw.get("bsawang_basket_state") or "{}")
            if isinstance(state, dict):
                for k, v in state.items():
                    basket_tags[k] = _split_basket(v if isinstance(v, str) else ",".join(v))
        except Exception:
            basket_tags = {}

        # 自检：校验互斥/冲突/跨字段矛盾（报错拦截）
        _validate_basket(basket_tags)

        # 按选中篮子注入 guidance / option_guidance（篮子文件定义的系统提示词补充，去重；维度规则本地注入）
        injected = []
        _seen = set()
        for _section in _get_dict()["sections"]:
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

        # 收集内容层 tag 建议（除任务类型/输出格式外的所有字典字段）
        # basket 字段值 = 逗号分隔的已选 tag（可能含用户手动补充的），拆成列表；空篮子不传。
        # combo 字段单值；未设置不传，LLM 自动补全。
        tag_lines = []
        for section in _get_dict()["sections"]:
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
            + loaded_tag_block
        )

        种子 = int(kw.get("种子", 0) or 0)

        if 接口格式.startswith("Anthropic"):
            content, usage_snap = self._call_anthropic(
                api_key, system_prompt, user_msg, 模型, API基础URL, 温度, 最大token, 参考图b64, 种子
            )
        else:
            content, usage_snap = self._call_openai(
                api_key, system_prompt, user_msg, 模型, API基础URL, 温度, 最大token, 参考图b64, 种子
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
        content = _collapse_blank_lines(content)

        return (content, 负面提示词)

    # ---- Anthropic 兼容（/v1/messages + thinking disabled）----
    def _call_anthropic(
        self, api_key, system, user_msg, model, base_url, temperature, max_tokens, image_b64=None, seed=0
    ):
        url = base_url.strip().rstrip("/")
        if not url.endswith("/v1/messages"):
            url += "/v1/messages"
        # 支持多模态：image_b64 存在时 content 为 [{type:text},{type:image}]
        user_content = user_msg
        if image_b64:
            media_type = image_b64.split(";", 1)[0].split(":", 1)[1] if ";" in image_b64 else "image/jpeg"
            data = image_b64.split(",", 1)[1] if "," in image_b64 else image_b64
            user_content = [
                {"type": "text", "text": user_msg},
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
            ]
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": user_content}],
        }
        if seed:
            payload["seed"] = seed
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
                "[提示词增强器] Anthropic 接口返回空 text。请检查「最大token」是否太小、"
                "「API基础URL」是否为 Anthropic 兼容地址（.../anthropic）。"
            )
        usage = data.get("usage") or {}
        snap = llm_usage.record(usage.get("input_tokens"), usage.get("output_tokens"))
        return content, snap

    # ---- OpenAI 兼容（/chat/completions）----
    def _call_openai(
        self, api_key, system, user_msg, model, base_url, temperature, max_tokens, image_b64=None, seed=0
    ):
        url = base_url.strip().rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        # 支持多模态：image_b64 存在时 user content 为 [{type:text},{type:image_url}]
        user_content = user_msg
        if image_b64:
            user_content = [
                {"type": "text", "text": user_msg},
                {"type": "image_url", "image_url": {"url": image_b64}},
            ]
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if seed:
            payload["seed"] = seed
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        data = self._post(url, headers, payload, strip_temperature_on_error=False)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"[提示词增强器] 响应结构异常：{json.dumps(data, ensure_ascii=False)[:500]}")
        content = _strip_code_fences((msg.get("content") or "").strip())
        if not content:
            reasoning = (msg.get("reasoning_content") or "").strip()
            hint = f"（reasoning 长 {len(reasoning)} 字符，可能被 max_tokens 截断）" if reasoning else ""
            raise RuntimeError(
                f"[提示词增强器] 模型返回空 content{hint}：推理模型 reasoning 占用 token，"
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
            raise RuntimeError(f"[提示词增强器] HTTP {e.code}：{body[:500]}")
        except Exception as e:
            raise RuntimeError(f"[提示词增强器] 请求失败：{e}")


def setup_routes(server):
    """注册 GET /bsawang/prompt_enhancer/dict：返回最新篮子 meta（前端「刷新字典」按钮用）。"""
    from aiohttp import web

    async def handle_dict(request):
        data = _get_dict()
        basket_meta = {}
        for section in data["sections"]:
            for field in section["fields"]:
                if field.get("type") == "basket":
                    basket_meta[field["key"]] = {
                        "options": field.get("options", []),
                        "mutually_exclusive": field.get("mutually_exclusive", False),
                        "conflicts": field.get("conflicts", []),
                        "condition": field.get("condition"),
                        "tasks": _tasks_from_condition(field.get("condition")),
                    }
        return web.json_response({"basket_meta": basket_meta})

    server.routes.get("/bsawang/prompt_enhancer/dict")(handle_dict)


WEB_DIRECTORY = "./web"

NODE_CLASS_MAPPINGS = {
    "Prompt_Enhancer": Prompt_Enhancer,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "Prompt_Enhancer": "提示词增强器",
}
