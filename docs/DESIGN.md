# bsawang-nodes 插件设计文档（完整版，含 Tag 管理器与预设系统）

> ComfyUI 自定义插件 `bsawang/comfyui-bsawang-nodes`，本仓库即插件，clone 到 `ComfyUI\custom_nodes\` 即用。
> 定位：**提示词增强器 + Tag 管理器**——预设抽象 tag → 快速扩写，从参考图反推收集 tag、存预设、一键应用。
> 运行副本：`H:\ComfyUI_Windows_portable\ComfyUI\custom_nodes\ComfyUI-bsawang\`（改代码后手动同步）。
> 本文为**设计文档**（整合原 DESIGN / TAG_MANAGER_SPEC），**给开发/维护者看**：完整节点设计、架构、机制与踩坑。
> 制作篮子和预设的 JSON **规约**另见 `PRESET_SPEC.md`（预设）、`BASKET_SPEC.md`（篮子）——供 **agent 制作时参考**，与本文不冲突（本文讲「怎么设计」，规约讲「JSON 怎么写」）。

## 目录
1. 项目概述
2. 总体架构
3. 节点总览
4. 共享基础设施
5. 节点详细设计
6. 预设系统（presets/，规约见 PRESET_SPEC.md）
7. 前端直出触发机制
8. 数据持久化
9. NSFW 策略
10. 部署与同步
11. 边界与未来工作
12. 踩坑记录

---

## 1. 项目概述

| 项 | 内容 |
|---|---|
| 节点数 | 5（全部 `CATEGORY = bsawang/提示词增强器`）|
| 技术栈 | Python（urllib，零第三方 HTTP 依赖）+ 前端原生 JS（`addDOMWidget` DOM 面板）|
| 模型后端 | DeepSeek（Anthropic 兼容）/ GLM / Gemini 等，通过「LLM API 设定器」解耦 |
| 数据 | `dict.json`（字典骨架）+ `baskets/`（篮子）+ `presets/`（预设）+ `templates/`（system prompt）+ `tasks.json` |
| 内存 | 无显式后台反推状态（中间节点直出）；仅 `llm_usage.py` 会话级 token 统计 |

**一句话**：把「用户简单提示词」经「预设 tag 抽象」扩写为完整生成提示词；复杂风格（天宫等）用预设捆绑多维度 tag，一键套用。

## 2. 总体架构

```
┌────────────────────────── ComfyUI 前端（web/） ──────────────────────────┐
│  prompt_enhancer_ui.js（增强器四栏面板 + 预设 tab）                       │
│  tag_manager_ui.js（Tag_Reverse 两块面板 + Tag_Library 三层面板）         │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ executed 事件直出（Tag_Reverse 面板刷新，回溯上游）
┌───────────────┴──────────────────────────────────────────────────────────┐
│                      ComfyUI 后端（*.py）                                │
│  节点：H3_API_PromptFormatter / LLM_API_Configurator / Prompt_Enhancer   │
│        / Tag_Reverse / Tag_Library                                       │
│  基础设施：llm_usage（token 统计）· setup_routes（字典/预设/篮子路由）    │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ 读写
┌───────────────┴───────────────┐
│  dict.json · baskets/ ·       │
│  presets/ · templates/ ·      │
│  tasks.json                    │
└───────────────────────────────┘
```

**核心机制**：
- **LLM 连接解耦**：`LLM_API_Configurator` 输出 `LLM_CONFIG` 自定义类型，下游节点连线接收——换 LLM 只改设定器
- **字典动态合并**：`dict.json` 定义 section 骨架 + 非篮子控件，`baskets/*.json` 按 section 合并注入篮子——增删篮子 = 增删文件
- **模板外置**：system prompt 收在 `templates/`，改 txt 即生效。反推由专用节点 Tag_Reverse 承担（`Tag反推结构化.txt`），增强器不再做 tag 对照
- **中间节点直出**：Tag_Reverse 结果随输出流向下游，面板靠 executed 事件直出，无轮询无后台状态

## 3. 节点总览

| 节点 | 显示名 | 输入 → 输出 | 定位 |
|---|---|---|---|
| H3_API_PromptFormatter | H3 API 提示词格式化 | `LLM + 反推文字 + 任务类型 + 时长` → `提示词` | H3 视频提示词六段式格式化 |
| LLM_API_Configurator | LLM API 设定器 | `7 个配置 widget` → `LLM_CONFIG` | LLM 连接工厂 |
| Prompt_Enhancer | 提示词增强器 | `LLM + 用户提示词 + 四栏设置 + 篮子` → `提示词 + 负面提示词` | 核心：预设 tag → 完整提示词 |
| Tag_Reverse | Tag 反推 | `LLM + 反推文字 + 模板` → `tag集合JSON + 反推结果` | 反推文字 → 结构化 tag 集合 + 建议 |
| Tag_Library | Tag 库编辑 | `（无数据输入）` → `tag集合JSON` | tag/篮子/预设 三层库管理 |

## 4. 共享基础设施

### 4.1 LLM_CONFIG 连接体系

`LLM_API_Configurator` 输出的 `LLM_CONFIG`（Python dict / ComfyUI 自定义类型）字段：

```python
{
    "接口格式": "Anthropic 兼容" | "OpenAI 兼容",
    "模型": "deepseek-v4-flash",
    "API基础URL": "https://api.deepseek.com/anthropic",
    "APIKey环境变量": "ANTHROPIC_AUTH_TOKEN",   # key 从环境变量读，不落工作流明文
    "温度": 0.4,
    "最大token": 8192,
    "支持视觉": False,                           # 增强器接图时据此判断
}
```

**LLM 调用（urllib，双接口）**：
- **Anthropic 兼容** `/v1/messages`：`thinking: {"type":"disabled"}` 必开（推理模型不开会吃光 max_tokens）；header `x-api-key`
- **OpenAI 兼容** `/chat/completions`：`Authorization: Bearer`
- 多模态：`支持视觉=是` 且节点有参考图 → 图片 base64 转 `image` / `image_url` 传参
- 错误透传：HTTP/结构异常/空 content 均 `raise` → 节点变红

`llm_usage.py`：会话级 token 累计（输入/输出/调用次数），`GET /llm_usage/last` 读「本次」，消费节点标题栏显示用量徽章。

### 4.2 字典系统（dict.json + baskets/）

- `dict.json`：section 骨架（type/type_info/content/output）+ 非篮子控件（任务类型/种子/视频时长/用户提示词/输出设置）
- `baskets/*.json`：一个篮子一个文件（主题风格/构图风格/光线/景别/氛围/色彩/…含 nsfw-* 系列），含 `options/guidance/option_guidance/conflicts/mutually_exclusive/condition/gate`
- **动态合并**：启动 + 刷新时按 `section.id` 定位、fields 按 key 去重追加；mtime 缓存；`GET /bsawang/prompt_enhancer/dict` 供前端「刷新字典」按钮
- 篮子格式与制作规约见 `BASKET_SPEC.md`

### 4.3 模板系统（templates/）

- 所有 system prompt 收在 `templates/`，按任务中文命名，`系统提示词文件` widget 默认指向，改 txt 即生效、不重启
- **内容锚点软耦合**（历史）：曾用模板声明【已加载tag】触发增强器注入全量篮子选项（tag 对照模式）；该模式已被 Tag_Reverse 节点取代，2026-08-22 移除
- NSFW 变体 `{key}.nsfw.txt` 按 `.gitignore *nsfw*` 忽略（本地保留不上远程）

### 4.4 前端面板体系

- 全部用 `node.addDOMWidget` 内嵌 DOM 面板（非弹窗），隐藏 state widget 做序列化点
- 控件词汇：chip 多选、建议行 + 采纳按钮、文本输入、分层切换、tab 切换
- **直出触发**（Tag_Reverse）：见 §7

### 4.5 后端路由

| 路由 | 方法 | 用途 |
|---|---|---|
| `/bsawang/prompt_enhancer/dict` | GET | 增强器篮子 meta + 预设（刷新字典 / 预设 tab）|
| `/bsawang/tag/dict` | GET | 全部篮子（完整）+ 预设池 |
| `/bsawang/tag/save_preset` | POST | 存预设 |
| `/bsawang/tag/delete_preset` | POST | 删预设 |
| `/bsawang/tag/add_basket_option` | POST | 篮子加选项（写 baskets/*.json + 失效缓存）|
| `/llm_usage/last` | GET | 本次 token 用量（llm_usage 模块）|

## 5. 节点详细设计

### 5.1 LLM_API_Configurator

| widget | 类型 | 默认 | 说明 |
|---|---|---|---|
| 接口格式 | combo | Anthropic 兼容 | 决定 urllib 走 /v1/messages 还是 /chat/completions |
| 模型 | STRING | deepseek-v4-flash | 手填模型名 |
| API基础URL | STRING | https://api.deepseek.com/anthropic | API 服务地址 |
| APIKey环境变量 | STRING | ANTHROPIC_AUTH_TOKEN | 从环境变量读 key，不落工作流明文 |
| 温度 | FLOAT | 0.4 | — |
| 最大token | INT | 8192 | — |
| 支持视觉 | combo | 否 | 是否传图给 LLM（GLM/Qwen-VL/Gemini 选是）|

设定时校验模型/URL 非空；key 缺失时下游调用显式报错。输出 `LLM_CONFIG`。

### 5.2 H3_API_PromptFormatter

- 输入：`LLM` + `反推文字` + `任务类型`（全参考/T2VA/I2VA/FL2VA/L2VA）+ `视频时长` + `系统提示词文件`
- 输出：`提示词`（H3 六段式/base 三字段）
- 模板 `templates/H3视频提示词格式化.txt` 外置「UI 任务类型 → 输出结构映射」段，改即生效
- 替代闭源 TE_H3_Prompt_Enhancer

### 5.3 Prompt_Enhancer（核心）

**四栏 UI**（字典驱动动态生成）：

| 栏 | 字段 |
|---|---|
| 类型选择 | 任务类型（文生图/图生图/文生视频/图生视频）|
| 类型基础信息设置 | 种子、视频时长（仅视频类型）|
| 内容设置 | 用户提示词 + 内容篮子（主题/构图/光线/氛围/身材/… 按任务类型动态显隐）|
| 输出设置 | 输出格式（自然语言/Tag/混合）、结构、语言、长度、带负面提示词 |

固定项：`LLM` 连线 + `系统提示词文件`。输出 `提示词` + `负面提示词`。

**核心机制**：
- 预制篮子多选（隐藏 state widget JSON 序列化）；tag 是建议，艺术/主题风格是强约束
- 约束自检：mutually_exclusive / conflicts / 跨字段 gate，拼接前校验报错
- （tag 对照模式已移除——反推由 Tag_Reverse 节点承担，增强器不再注入全量 tag 池）
- 任务类型动态显隐 + 增强要点外置（tasks.json）
- **预设 tab**（index 0）：读 /dict 的 presets，点预设覆盖所涉篮子
- 负面词基础库（质量/解剖/安全三类）+ LLM 补充本次特定项
- token 用量徽章（onExecuted → /llm_usage/last）

### 5.4 Tag_Reverse「Tag 反推」（中间节点直出）

**输入 / 输出**：

| 输入 | 类型 | 说明 |
|---|---|---|
| 反推文字 | STRING multiline | VL 反推输出 / 多维描述 |
| 系统提示词文件 | STRING | 默认 `templates/Tag反推结构化.txt`，改 txt 即生效 |
| LLM | LLM_CONFIG（连线）| 接「LLM API 设定器」|
| bsawang_tag_state | STRING hidden | 当前 tag 集合（matched），随工作流序列化 |

| 输出 | 类型 | 说明 |
|---|---|---|
| **tag集合JSON** | STRING | `{篮子key: [tag]}`（matched），喂下游增强器/预设 |
| **反推结果** | STRING | 完整 `{matched, suggestions, 反推文字}`，供面板直出 + 人读 |

**执行逻辑（process）**：
1. 校验 LLM 连接 + 反推文字非空
2. 加载全量 tag 池（dict.json + baskets/ 全部篮子 options）
3. LLM 调用（结构化模板）：`【反推文字】+【已加载tag】` → 输出严格 JSON
4. 解析 + 校验：matched 只留池内存在的 tag；suggestions 的篮子必须在池内
5. 双输出返回（不存后端状态）

**面板（两块布局）**：
```
┌ 匹配结果（来自反推，点击取消）────────┐
│  [主题风格>仙侠] [构图风格>天宫] ...  │
│  [预设名称____] [保存为预设]          │
├────────────────────────────────────┤
│  tag 建议批准（采纳=写入篮子库+并入匹配）│
│  女性服饰>皮衣 ↳黑色亮面紧身皮衣 [采纳] │
│  [全部采纳]                          │
└────────────────────────────────────┘
```
- 匹配结果：`篮子>tag` 单个 chip，点击取消
- 保存为预设：把当前 matched 存为 `presets/{名称}.json`
- 建议批准：**采纳 = `POST /bsawang/tag/add_basket_option`（写篮子库）+ 并入匹配**；「全部采纳」批量

### 5.5 Tag_Library「Tag 库编辑」（三层库管理）

| 输入 | 说明 |
|---|---|
| bsawang_tag_state | 当前 tag 集合（hidden，序列化）|

| 输出 | 说明 |
|---|---|
| tag集合JSON | 当前集合（编辑/应用预设后）|

面板三层（层切换）：

| 层 | 功能 |
|---|---|
| tag | 当前集合跨篮子 chips 增删（点击移除）|
| 篮子 | 篮子池列表（dict+baskets），展开看 options、加新选项（`POST add_basket_option`）|
| 预设 | presets 列表：**应用**（覆盖所涉篮子填集合）、**删除**、**保存当前集合为预设** |

## 6. 预设系统（presets/）

预设 = 跨多个篮子的一组 tag 捆绑（一键填篮子），复杂风格（天宫）用它打包多维度。**完整格式/约束/质检/制作流程见 [`PRESET_SPEC.md`](PRESET_SPEC.md)**，本节只记设计要点：

- 一个预设一个文件 `presets/{key}.json`：`{key, label, description, tags}`；NSFW 变体 `{key}.nsfw.json`
- **应用 = 覆盖所涉篮子**（替换该篮子当前选择），未涉及的篮子不动，应用后可微调
- `tags` 的篮子 key 必须在当前字典内（不存在 → 应用跳过并提示）；tag 值不必已在篮子 options（应用只填状态，LLM 运行时把选中的 tag 传给增强器）
- **消费方**：Tag_Reverse（存为预设）、Tag_Library（应用/存/删）、Prompt_Enhancer（「预设」tab 应用，读 /dict 的 presets 字段）
- 首个预设：天宫、盗梦空间

## 7. 前端直出触发机制（关键架构决策）

**背景**：Tag_Reverse 是中间节点，需要面板在运行后即时显示结果，但 ComfyUI 1.48 的 `onExecuted` 不触发、`executed` 事件只发显示节点。

**方案**（最终）：
```
Tag_Reverse 执行 → 输出流向 PreviewAny
→ PreviewAny 触发 executed 事件（detail = {node, display_node, output:{text:[完整JSON]}, prompt_id}，无 node_type）
→ 从 PreviewAny 沿 input.link → links[link].origin_id 递归回溯
→ 找到挂了 _bsawangApplyResult 的 Tag_Reverse
→ 读 output.text[0]（完整反推结果 JSON）→ applyResult 渲染两块面板
```

**被否方案**（记录）：后端存 reverse_last + 2s 轮询（有延迟 + 后台状态，用户否）；按 `detail.node_type` 过滤（字段不存在，永远挡死）；`onExecuted`（本版本不触发）。

## 8. 数据持久化

| 数据 | 位置 | 生命周期 |
|---|---|---|
| 篮子定义 | `baskets/*.json` | 持久（git 跟踪；nsfw-* 忽略）|
| 字典骨架 | `dict.json` | 持久（git 跟踪）|
| 预设 | `presets/*.json` | 持久（git 跟踪；nsfw 变体忽略）|
| 任务增强要点 | `tasks.json` | 持久（git 跟踪）|
| system prompt | `templates/*.txt` | 持久（git 跟踪；nsfw 变体忽略）|
| token 统计 | `llm_usage.py` 进程内 | 会话级（重启清空）|
| 反推中间结果 | 节点输出 | 不持久（中间节点直出）|

## 9. NSFW 策略

- 产出文件含 NSFW → `-NSFW` / `.nsfw` 后缀 + `tags: [nsfw]`
- `.gitignore` 含 `*nsfw*`：NSFW 文件本地 git 保留、不上远程
- 命名约定：模板 `.nsfw.txt`、预设 `.nsfw.json`、篮子 `nsfw-*.json`
- 发布时按后缀/标签筛选（子目录/稀疏检出/分仓库）

## 10. 部署与同步

- **仓库**：`assets/nodes/bsawang-nodes/`（独立 git 仓库，aigc-study 内嵌）
- **运行副本**：`custom_nodes/ComfyUI-bsawang/`——改代码后手动同步（py/js/templates/baskets/presets），重启 ComfyUI 生效
- **前端 JS 改动**：仅 F5 强刷（Ctrl+Shift+R）即可
- **测试工作流**：`docs/tag_reverse_spike.json`、`docs/tag_library_spike.json`、`docs/workflow_sample.json`

## 11. 边界与未来工作

| 项 | 状态 |
|---|---|
| 预设「应用」跨节点共享 | presets/ 文件级共享（Step 1）|
| Tag_Reverse → 增强器 tag 集合**直连** | Step 2（增强器加输入端口，现输出已具备，增强器端待接）|
| 「面板重跑匹配」（reverse_run）| 预留思路，未接线（当前直出随工作流跑）|
| 多 Tag_Reverse 节点同一工作流 | 各自独立面板，互不干扰（executed 事件按节点回溯）|
| 篮子编辑写回完整校验 | 库编辑「编辑篮子」目前只加选项，增删/改 guidance 待接 |

## 12. 踩坑记录（汇总）

| 坑 | 结论 |
|---|---|
| executed 事件 detail 无 `node_type` | 别按它过滤，按 node.id + `_bsawangApplyResult` 判断 |
| executed 只发显示节点 | 中间节点要回溯上游 |
| STRING 输出在 `output.text[0]` | 不是 RETURN_NAMES 命名 |
| ComfyUI 在 onNodeCreated 后才应用 widget 值 | JS 写 widget 初值会被覆盖（用 widget callback 再确保）|
| 推理模型 max_tokens 吃光 | Anthropic 接口 `thinking: disabled` 必开 |
| 标签/文件名耦合静默失效 | 用内容锚点软耦合（模板声明触发）|
| 运行副本落后仓库 | 改完必须手动同步 + diff 核对 |
| 轮询刷新有延迟 + 后台状态 | 中间节点用直出（executed 回溯），不轮询 |
