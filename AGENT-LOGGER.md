# AGENT-LOGGER — bsawang-nodes（提示词增强器插件）

## 2026-08-22 Tag 库编辑「篮子/预设」完整编辑器 + 样式统一标准（定案）

### 背景
Tag 管理器功能定案后，用户要求库编辑做「增删改查 + 引导语 + tag 管理」的可视化编辑器，并对三个面板统一视觉标准。

### 改动
- **Tag_Library 重构为两层（篮子/预设）编辑器**：横排条目（chip，点选切换下方编辑容器）+ 末尾「+ 新增」（替换编辑区）；无 key 字段（label 自动生成）；tag 也横排+编辑区
- **后端新增篮子/tag CRUD 路由**：`save_basket` / `delete_basket` / `save_option`（tag+引导语）/ `delete_option`；`/tag/dict` 返回全量字段（guidance/option_guidance）
- **引导词去前缀**：编辑框只显纯内容，保存时拼接 `**{label}** = ` / `{tag}：` 写入
- **预设 tag 管理**：点击移除 + 全量 `篮子>tag` 选择器（子 tab + chips 点击追加）
- **样式统一标准**（三面板一致）：一个外边框 + 内部横线分隔（`.btm-box` 顶部横线）；tab 上圆下方（`8px 8px 0 0`）；条目用 chip；保存按钮绿色、删除按钮红色（双击确认）；信息栏顶部带图标默认「就绪」；图标按钮（SVG）

### 关键实现点
1. **删除 = 双击确认**：`makeIconBtn(..., true)` 首次点变亮红武装、2s 内再点执行——防误删
2. **引导语拼接在前后端约定**：显示剥前缀（`stripBasketGuidance`/`stripTagGuidance`），保存拼接回完整行
3. **正则改代码的坑**：`.*?` 非贪婪但无限界，跨块吞了别处 `});` 导致面板空白——**复杂结构改动用精确 Edit，不用正则**
4. **运行副本数据恢复**：测试改动面板写 baskets/presets 后，用项目 `cp` 全量还原（项目 = 数据真相源）

### 相关
- CRUD 经面板实测通过（增/改/查/删，含 tag 改名、引导语）；运行副本已从项目完整恢复

### 背景
提示词增强器定位「预设抽象 tag → 快速扩写」。用户要求补全「图片反推 → tag 集合 → 预设复用」链路：从参考图反推文字收集 tag 组合、存成预设、增强器一键应用。初版合并单节点 UI 太乱，拆为两个节点。

### 改动
- **Tag_Reverse「Tag 反推」**（`tag_reverse.py`）：反推文字 + LLM（结构化模板 `templates/Tag反推结构化.txt`）→ 输出 `{matched, suggestions}` JSON；面板两块：匹配结果（`篮子>tag` chip，可取消）+ 名称存预设；建议批准（逐条采纳 = 写篮子库 + 并入匹配）+ 全部采纳
- **Tag_Library「Tag 库编辑」**（`tag_library.py`）：tag/篮子/预设 三层库管理——应用预设（覆盖所涉篮子）、保存当前集合、删除预设、篮子加选项
- **预设系统**：`presets/*.json`（一个预设一个文件）+ `docs/PRESET_SPEC.md`；首批 天宫/盗梦空间；增强器「预设」tab 一键应用
- **后端路由**（`prompt_enhancer.py` setup_routes）：`GET /bsawang/tag/dict`、`GET reverse_last`、`POST save/delete_preset`、`POST add_basket_option`、`POST /bsawang/tag/reverse_run`（重跑匹配，前端未接线）、`POST /bsawang/restart`
- **重启按钮**：`web/comfyui_topbar.js` 往运行按钮区（`[data-testid="action-bar-buttons"]`）插「重启」按钮，点击 **直接 POST ComfyUI-Manager 的 `/manager/reboot`**（复用现有接口，os.execv 同命令重 exec），不自建重启路由
- 运行副本全同步（`prompt_enhancer/tag_reverse/tag_library/__init__/web/*` + presets + 模板）

### 关键实现点
1. **中间节点直出，不存后端反推状态**：Tag_Reverse 双输出 `tag集合JSON` + `反推结果`（完整 matched+suggestions），结果随工作流走，后端无 reverse 状态
2. **前端刷新 = executed 事件直出**（踩坑最终结论）：ComfyUI 1.48 的 `executed` 事件**只发给显示节点（PreviewAny），不发中间节点**；detail = `{node, display_node, output:{text:[完整JSON]}, prompt_id}`（**无 node_type 字段**）。修法：从显示节点沿输入连线**递归回溯**找上游挂了 `_bsawangApplyResult` 的 Tag_Reverse，用 `output.text[0]`（完整反推结果）渲染面板。曾错按 node_type 过滤（不存在）、曾轮询 reverse_last（2s 延迟 + 后端存状态，已废弃）
3. **⚠️ ComfyUI widget 时序坑**：ComfyUI 在 onNodeCreated **之后**才应用 widget 值（默认/已保存值会覆盖 JS 初值）——JS 写 widget 的初值会被覆盖
4. **内容锚点软耦合**（沿用）：建议采纳写篮子库走 `add_basket_option`（baskets/*.json 追加 options + 失效字典缓存）
5. **重启复用 Manager**：运行区「重启」按钮直接 `POST /manager/reboot`（ComfyUI-Manager 接口），不自建重启路由

### 相关
- [[soft-coupling-content-anchor]] · README「任务模版文件夹」表 · `docs/PRESET_SPEC.md` · `docs/tag_reverse_spike.json` / `docs/tag_library_spike.json`（测试工作流）
- ComfyUI 前端知识：执行栏 = TopBarHeader/GraphView bundle，按钮有 `data-testid`；扩展往菜单区插按钮用 `app.menu.settingsGroup.element.before(...)`

---

## 2026-08-21 系统提示词收编 templates/ 任务模版文件夹

### 背景
系统提示词散落在仓库根（`h3_system_prompt.txt` / `prompt_enhancer_system.txt` 等英文名），用户要求收进任务模版文件夹、按任务改中文名，保留 NSFW 后缀机制。

### 改动
- 新建 `templates/`，5 个 system prompt 按任务中文命名收编：
  - `prompt_enhancer_system.txt` → `templates/常规文生图.txt`（增强器通用基座，默认）
  - `prompt_enhancer_system_tag_match.txt` → `templates/文字反推tag.txt`（tag 对照模式）
  - `h3_system_prompt.txt` / `h3_system_prompt.nsfw.txt` → `templates/H3视频提示词格式化.txt` / `.nsfw.txt`
  - `prompt_enhancer_system_fusion.nsfw.txt` → 已弃用删除（老方案 VL→LLM 融合，现已 LLM 前置；无工作流引用，未收录进 templates/）
- 两个节点默认路径同步：`h3_api_prompt_formatter.py` → `templates/H3视频提示词格式化.txt`；`prompt_enhancer.py` → `templates/常规文生图.txt`
- **tag 对照模式判定标记 `tag_match` → `文字反推tag`**（新文件名无 `tag_match`，不随文件名改会静默失效）
- README 新增「任务模版文件夹（templates/）」表；docs/workflow_sample.json / BASKET_SPEC / BASKET_EDITOR_NODE_SPEC 旧文件名引用同步改
- NSFW 后缀机制保留：`.nsfw` 变体命名不变，`.gitignore` `*nsfw*` 忽略不变（本地保留、不上远程）

### 关键实现点
tag 对照模式由 **system prompt 内容自声明**触发（`prompt_enhancer.py` enhance 入口）：模板内声明需要【已加载tag】输入 → 代码检测到才注入全量已加载篮子选项。**与文件名无关**——改名/挪文件不失效（曾踩坑：初版按文件名 `tag_match` 判定，改中文名后静默失效）。

### 后续（同日）
- 检查 krea2 文生图等工作流引用：4 个工作流引用旧路径已失效，逐个修复——`kera2_文生图` / `krea2ie_参考文生图` → `templates/常规文生图.txt`；`minimaxH3_统一工作流` → `templates/H3视频提示词格式化.txt`（旧插件目录 ComfyUI-H3-APIPromptFormatter 也已失效，一并改为 ComfyUI-bsawang）；`图片反推` → `templates/文字反推tag.txt`
- 修复脚本：`H:\temp\bsawang_wf_fix.py`（幂等；已验证改后 JSON 可 parse、新值落盘 UTF-8 正确）

### 关键命令
运行副本同步：`cp` templates/ 5 个 txt + 两个 .py + README 到 `custom_nodes/ComfyUI-bsawang/`，删根 5 个旧 txt（已执行）。ComfyUI 重启后生效。

### 相关
- 本条目是仓库级重构；旧文件名引用保留在下方历史条目（历史不改）。

---

## 2026-08-19 合并插件 + 新增两个节点

### 背景
原有两个独立节点包：`ComfyUI-H3-APIPromptFormatter`（H3 API 提示词格式化，2026-08-18 建）与新增的 `ComfyUI-PromptEnhancer`（LLM API 设定器 + 提示词增强器，2026-08-19 建）。用户要求像 KJ/Muye 一样统一为**一个插件组**，且**用用户名 bsawang 命名** → 合并为 `ComfyUI-bsawang`，三个节点 CATEGORY 统一「提示词增强器」。

### 产物
- `h3_api_prompt_formatter.py` + `h3_system_prompt.txt` — 原 H3 格式化节点（CATEGORY 从「H3 API」改为「提示词增强器」）
- `llm_api_configurator.py` — 新增 LLM API 设定器（输出 `LLM_CONFIG` 自定义类型）
- `prompt_enhancer.py` + `prompt_enhancer_system.txt` — 新增提示词增强器（接 LLM_CONFIG + 用户提示词 → 任务类型/艺术风格增强）
- `__init__.py` — 合并注册三个节点

### 设计（新增节点，参考 H3 节点方案）
| 节点 | 输入 | 输出 | 要点 |
|---|---|---|---|
| LLM_API_Configurator | 接口格式/模型/URL/key环境变量/温度/max_tokens（widget） | LLM_CONFIG 对象 | 连接配置解耦；设定时即验证 key/模型/URL |
| Prompt_Enhancer | LLM（连线）+ text + 任务类型 + 艺术风格 + 系统提示词文件 | 增强后提示词 | 任务类型仅图/视频 4 类；艺术风格 20 个下拉；system prompt 外置 txt |

- LLM 调用复用 H3 方案：urllib 双接口、thinking disabled、key 不落明文、错误透传
- 增强规则来自描述圣经：T2I→四层结构 / I2I→只补缺失保一致 / T2V→静态基础+时间轴 / I2V→只写运动增量（外置 prompt_enhancer_system.txt）
- 艺术风格下拉拆英文词（widget 显示「仙侠(xianxia ethereal)」，传 LLM 拆出英文风格词）

### 验证（2026-08-19）
- 两个新节点跑通（用户确认）；合并后 ComfyUI Python 包加载 3 节点全部注册成功
- 部署方式：复制 src/ 到 `custom_nodes/ComfyUI-bsawang/`（扁平结构，无 src/ 子目录）

### 关键命令
无（ComfyUI 重启后生效）。

### 相关
- 原 H3 节点历史见下方「2026-08-18」节；知识库 `assets/nodes/bsawang-nodes/` + `knowledge/33`。

---

## 2026-08-18 原 H3 API 提示词格式化节点（迭代史，合并前留痕）

### 背景
`minimaxH3_统一工作流.json` 的视频反推链用闭源节点 TE_H3_Prompt_Enhancer（TE_MAN/.pyd）做 H3 提示词格式化，连续踩坑：构图信息丢失、时间戳缺失、`[Shot 4-9]` 压缩、凭空生成 `<Video N>`（884 LoadVideo 为 mute、无真实视频参考时）。闭源 .pyd 规则锁死不可改 → 换 API LLM 方案。

### 产物
- `h3_api_prompt_formatter.py` — 节点本体（urllib，双接口：Anthropic `/v1/messages` 默认 + OpenAI `/chat/completions`）
- `h3_system_prompt.txt` — 角色框架段 + 工作流附加约束 + H3 官方规范（外置可改）
- `__init__.py` — 注册

### 关键坑（迭代记录）
1. **DeepSeek OpenAI 兼容接口拒 NSFW** → 改走 Anthropic 兼容接口 + 角色框架 system prompt（不拒）
2. **v4-flash 是推理模型**（reasoning 可达 2 万字级，吃光 max_tokens 后 content 为空）→ `thinking: {"type": "disabled"}` 必开
3. **模型名**：`deepseek-chat` 是旧别名，账号实际模型 v4-flash/v4-pro → 默认 v4-flash
4. **Vue ComboWidget 动态下拉坑**：裸 fetch 被 CSRF 拦 → 放弃下拉+刷新，模型改 text 输入
5. **时长幻觉**：反推提示词无总时长约束 → 15s 视频被编到 326s → 加【视频总时长】前置 +「不得超出」
6. **格式化器发明悬空标签** → 加「只使用输入已有标签」约束（外置 txt）
7. **`[Shot 1] At 00:00.000`**：H3 规范开场镜不加时间戳 → 约束外置 txt
8. **系统提示词外置**：10KB 文本从 widget 内嵌改为文件路径

### 验证（2026-08-18）
NSFW 样例端到端：六段齐全、无 `<Video N>`、不拒、不编造外观、Shot1 无时间戳、后续镜头递增时间戳、无新悬空标签。
