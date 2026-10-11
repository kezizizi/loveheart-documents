# CLAUDE.md · 爱心社「爱心足迹」查询系统

> **这份文件是本仓库唯一的交接文档（仓库里没有 README，也不要有）。**
> 写给 AI 协作者：读完这一份就该能安全地改这个仓库，不必先读完全部代码。
> 改动代码后请**同步更新本文件**，尤其是第 3 节的红线与第 6 节的口径。

---

## 1. 一句话定位

北京大学学生爱心社的社员活动记录查询系统。社员输入**姓名 + 学号**，得到自己跨两个学年库
的完整足迹：活动时间轴、志愿学时、部组分布，以及可导出的 CSV 与分享海报。

技术栈：**Streamlit**（视图） + **飞书多维表格 Base**（数据源） + **Pillow**（服务端渲染海报）。
部署目标是 Streamlit Community Cloud，因此**没有无头浏览器**，海报只能服务端拼版。

---

## 2. 当前规模（实测值，会随数据增长，用第 8 节的命令重新量）

| 指标 | 值 |
|---|---|
| 自动发现的活动问卷表 | **171 张**（25-26 学年 125 + 26-27 学年 46） |
| 注册表构建耗时 | 约 13 秒（约 175 次 API 请求） |
| 单次社员查询 | 约 5 秒 |
| 测试 | **162 项**，全绿，不触网、不需密钥 |
| 代码量 | 约 4800 行（不含测试与资源） |

活动表数量、部组分布、数据库字段都会随时间变化 —— **任何写死的数字都只当参考**，
以 `scripts/verify_live.py` 的实时输出为准。

---

## 3. 🚩 不可违反的硬约束

按重要性排序。每条都对应某次真实事故或用户的明确指令，**不要凭直觉放宽**。

### 3.1 取数范围 = 只访问「主表里已关联」的活动表

查询某人的参与记录时，**只访问该人主表记录里确实关联了 record_id 的那几张活动表**，
其余一张都不碰。实现是 `feishu/member_query.py::linked_tables`，
它是在 `collect_participations` 里**唯一**的取数范围来源。

- **不许**扩大到「该人有档案的库里的全部活动表」。
- **不许**扩大到「全部库」。
- **不许**为了「多扫一点更全」而新增按姓名扫描的兜底通道 —— 那种通道曾经存在，已被用户要求删除。

**原因**：活动表访问是整条链路里最贵的一步，而**飞书 API 调用量有月度上限**。
对只在 26-27 有档案的社员（本届新生多数如此）擅自加扫 25-26，就是每次查询白打 125 张表。
守卫测试：`tests/test_member_query.py::test_collect_participations_never_widens_scope`
（它不是恒真的：把 `tables_of` 改成返回全库会立刻失败）。

**代价（已知并接受）**：活动记录必须都已建立关联，否则那条记录对该社员不可见。
这不是 bug，是刻意的取舍 —— 覆盖率情况见第 12 节。

### 3.2 不许硬编码活动表 id 或活动名映射

活动表 100% 靠「遍历 Block 树 + 找有没有指向主表的 link 字段」动态识别
（`feishu/registry.py`）。主表侧指向活动表的 link 字段用于建立取数索引。
历史上有一张 80 条的手工映射表，其中两处 id 错位（扫雪与爱心驿站指向同一张表），
**这是重写的直接动因** —— 不要走回头路。骨干新增活动表后无需改任何代码。

### 3.3 部组只能来自文件夹层级

`resolve_group` 只认 `config.GROUP_COLORS` 里的 **9 个已知部组**（护老部 / 无障碍部 /
儿童部 / 手语分社 / 资助部 / 组织部 / 宣传部 / 项目组 / 外联部），支持子串匹配
（如「组织部26春」）。**认不出的一律返回 `None`，归入“其他”** ——
不要拿最外层文件夹名顶替，否则「1+n第二期」这类专项工作流会伪装成部组。

也**不要**用入社问卷里填的「感兴趣的部组」——那是意愿，不是事实。

### 3.4 敏感字段永不进入请求投影或结果

唯一黑名单来源是 `config.SENSITIVE_FIELDS`（身份证 / 志愿者编号 / 微信号等）。
`feishu/client.py` 里有**两道断言**（出站字段名、入站字段字段），命中即抛
`SensitiveFieldError`。注册表阶段也会把这些字段从 `safe_fields` 里剔掉。
改了任何字段相关代码，必须让隐私回归用例保持通过。

### 3.5 主表认人：姓名与学号必须同时命中

`lookup_member` 只认「姓名归一后相等 **且** 学号归一后相等」的记录，只看姓名一律不算。
查不到时按 `NOT_FOUND_*` 归因（`name` / `sid` / `sid_missing`），交给 UI 给对应文案。

**注意区分两件事**（历史上混淆过一次）：
- **主表认人** → 姓名 + 学号必须同时命中，不可放宽。
- ~~参与判定~~ → 已随扫表通道一起删除，现在**不存在**任何「仅姓名匹配」的路径。

### 3.6 学号必须过 `normalize_sid`

25-26 库的「学号」是**数字字段**，26-27 库是**文本字段**。
`services/identity.normalize_sid` 把它们统一成纯数字字符串（`2024001.0` → `"2024001"`）。
绕过它做比较会让跨库匹配永远失败。

### 3.7 所有 `st.html` 调用必须走 `ui/pages.py::_html`

`st.html("")` 会抛 `st.html body cannot be empty` 把整页打挂。组件在「无事可画」时
习惯返回空串（例如 `group_pills([])`），所以 `_html` 是**本模块唯一的 st.html 出口**，
空内容直接跳过。新增 `st.html` 调用一律走它。

### 3.8 UI 里来自飞书的数据必须转义

`ui.components.esc`。`components.note()` **默认转义** `body`，
只有调用方明确传 `html=True` 才按富文本渲染。

### 3.9 `load_registry` 的签名里不许有任何参数

`ui/state.load_registry()` 刻意**不收参数** —— 缓存键里一旦掺进会话级的东西（如 nonce），
每个新访客都会重建一次注册表（约 175 次请求）。所有会话共用同一份（TTL 24 小时）。
守卫测试：`tests/test_state_cache.py`。
**代价（有意接受）**：点「刷新数据」对所有人都生效。

### 3.10 不要「丢掉未知字段继续查」

字段被改名或删列后，接口会返回 `FieldNameNotFound`（1254045）。正确动作是抛
`SchemaChangedError` → 重建注册表 → **只重试一次**（`services/schema_refresh.py`）。
**绝不**把未知字段悄悄丢掉继续查 —— 学时字段一旦被改名，那样做会让整张表学时**静默变成 0**，
比报错危险得多。宁可报错，不给错数字。

---

## 4. 数据流与分层

```
飞书 Block 树（含文件夹）
  └─ 逐表探测：有 link 字段指回主表？ → 是活动问卷表
       └─ 文件夹层级 → 部组；文件夹名/表名 → 学期
       └─ 字段名正则 → 学时字段 / 时间字段
                    ↓  产出 ActivityRegistry（可缓存快照）
主表记录（姓名+学号命中）
  └─ 主表 link 字段 → 活动表 record_id 列表
       └─ batch_get_records → 参与记录 → 学时 / 时间 / 感想
            └─ summarize() → Footprint → UI / CSV / 海报
```

**严格三层，依赖单向**：

| 层 | 目录 | 允许做什么 |
|---|---|---|
| 数据层 | `feishu/` | **唯一**发网络请求的地方；把两种接口形态归一化 |
| 业务层 | `services/` | **纯函数**：无网络、无 Streamlit，便于单测 |
| 视图层 | `ui/` | 只做编排与渲染；`app.py` 是入口 |

**唯一缝（seam）**：`feishu/protocol.BitableClient`。上层只依赖这个 Protocol，
真实实现走飞书 HTTP（`feishu/client.py`），测试与演示实现读本地 fixture
（`feishu/fake.py`，**同一份**，避免两套假实现漂移）。

---

## 5. 文件地图

```
app.py                      Streamlit 入口；页面级异常兜底（避免白屏）
config.py                   纯常量 + 容错配置加载器。字段白名单/敏感黑名单、部组清单与配色、
                            学期归一、活动名清洗、字段探测正则、TTL/并发默认值
CLAUDE.md                   本文件（唯一交接文档）

feishu/  ── 数据层（唯一网络出口）
  protocol.py               BitableClient 协议 —— 测试与真实的唯一缝
  client.py                 HTTP 客户端：token 单例复用、退避重试、v3→v1 回退、字段投影守卫
  schema.py                 把 base/v3 与 bitable/v1 两种 JSON 归一成同一种形状；单元格取值工具
  registry.py               活动注册表：遍历 Block 树 + 探测 link 字段 + 文件夹推导部组 + 字段探测
  member_query.py           单成员跨库查询：认人、归因、只按关联取数、汇总参与记录
  models.py                 不可变 dataclass：ActivityTable / ActivityRegistry / MemberRecord /
                            Participation / Footprint / MembershipDate / TimelineItem
  fake.py                   内存版客户端，测试与演示共用

services/  ── 业务层（纯函数）
  identity.py               学号/姓名归一化、跨库合并（MergedMember）
  membership_date.py        入社日期口径、日期格式化、入社天数
  footprint.py              时间轴排序、学时汇总、部组聚合、与 N 位伙伴同行
  poster.py                 Pillow 服务端渲染海报；poster_height() 是画幅高度的唯一来源
  schema_refresh.py         with_schema_refresh：命中 SchemaChangedError 时重建并只重试一次

ui/  ── 视图层
  theme.py                  注入品牌样式与图片资源（读 assets/css/theme.css）
  components.py             自绘 HTML 组件；esc() 转义、note() 默认转义
  animations.py             心跳 loading、数字滚动
  pages.py                  页面编排；_html() 是 st.html 的唯一出口
  state.py                  缓存装配与会话状态（load_registry / run_query / refresh）

assets/                     栏图、社徽、吉祥物、二维码、开源中文字体、样式表
docs/                       设计源文件、海报示例、飞书表格结构分析报告
  └ 爱心社社员档案_飞书多维表格结构分析.md   ← 想了解 Base 的真实结构看这份
scripts/verify_live.py      真实接口联调自检（用真实密钥，只打印结论不做断言）
tests/                      162 项单元测试 + 录制的 JSON fixture（blocks/fields/records）
.github/workflows/ci.yml    跑测试 + 密钥泄漏扫描
```

---

## 6. 领域口径（改数据相关代码前必读）

| 项目 | 口径 |
|---|---|
| 主键 | 归一化后的「姓名 + 学号」；学号统一成纯数字字符串 |
| 活动表识别 | Block 树里的 table 且**有 link 字段指回主表**；排除主表自身、`FORBIDDEN_TABLE_IDS`、模板/反馈渠道类表名 |
| 活动名 | 剥掉「反馈、学时问卷」「问卷」「副本」「-结果分析」等后缀（`config.ACTIVITY_NAME_SUFFIXES`） |
| 部组 | 表格在 Base 里的**文件夹归属**；只认 9 个已知部组，其余归「其他」 |
| 学期 | 由文件夹名或表名里的「2025秋 / 26春 / 暑假」推导，统一成四位年份 + 季节 |
| 入社日期 | 两库全部记录取最早：优先「入社日期」字段真实值（25-26 库有）→ 标“真实填写”；缺该字段则回退 **Base 记录 `created_time`**（自动字段，≈ 首次填招新问卷）→ 标“约为首次入档时间”。⚠️ 批量导入的行 created_time 是导入时刻，可能晚于实际入社时间 |
| 志愿学时 | 字段名不统一（志愿时长 / 志愿学时 / 工时），按 `HOURS_RE` 正则探测；同条记录多个学时字段则**求和** |
| 参与时间 | 字段名更野（参与时间 / 填写日期 / 签到时间），`TIME_RE` 同时认「时间」与「日期」；`TIME_STRONG_HINTS` 排序 |
| 时间轴排序 | 参与时间 → 学期标签 → 记录创建时间，三级回退 |
| 置信度 | 取数只走关联后**恒为 high**；`medium` / `low` 是历史遗留（UI 文案与 CSV 列还留着，见第 12 节） |
| 海报画幅 | 宽固定 1080；高 `max(1620, 版头+统计卡+条数×行高+页脚)`，**收录全部活动不截断** |
| 缓存 | 注册表 24 小时（**全局共用**）；单次查询结果 10 分钟 |
| 与 N 位伙伴同行 | 按表去重、每表一次请求、上限 `CO_PARTNER_CAP=30` 张；人数按姓名去重，只出人数不出姓名 |

---

## 7. 飞书接口的坑（每条都是一次真实事故）

代码里都已处理。**改数据层之前先看这张表**，否则很容易把已修好的东西改坏。

| 现象 | 根因 | 现在的处理 |
|---|---|---|
| **部分活动表凭空消失**，且每次消失的还不一样 | 旧版接口把**限流**报成 `HTTP 400` + `code 99991400`（**不是 429**）。只按状态码判断会把请求直接判死，被限流的那张表被丢掉，而注册表要缓存 24 小时。修复前连跑三次得到 169 / 165 / 171 张 | `99991400 / 99991404 / 1254290` 纳入可重试判定 + 退避重试 |
| **整张表的数据一条都拿不到** | 请求投影 `field_names` 里带**超过 50 字**的字段名，飞书会拒掉**整个请求**（`field validation failed`，`99992402`），而不是忽略那一个字段。触发源是有人把问卷题干当成了列名（最长 78 字） | API 边界 `client.usable_names()` 统一剔除；注册表侧 `safe_fields` / 学时 / 时间 / 主表投影同步过滤。投影被剔空则**宁可报错**，也不退化成「不带 field_names」（那会拉回全部字段、越过隐私边界） |
| 骨干改个列名，那张表就整体报错 | 缓存 24 小时的旧字段名触发 `FieldNameNotFound`（`1254045`） | 抛 `SchemaChangedError`，穿透单表隔离，重建注册表后**只重试一次** |
| 关联取不到任何数据 | link 单元格的键名是 `link_record_ids`（不是旧文档里的 `record_ids`） | 两种键名都兼容（`schema.cell_link_record_ids`） |
| 按姓名筛选失效、返回整表 | 旧版 `records/search` **不认 `keyword`**（传了也返回整表） | 翻译成 `filter` 条件下推到服务端；再不行退化为全量拉取 + 客户端筛 |
| 新版接口整体不可用 | 新版 v3 需要 4 个独立 scope（`base:block:read` / `base:table:read` / `base:field:read` / `base:record:read`） | 自动回退到旧版 `bitable/v1`。**只有 `base:block:read` 没有旧版对应**（文件夹树只存在于新版），缺它会降级为「扁平表列表」、部组退化为「其他」并在页面提示 |
| 整页报 `st.html body cannot be empty` | 一个部组标签都没有时 `group_pills([])` 返回空串 | 所有 `st.html` 收口到 `ui/pages.py::_html`，空内容跳过 |
| 读不到某个库的数据 | 该库没把应用加为**协作者**（`1254302 RolePermNotAllow`） | 见第 8.4 节的飞书后台配置 |

---

## 8. 环境、命令与调试

### 8.1 跑测试（**固定用系统 Python 3.9**）

```bash
"C:/Users/gzk16/AppData/Local/Programs/Python/Python39/python.exe" -m pytest
```

系统 Python 3.9 已装齐 pytest / streamlit / pillow / requests。
**本机 pip 装包被沙箱拦**（curl 通但 pip 报 no versions）—— 不要折腾新建 venv。

### 8.2 本地看界面

```bash
# 演示模式：内置假数据，不需要密钥
AX_DEMO=1 "C:/Users/gzk16/AppData/Local/Programs/Python/Python39/python.exe" -m streamlit run app.py

# 真实模式（需要 .streamlit/secrets.toml）
"C:/Users/gzk16/AppData/Local/Programs/Python/Python39/python.exe" -m streamlit run app.py
```

带参查询（便于分享与调试）：`http://localhost:8501/?name=张三&sid=2300017473`

### 8.3 真实接口联调自检

改了数据层代码后必跑（**需要真实密钥，只读不写**）：

```bash
python scripts/verify_live.py              # 全量自检
python scripts/verify_live.py --skip-query # 只建注册表，不查具体社员
```

它会打印：接口版本、是否降级、两个库各发现多少张活动表、部组分布、字段探测抽样、
敏感字段泄漏检查、一次真实社员查询结果。打印姓名时做脱敏。
**判断改动是否无损，就看这里的活动表数量与命中数是否与改前一致。**

### 8.4 飞书后台配置（新克隆仓库必做，最容易漏）

1. **开通权限并创建版本发布**（不发布不生效）：`bitable:app` 必需；
   `base:block:read` 强烈建议（**这是部组标签的唯一来源**，缺了部组全变「其他」）。
2. **把应用加为每个库的协作者**。⚠️ 入口**不在「分享」面板**（那里只搜成员和用户组，
   搜不到应用）：

   > 打开多维表格 → 右上角 **「···」→「···更多」→「添加文档应用」** → 搜应用名 →
   > 权限选 **「可管理」**。

   两个库都要做。**这两个库都开了高级权限，必须给「可管理」**，给「可编辑」仍报 `1254302`。
   若「添加文档应用」里也搜不到：① 发布版本的「可用范围」需包含该库所有者；
   ② 应用至少开通任意一个云文档 API 权限。

   **API 加协作者走不通，别再试**：`member_type: "appid"` 被无条件拒绝（`1063001`）；
   用应用 open_id 需要机器人能力，本应用没开（`bot/v3/info` 返回 `11205`）。**只能走 UI。**

### 8.5 密钥与配置

`.streamlit/secrets.toml` **不在仓库里**（`.gitignore` 排除，CI 还有一道扫描）。
新克隆需自建：

```toml
[feishu]
app_id = "cli_你的应用ID"
app_secret = "你的应用密钥"

[base_25_26]
base_token = "第一个多维表格的 base_token"
main_table_id = "「社员档案」主表的 table_id"

[base_26_27]
base_token = "第二个多维表格的 base_token"
main_table_id = "「社员档案」主表的 table_id"
```

`base_token` / `main_table_id` 从 URL 里取：`https://<租户>.feishu.cn/base/<base_token>?table=<table_id>`。
也可用环境变量：`APP_ID` / `APP_SECRET` / `BASE_25_26_TOKEN` / `BASE_25_26_TABLE_ID` /
`BASE_26_27_TOKEN` / `BASE_26_27_TABLE_ID` / `REGISTRY_TTL` / `MEMBER_TTL` / `MAX_WORKERS`。

### 8.6 Windows 上的坑

- **没有 watchdog**：Streamlit 不监听被导入模块的改动。**改完代码必须手动重启服务**。
- **旧进程占端口**：旧进程还占着端口时，新起的会静默失败，你看到的仍是旧页面。
  用 `netstat -ano | grep :8501` 找 PID 再 `taskkill //F //PID <pid>`。
- 界面验收：本仓库是 SPA + JS 框架，普通 `--screenshot` 只能拍到骨架屏；
  需无头 Edge + `--remote-allow-origins=*` + CDP 截图。

---

## 9. 改代码时的契约

- **要发网络请求 → 只加在 `feishu/`**。业务层与视图层不许直接 import `requests`。
- **新数据源 → 实现 `feishu/protocol.BitableClient`**，不要改上层调用。
- **所有新字段名先进 `config.MEMBER_READ_WHITELIST`**，否则不会被请求。
- **渲染 HTML → 用 `ui/components.py` 的 `esc` / `note`；`st.html` 一律走 `ui/pages.py::_html`**。
- **海报高度 → 只改 `services/poster.poster_height`**。改版式（增删区块、改行高）必须同步改它，
  否则会截断或留白 —— 高度能在落笔前算准，正是因为版式是自上而下的定高流水线。
- **单表失败要隔离**，但 `SchemaChangedError` 必须**穿透**（`raise`），否则自动重建触发不了。
- **新错误码 → 先判断它属于哪一类**：可重试（限流）/ 走 v1 回退（新版不可用）/ 语义化（结构变更）。
  分错类会造成静默丢数据。

---

## 10. 常见任务 → 改哪里

| 想做的事 | 动哪里 |
|---|---|
| 骨干新增了活动表，让它出现 | 不用改代码；点页面「刷新数据」（注册表 TTL 24h，且**全局生效**） |
| 调整学时/时间字段的识别规则 | `config.HOURS_RE` / `TIME_RE` / `TIME_STRONG_HINTS` + `registry.detect_*` |
| 改部组清单或配色 | `config.GROUP_COLORS` / `GROUP_FALLBACK_COLOR` |
| 改查不到时的文案或补录链接 | `ui/components._NOT_FOUND_LEAD` / `not_found_note`；链接在 `config.SUPPLEMENT_FORM_URL` |
| 改活动名清洗规则 | `config.clean_activity_name` + `ACTIVITY_NAME_SUFFIXES` |
| 改海报版式 | `services/poster.py`（同时改 `poster_height`） |
| 改缓存时长 | `config.DEFAULT_REGISTRY_TTL` / `DEFAULT_MEMBER_TTL`（或 secrets） |
| 新增可查询字段 | `config.MEMBER_READ_WHITELIST`（记得同步隐私清单与测试） |
| 改前端样式 | `assets/css/theme.css` + `ui/theme.py` |
| 改查询参数入口 | `ui/pages._apply_query_params` |

---

## 11. 隐私

- 只查询用户本人输入的那一条记录，**不展示任何其他社员的信息**。
- 身份证号、志愿者编号、微信号等敏感字段，在代码层就被排除在请求投影之外 ——
  既不读取，也不进入内存、页面或导出文件。唯一黑名单来源是 `config.SENSITIVE_FIELDS`，
  `feishu/client.py` 有两道断言，并有单元测试守着。
- 「与 N 位伙伴同行」只输出**人数**，不含任何姓名。
- 海报上**只出现姓名**，不含院系、年级、学号。
- 社员档案导出件（`docs/*.xlsx`）含姓名/学号/院系，**已被 `.gitignore` 挡住，绝不入库**。
- `docs/平面设计常用素材/`（约 41 MB）仅供本地参考，不入库。
- 中文字体 `assets/fonts/NotoSansSC-VF.ttf` 随仓库提交（SIL OFL 开源）；
  **不要换成微软雅黑/黑体**，那些是专有字体，放进公开仓库有授权风险。

### ⚠️ 提交纪律（曾出过事故）

- **不要盲目 `git add -A`。** 先 `git status --short` 与 `git diff --cached --stat` 看清暂存了什么
  —— 曾有 AI 用 `-A` 把 `.streamlit/secrets.toml` 连同 `.gitignore` 的改动一起提交了。
- **`.gitignore` 里的 `.streamlit/secrets.toml` 那一行不许删。**
  有人会因为「Streamlit Cloud 读不到密钥」而想删掉它把文件传上去 —— 那是错的，见下条。
- **密钥永远不入库。** 云端靠 **Streamlit Cloud 的 Settings → Secrets**（服务端加密保存）
  注入，本地靠被忽略的 `secrets.toml`；`config.load_settings` 先读 `st.secrets` 再读环境变量。
  两个来源都不需要文件进仓库。若密钥真的被推到 GitHub，**必须立即在飞书开放平台重置
  app_secret**，因为该应用能读到社员档案（姓名/学号/院系）。
- CI 有 `secret-scan` 任务兜底：一旦 `secrets.toml` 被跟踪、或代码里出现 `cli_` 开头的硬编码
  凭证，流水线直接失败。

---

## 12. 已知限制与待办

**数据侧（需人工在飞书里做，不是代码问题）**

- 活动记录必须都已建立双向关联，否则那条记录对该社员不可见（见 3.1）。
  当前覆盖率约：25-26 学年 92%、26-27 学年 96%。补完关联即 100%。
- 25-26 主表里有一批历史记录**没有填学号**。这批人拿真学号来查会落到
  `sid_missing` 归因 → 引导补录。**系统刻意不做姓名兜底匹配**（那会拿同名误判换覆盖率）。

**代码侧（已知、待决定）**

- `confidence` 恒为 `high`：UI 的「待确认」提示、`low_confidence_count`、
  CSV 的「置信度」列都不会再有第二种取值。**没删**，因为「置信度」是给人看的输出，
  要不要连 CSV 列一起撤掉属于产品决定，需用户拍板。代码里已加注说明。
- `ActivityRegistry.errors` 里的失败只会提示「数据可能不完整」，不会自动重试到底 ——
  限流重试耗尽后仍有极小概率丢表，但**不会无声无息**。
- 活动名清洗还能更好（如「9月20日无障碍部迎新会反馈」尾部「反馈」未剥净，
  与「2025星空」类命名风格不一致）。
- `search_records` 的 `limit` 在旧版回退路径被忽略（会全量返回），不影响正确性，只影响速度。

**未完成**

- 尚未推送 GitHub、尚未部署（本机沙箱连不上 github.com）。
  填密钥、加协作者均已完成，直接 `git remote add origin <地址> && git push -u origin main` 即可。

---

## 附：许可

代码供北京大学学生爱心社内部使用。
字体 `assets/fonts/NotoSansSC-VF.ttf` 采用 SIL Open Font License 1.1。
