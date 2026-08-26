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

# 图生图类任务：素材进定义行 + 行内联标记（参考匹配）
IMAGE_TASKS = {"图生图(I2I)", "参考生视频"}

# 角色提取边界（角色隔离）：每个角色反推时只看图提取对应维度，不透全图/不越界
角色提取提示 = {
    "风格参考": "只看图提取画风/光影/质感/色彩倾向，不透场景内容",
    "环境参考": "只看图提取环境类型，不提取氛围/风格/具体元素",
    "角色参考": "只看图提取人物外观，不透环境/风格",
    "构图参考": "只看图提取构图/机位/视角",
    "动作参考": "只看图提取动作/姿态",
    "道具参考": "只看图提取道具/物体（款式/材质/颜色），不提取主体/环境/风格",
    "音频参考": "只提取声音/氛围",
    "底图": "看图提取人物外观+环境+构图+光线（整图为视觉底，动作由参考视频提供）",
}

# 视频/音频素材的素材行提示（按角色描述其用途；与角色提取提示区分——后者是看图提取）
视频角色提示 = {
    "视频编辑源": "视频为编辑源：保留原视频动作/场景/机位/节奏，仅替换人物外观",
    "动作参考": "视频为动作来源：主体执行与视频完全一致的动作",
    "音频参考": "音频为声音来源：按系统提示词音频说明使用",
}

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

# 任务类型 → 系统提示词模板（内置自动切换；系统提示词文件 widget 留空时按此选）
任务模板 = {
    "文生图(T2I)": "常规文生图.txt",
    "图生图(I2I)": "常规文生图.txt",
    "文生视频(T2V)": "文生视频.txt",
    "参考生视频": "参考生视频.txt",
}


def _auto_system_prompt(任务类型: str) -> str:
    """按任务类型自动选系统提示词文件；未知类型回落 常规文生图。"""
    tpl = 任务模板.get(任务类型, "常规文生图.txt")
    return str(NODE_DIR / "templates" / tpl)


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
        # 从外部字典动态构建各栏 widget；系统提示词已内置（按任务类型自动选模板）
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
        optional = {}
        for i in range(1, 17):
            optional[f"素材{i}"] = (
                "MATERIAL",
                {"tooltip": f"素材{i}：素材封装器输出（角色/标签/反推/内容），按素材使用说明（常规文生图.txt）使用；未接则忽略"},
            )
        return {"required": required, "optional": optional}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("提示词", "负面提示词")
    FUNCTION = "enhance"
    CATEGORY = "bsawang/提示词增强器"

    def enhance(self, LLM, 用户提示词, **kw):
        text = (用户提示词 or "").strip()
        # 用户提示词作为补充说明（主体/场景等细节，素材已定义的由素材提供）；素材驱动时可空

        # 从 LLM_CONFIG 取连接配置
        if not isinstance(LLM, dict) or not LLM.get("模型"):
            raise ValueError("[提示词增强器] LLM 连接无效：请接「LLM API 设定器」的输出。")
        支持视觉 = LLM.get("支持视觉", False)

        # 任务类型（必填）：决定图生图强制素材、前置提示词与素材标记
        任务类型 = kw.get("任务类型", "文生图(T2I)")
        is_image_task = 任务类型 in IMAGE_TASKS

        # 收集素材（素材封装器输出，自增口 素材1..素材16）；按配置构建注入
        # 反推=是的图片 → 附加给 LLM 看图反推；反推=否 → 仅角色定义（<标签> 标记，不处理图像）
        素材列表 = []
        for i in range(1, 17):
            m = kw.get(f"素材{i}")
            if isinstance(m, dict) and m.get("类型"):
                素材列表.append(m)
        # 用户提示词与素材都空 → 无事可做；有素材则继续（素材驱动，用户提示词仅补充）
        if not text and not 素材列表:
            return ("", "")
        # 素材自检：角色↔类型一致性（特殊模式前置校验）
        for m in 素材列表:
            if m.get("角色") == "视频编辑源" and m.get("类型") != "视频":
                raise ValueError(f"[提示词增强器] 素材自检失败：角色「视频编辑源」必须接视频素材，当前是「{m.get('类型')}」")
            if m.get("角色") == "底图" and m.get("类型") != "图片":
                raise ValueError(f"[提示词增强器] 素材自检失败：角色「底图」必须接图片素材，当前是「{m.get('类型')}」")
        # 图生图：强制接图片素材（源图锚点）
        if 任务类型 == "图生图(I2I)" and not any(m.get("类型") == "图片" for m in 素材列表):
            raise ValueError("[提示词增强器] 图生图需要接图片素材（源图锚点）：请用「素材封装器」接图片")
        # 参考生视频：至少一个参考素材（图片/视频/音频任一）
        if 任务类型 == "参考生视频" and not 素材列表:
            raise ValueError("[提示词增强器] 参考生视频需要至少一个参考素材（图片/视频/音频任一）：请用「素材封装器」接素材")
        素材行 = []
        素材定义输出 = []
        附加图片 = []
        图片编号 = 0
        视频编号 = 0
        音频编号 = 0
        for m in 素材列表:
            t = m.get("类型")
            role = m.get("角色") or "素材"
            label = (m.get("标签") or "").strip() or role
            rev = bool(m.get("反推"))
            # 图生图：图片素材编号（图片1..N）+ 前置素材定义「图片N<标签>：角色」输出
            # 文生图：素材不标 <标签>（无下游参考匹配），纯内容注入
            if t == "图片" and is_image_task:
                图片编号 += 1
                素材定义输出.append(f"图片{图片编号}<{label}>：{role}")
            if t == "文本":
                if is_image_task:
                    素材行.append(f"文本素材·{role}：{m.get('数据')}")
                else:
                    素材行.append(f"【素材·{role}】：{m.get('数据')}")
            elif t == "图片":
                # 角色提取边界（角色隔离）：按角色只看图提取对应维度，不透全图
                提取提示 = 角色提取提示.get(role, f"看图提取「{role}」内容")
                if rev and 支持视觉:
                    try:
                        附加图片.append(llm_utils.image_to_base64(m.get("数据")))
                    except Exception as e:
                        raise ValueError(f"[提示词增强器] 素材「{role}」图片转 base64 失败：{e}")
                    if is_image_task:
                        素材行.append(f"图片{图片编号}<{label}>：{role}（源图，{提取提示}；可结合 tag/用户输入修改）")
                    else:
                        素材行.append(f"【素材·{role}】：附参考图（{提取提示}；可改属性 tag>用户输入>素材）")
                else:
                    if is_image_task:
                        素材行.append(f"图片{图片编号}<{label}>：{role}（源图，仅角色定义，{提取提示}，可修改）")
                    else:
                        素材行.append(f"【素材·{role}】：仅角色定义（未反推，不描述图像内容）")
            elif t in ("视频", "音频"):
                类型名 = "视频" if t == "视频" else "音频"
                if is_image_task:
                    # 图生图/参考生视频：视频/音频素材带标签进素材定义输出，供下游格式化器映射 <Video N>/<Audio N>
                    if t == "视频":
                        视频编号 += 1
                        编号 = 视频编号
                    else:
                        音频编号 += 1
                        编号 = 音频编号
                    素材定义输出.append(f"{类型名}{编号}<{label}>：{role}")
                    素材行.append(f"{类型名}{编号}<{label}>：{role}（{视频角色提示.get(role, '按系统提示词素材说明使用')}；未提及则不输出）")
                else:
                    素材行.append(f"【素材·{role}】：{t}素材（按素材使用说明，未提及则不输出）")

        # 系统提示词内置：按任务类型自动选模板（widget 已删除）
        system_prompt = _read_system_prompt(_auto_system_prompt(任务类型))

        # 任务类型行（前置提示词按任务区分：图生图源图为内容基础、按素材角色提取、可修改）
        if is_image_task:
            任务类型行 = (
                f"任务类型：{任务类型}（图生图：源图为内容基础，按素材定义角色提取，可修改；"
                "⚠️ 输出提示词正文即可（素材定义由系统前置，勿重复输出、勿把素材定义/分类名/图片/视频/音频编号写进正文）；"
                "提及对应角色/风格/内容时只用 <标签> 内联指代（如 <123>），不重复「角色参考/风格参考」等分类名或「图片N」；"
                "禁止「保持同一角色」「保留原图」「将X改为Y」等编辑声明/前后对比，直接用 <标签> 开头描述主体）"
            )
        else:
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
                if key in ("用户提示词", "任务类型"):
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

        # 视频编辑源场景：镜头结构与时间轴沿源视频，跳过时间轴/分镜增强与结构指令
        有视频编辑源 = any(m.get("角色") == "视频编辑源" for m in 素材列表)
        if 有视频编辑源:
            增强要点说明 = "（视频编辑源场景：跳过时间轴/分镜增强——镜头结构与时间轴沿源视频，不分镜、不写时间段切分，见系统提示词「视频编辑源」节）"
            if 输出结构 == "分段标题":
                结构说明 = "存在「视频编辑源」素材：保留 [主体]/[环境场景]/[构图]/[光线]/[氛围] 块结构，但**不写 [时间轴]、不分镜、不写时间段切分**（镜头结构沿源视频，见系统提示词「视频编辑源」节）。"
            else:
                结构说明 = "存在「视频编辑源」素材：自然段落描述编辑结果，**不分镜、不写时间段切分**（见系统提示词「视频编辑源」节）。"
        else:
            增强要点说明 = 增强要点
            结构说明 = "输出结构为『分段标题』时，按 [主体]/[环境场景]/[构图]/[光线]/[氛围]/[风格] 分块（视频加 [时间轴]）；『连贯一段』时输出自然段落——视频场景按时间/镜头推进组织，写明镜头切换点与时间段（如「0-2秒…；2-5秒切至…」），不得写成单镜头连续描述；图片场景不受此限。"

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
            f"{增强要点说明}\n\n"
            "上面给出的 tag 是给 LLM 的建议，请结合用户提示词自由组织、合理增补细节。"
            "按系统提示词的「按输出格式组织」段与上方增强要点执行。"
            f"{结构说明}"
            f"{负面说明}"
            "直接输出增强后的提示词本身，不要任何前言、解释或 markdown 代码块。\n\n"
            + ((f"【素材定义】\n" if is_image_task else f"【素材】\n") + "\n".join(素材行) + "\n\n" if 素材行 else "")
            + f"【用户提示词】\n{text}"
        )

        种子 = int(kw.get("种子", 0) or 0)

        content, _usage = llm_utils.call_llm(
            LLM, system_prompt, user_msg,
            image_b64=附加图片 or None, seed=种子, error_prefix="[提示词增强器] ",
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
                content = self._second_pass_verify(LLM, tag_lines, content, text, 种子)
                content = llm_utils.collapse_blank_lines(content)
            except Exception:
                pass  # 二次优化失败不阻断，回落第一次输出

        # 图生图：前置素材定义（图片N<标签>：角色）到最终提示词，供下游参考匹配
        if is_image_task and 素材定义输出:
            content = "\n".join(素材定义输出) + "\n\n" + content
        return (content, 负面提示词)

    def _second_pass_verify(self, LLM, tag_lines, content, text, 种子):
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
