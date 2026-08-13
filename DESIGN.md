---
name: SR Supplier Risk Monitoring
description: Google AI Studio 原版视觉语言下的本地供应商风险控制台
colors:
  primary-deep: "#007aff"
  primary: "#007aff"
  primary-soft: "#eef6ff"
  primary-hover: "#dbeafe"
  canvas: "#f1f5f9"
  surface: "#ffffff"
  ink: "#101d28"
  text-secondary: "#424751"
  text-muted: "#727782"
  outline: "#c2c6d2"
  critical: "#ff3b30"
  warning: "#ff9500"
  medium-risk: "#007aff"
  low-risk: "#8e8e93"
  success: "#34c759"
  error-container: "#ffdad6"
  dark-panel: "#0b131e"
  dark-raised: "#0e1726"
typography:
  headline:
    fontFamily: "Noto Sans SC, Microsoft YaHei, sans-serif"
    fontSize: "24px"
    fontWeight: 900
    lineHeight: 1.25
  title:
    fontFamily: "Noto Sans SC, Microsoft YaHei, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.35
  body:
    fontFamily: "Noto Sans SC, Microsoft YaHei, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Noto Sans SC, Microsoft YaHei, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.4
  data:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.4
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "16px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  xxl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.primary-deep}"
    textColor: "{colors.surface}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: "10px 20px"
  button-primary-hover:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "10px 20px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary-deep}"
    rounded: "{rounded.lg}"
    padding: "8px 16px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "16px"
  input:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "10px 12px"
  chip:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.primary-deep}"
    rounded: "{rounded.full}"
    padding: "4px 8px"
---

# Design System: SR Supplier Risk Monitoring

## 1. Overview

**Creative North Star: “可追溯的风险控制台”**

本设计系统以 Google AI Studio 已确认并迁移到 React 的版本为唯一视觉权威。它服务于采购经理的高密度日常工作：用稳定的蓝灰 App Shell 承载真实业务数据，用明确的 P1–P4 语义色突出风险，用紧凑但可扫描的卡片、表格和状态条解释证据链。

整体气质专业、可信、克制。界面不是营销页面，也不是未来感 AI 展示屏；智能能力通过模型状态、工具调用和证据卡片透明呈现。新功能必须看起来像从现有系统自然生长出来，而不是来自另一个模板。

**Key Characteristics:**

- 240px 桌面侧栏、粘性顶栏、最大 1440px 内容区构成稳定 App Shell。
- 1024px 以下切换为移动端顶栏与固定底部导航，内容保持单列和触控可达。
- 蓝灰中性色承担结构，鲜艳颜色只用于选中、风险等级、状态和主要操作。
- 真实数据密度优先，卡片、表格、弹窗和 Agent 消息保持统一组件语言。
- Motion 只表达切页、选中、展开、反馈与服务状态，并支持减少动效。

## 2. Colors

这是一个冷静的蓝灰控制台色板：深海军蓝建立信任，柔和冰蓝组织层级，红橙蓝灰严格映射 P1–P4 风险。

### Primary

- **系统蓝** (#007aff)：品牌标识、主要按钮、输入焦点、当前导航和关键链接。
- **悬停蓝** (#0062cc)：主要按钮悬停和高亮反馈。
- **信息冰蓝** (#eef6ff)：侧栏、只读提示、选中区域和轻量信息容器。
- **交互浅蓝** (#d6e4f3)：悬停反馈，不作为大面积背景。

### Secondary

- **P1 警报红** (#ff3b30)：仅用于重大风险、危险操作和错误强调。
- **P2 警戒橙** (#ff9500)：高风险和警告状态。
- **P3 分析蓝** (#007aff)：中度风险，与系统蓝保持连续性。
- **P4 稳定灰** (#8e8e93)：低风险和次要等级。
- **运行绿** (#34c759)：服务正常、监控启用和成功反馈。

### Neutral

- **冷白画布** (#f1f5f9)：浅色模式页面背景与输入底色。
- **纯白表面** (#ffffff)：卡片、顶栏、弹窗和主要内容容器。
- **深海墨色** (#101d28)：主文字及深色模式基础表面。
- **次级墨灰** (#424751)：正文说明、非选中导航和次级信息。
- **静默灰** (#727782)：时间戳、占位信息和辅助标签。
- **结构描边** (#c2c6d2)：卡片、表格、输入和分隔线。
- **深色面板** (#0f172a) 与 **深色抬升面** (#1e293b)：深色模式中的层级容器。

### Named Rules

**风险色专用规则。** 红、橙、P3 蓝和 P4 灰只表达风险等级或对应系统状态，不用于装饰。

**一屏一主操作规则。** #007aff 用于当前屏幕最重要的操作；次要动作使用白底描边或透明背景。

## 3. Typography

**Display Font:** Noto Sans SC（Microsoft YaHei 回退）  
**Body Font:** Noto Sans SC（Microsoft YaHei 回退）  
**Label/Mono Font:** IBM Plex Mono（系统等宽字体回退）

**Character:** 中文无衬线字体提供稳定、清晰的企业工具感；等宽字体只用于风险分、ID、时间、配额和技术状态，使数据与说明文字快速区分。

### Hierarchy

- **Headline**（900，24px，1.25）：页面主标题和风险详情主体名称；移动端不小于 22px。
- **Title**（700，18px，1.35）：顶栏标题、弹窗标题和主要卡片标题。
- **Section Title**（700，14–16px，1.4）：表格区、状态卡和 Agent 面板标题。
- **Body**（400/500，13–14px，1.6）：摘要、说明和消息正文；连续文本控制在 65–75 个字符宽度内。
- **Label**（600/700，11–12px，1.4）：字段名、状态说明、导航与按钮文字。
- **Data**（600/700，12–18px，1.4）：分数、数量、等级、时间和代码；只在数据本身需要比较时使用。

### Named Rules

**数据才用等宽规则。** 不把 IBM Plex Mono 用于普通标题、按钮或正文，也不使用展示型字体制造“AI 感”。

## 4. Elevation

系统以描边和色调分层为主、阴影为辅。静态卡片通常使用 1px #e2e8f0 描边与极轻阴影；粘性顶栏、下拉面板和移动导航使用更明确但仍柔和的阴影；弹窗才使用最高层级阴影。深色模式主要依靠 #0b131e、#0e1726 与 #1e293b 的明度差建立层级。

### Shadow Vocabulary

- **静态表面** (`0 1px 2px rgba(15, 23, 42, 0.05)`): 普通卡片、按钮和表格容器。
- **悬浮面板** (`0 10px 15px -3px rgba(15, 23, 42, 0.12)`): 通知、系统菜单和移动底部导航。
- **模态层** (`0 25px 50px -12px rgba(15, 23, 42, 0.35)`): 弹窗与风险详情。

### Named Rules

**默认平面规则。** 阴影不用于装饰；先用表面色与描边表达结构，只有悬浮、粘性或模态层级才增加阴影。

## 5. Components

### Buttons

- **Shape:** 主要操作使用 12px 圆角；紧凑图标按钮使用 8px；状态胶囊使用全圆角。
- **Primary:** #007aff 白字，典型内边距 10px 20px；文字 13–14px、600–700。
- **Hover / Focus:** 悬停切换为 #0062cc，并允许轻微 0.97–1.02 缩放；键盘焦点使用 2px #007aff 焦点环。
- **Secondary / Ghost:** 白底或透明底、#e2e8f0 描边、#007aff 或 #424751 文字；不得与主按钮争夺视觉优先级。
- **Disabled / Loading:** 降低透明度但保持标签可读；异步动作必须显示进行中状态并阻止重复提交。

### Chips

- **Style:** 4px 8px 内边距、全圆角、11–12px 粗体；信息类使用 #eef6ff/#007aff。
- **State:** P1–P4 芯片严格采用风险语义色；选中筛选器可使用主蓝描边或浅蓝背景。

### Cards / Containers

- **Corner Style:** 标准卡片 12px，关键面板和弹窗 16px。
- **Background:** 浅色使用 #ffffff，辅助区使用 #f1f5f9 或 #eef6ff；深色使用 #0b131e、#0e1726、#1e293b。
- **Shadow Strategy:** 静态卡片仅使用极轻阴影或无阴影，始终保留明确描边。
- **Border:** 1px #c2c6d2；深色模式使用 slate-800 等效描边。
- **Internal Padding:** 紧凑卡片 12px，标准卡片 16px，重点详情 20–24px。

### Inputs / Fields

- **Style:** #f1f5f9 背景、1px #e2e8f0 描边、8–12px 圆角、10px 12px 内边距。
- **Focus:** 2px #007aff 焦点环；不得只移除浏览器 outline 而没有替代状态。
- **Error / Disabled:** 错误使用 #ffdad6 容器与 #93000a 文字；禁用仍需保持至少 4.5:1 的标签对比度。

### Navigation

- 桌面端固定 240px 侧栏，当前项使用 #007aff 实心背景、白字和共享布局高亮；非当前项为 #424751，悬停进入 #dbeafe/60。
- 顶栏固定在内容顶部，承载页面上下文、模型/外部核查/服务状态、搜索和当前风险入口。
- 1024px 以下隐藏侧栏，使用固定底部导航；至少保持 44px 触控目标并预留安全区与页面底部空间。

### Risk Evidence & Agent

- 风险详情按“来源 → 事件 → 供应商匹配 → 规则评分”呈现，不得跳过证据直接给结论。
- Agent 消息使用 72/28 桌面双栏；移动端单列，聊天区独立滚动，状态卡自然排在下方。
- 工具调用通过可折叠证据区展示真实工具名、参数和结果状态；不得伪造耗时、HTTP 状态或外部核查内容。

## 6. Do's and Don'ts

### Do:

- **Do** 直接复用现有 React 组件、Tailwind 色值、12/16px 圆角和 Motion 状态模式。
- **Do** 在新增页面首屏优先展示当前风险、受影响供应商、证据或系统健康信息。
- **Do** 为加载、空数据、失败、禁用、成功和只读状态提供明确文案与视觉反馈。
- **Do** 同时使用 P1–P4 文本、名称和语义色表达风险等级。
- **Do** 在 1440px、1024px、768px、390px 和最低 320px 宽度检查布局，并遵守减少动效设置。
- **Do** 保持浅色与深色模式的结构、层级和交互一致。

### Don't:

- **Don't** 偏离 Google AI Studio 已确认版本，另起配色、字体、图标、圆角或组件体系。
- **Don't** 使用紫色霓虹、装饰性渐变、泛滥玻璃拟态、渐变文字或营销落地页式 Hero。
- **Don't** 使用超过 1px 的彩色侧边条装饰卡片，也不要把所有内容机械地放入同尺寸卡片网格。
- **Don't** 使用虚构指标、模拟在线状态、假下载、假用户身份或不存在的后端能力填充页面。
- **Don't** 设计库存、替代供应商、历史趋势、外部通知、风险处置或知识图谱界面，除非产品范围已被明确重新批准。
- **Don't** 让动画延迟任务完成、循环吸引注意或成为理解内容的必要条件。
- **Don't** 在移动端保留桌面双栏、窄列嵌套滚动或小于 44px 的主要触控目标。
