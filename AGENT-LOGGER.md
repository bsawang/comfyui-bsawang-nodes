# AGENT-LOGGER — bsawang-nodes（提示词增强器插件）

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
