# AGENTS.md — AIROOT

面向在本仓库工作的 AI Agent / 协作者。第一次进入请按顺序读完本文件，再读 `docs/AIROOT-总体方案规划-v0.3.md` 的 §1.1（规范层级）。

## 1. 这是什么项目

**AIROOT 是面向 AI Agent 的本机能力控制平面（Local Capability Control Plane）。**

它要解决的唯一核心问题是：

> 如何让 AI Agent 在长期使用的 Windows 电脑上，以确定、可审计、可诊断、可恢复的方式获得本机能力。

它**不是**：通用包管理器、Everything 安装器、外部软件维护平台、容器/沙箱、企业分发系统，也不是能对抗管理员权限的安全产品。

形态固定为：

```text
AIROOT
  = Skill 适配层（AIROOT\SKILL.md）
  + 稳定 CLI/API（AIROOT\cli）
  + Windows 权限边界（ACL + elevated broker）
  + 状态注册表（SQLite registry/event）
  + 事务/恢复引擎（journal + generation）
  + 能力扩展运行时（Capability Extension）
```

### 当前阶段：P1「最小 Core」已实现，P2 未开始

**已实现（协议级，Python 3.11）**：root 解析与卷身份守卫、SQLite registry（WAL + generation CAS + 迁移至 v5）、只读 JSON 投影、`where` 确定性选择、`doctor` D1–D10、假 Capability Extension、模拟事务与 journal 驱动恢复、CLI 与 golden 语料。**外加管家域的第一段**（ADR-0004 步骤 1–4）：数据根注册（可跨卷）、只读 PE 静态探测、能力白名单发现、`adopt --mode reference` 非拥有式登记；一个对象**可带版本集合与观测到的活跃版本**（`D:\env\nvm4w` → 3 个版本 + active 26.8.1.0，来自 `nodejs` junction 的只读 `readlink`）。**步骤 8–9**：依赖分流决策层（`scope decide`/`scope memory`）、会话级环境激活（`env activate`、`exec`）、**user 级环境变量持久化**（`env persist` 需 approval token、`env list`、`env forget` 精确还原，值只允许指向数据根内真实路径）。**步骤 5–6**：`where` 改为 **steward-first**（reference 是一等候选，优先级由 `policy/selection-policy.json` 决定；owned 坏 + reference 健康 = 正常降级 `CURRENT_SOURCE_DEGRADED`），`doctor` 扩展到**数据根与 reference 的重观测诊断**（D1/D3 扩展、`--include-unmanaged`）。**步骤 8b**：`plan` 先路由再计划（`--scope/--target/--dry-run`，路由决定进 `metadata.routing`；需要确认或 scope 提升时**不落盘计划**并以退出码 4 停下）。**步骤 8c**：**Install Backend 抽象**（`caps/backends/`：九个冻结声明 + 九步协议；`portable_file` 本地无脚本 artifact、`https_artifact` 强制 SHA256 且拒绝明文/降级重定向；无脚本守卫；`install`/`repair` 按 plan 的 backend 分发）。**步骤 9h**：**desired vs declared 接进诊断**（新码 `DESIRED_NOT_SATISFIED`(2) 挂在 D5；manifest 不可读时 `doctor` 给结论而不抛异常；**未 pin 的机器诊断输出逐字节不变**；`tool list` 显示 `desired_version`/`in_sync`，无 pin 时 `in_sync` 是 `None` 而非 `False`）。**步骤 9g**：**`desired` 层与 `tool pin`**（五层事实模型里最后空缺的一层：`state/desired.json` 按 §17 的形状；`pin` **只写 desired、给出计划、绝不改 binding**；`evaluate()` 比较 desired 与 declared；没有可信来源时如实报 `plan_blocked_by` 而不是伪造 locator；manifest 含 shell 片段直接拒）。**步骤 9f**：**只读观察面**（`tool list`/`tool status`/`tool verify`——只读、不切换 active、不 adopt/修复；`path verify` 检查"机器 PATH 只允许一个 AIROOT 条目、版本目录绝不直接进 PATH"这条此前只有文字的不变量；新码 `PATH_EXPOSURE_VIOLATION`(2)）。**步骤 9e**：**session 激活的完整语义**（`env activate --session <id>` 落盘快照栈；`env deactivate [--all]` 输出**恢复脚本**——恢复旧值而不是删变量；`env activate --json` 补齐 §16.4 要求的 diff/generation/快照/deactivate 四项；新码 `SESSION_STATE_STALE`(2)；`unadopt` 作为 `forget` 的兼容别名；Skill 漂移守卫升级到**命令路径**粒度）。**步骤 9d**：**`rebuild`**（从权威重建 `state/registry.json` 与 `logs/audit/events.json`；旧投影归档到 `state/rebuild/<seq>/`；**数据库永不被重建**，不一致时拒绝而不是洗白；陌生对象只报告，`adopted=0`/`files_deleted=0`）。**步骤 9c**：**来源清单**（`policy/sources.json` 可信 host + `caps/sources.py` 解析上游校验和；`source list` / `source resolve`；`plan --source-json` 构造真实 artifact 计划。**expected digest 只能来自上游校验和文件**，清单里不允许写 digest；v1 不做签名，三处明说"摘要不等于签名"）。**步骤 9b**：**Skill 适配层**（`SKILL.md` + `agents/airoot.json` + `references/`，13 项漂移守卫：命令存在性、三选一 == `CONFIRMATION_OPTIONS`、退出码表 == `REASON_EXIT`、未实现命令白名单为真）。**步骤 11–12**：**删除分级**（`tool retire` / `tool gc --plan|--apply` / `uninstall` = retire + gc；reference 永不 `uninstall`，只报 `OWNERSHIP_REQUIRED` 与绝对路径；`gc` 是 AIROOT 唯一删文件的路径，必须消费 approval token 且 apply 时重算准入判据）与**能力边界**（`policy/capabilities.json` 冻结 8 个能力 + `caps/boundary.py` 三条件准入 + `capability list/check`；白名单加载期就拒绝"引用未冻结能力"）。**契约草案 §31（规划 §19 的 P3 协议面）**：`search` 的请求/响应按已发布的 `search-request`/`search-response` Schema 实现——请求构造与**实现上限**（超限 `EXTENSION_INPUT_INVALID`(8)，不静默截断）、root 规则（空 roots 用注册的数据根，**绝不扫全卷**）、cursor 绑定（改查询即 `SEARCH_CURSOR_INVALID`(8)）、**有界 crawl**、`physical_verify`、结果打标（`management`/`capability_id`）。**这个 build 没有索引**，所以每次回答在四处同时自报 fallback（`status=degraded`、`SEARCH_FALLBACK_USED`(2)、`data.fallback.kind=crawl`、`freshness=unknown/none`）；`search status|explain|implementations` 可用。**契约草案 §32**：`cache\search\index.db` 是真的——`search refresh`（`rebuild` 是同一件事的协议拼写）用一次有界 crawl 建索引，**整文件原子替换、不用 WAL、不碰数据根里任何文件**（ADR-0019）；查询**索引优先**，覆盖不足或索引损坏时如实回落 crawl（`SEARCH_INDEX_DEGRADED`(2) + 警告）。于是 `freshness` 第一次有真实值（`current`/`stale` + `last_indexed_at` + `lag_ms` + `coverage`），**健康时退出码是 0**。**它仍然不是 USN/NTFS 索引**：`current` 的含义是"这份清单是最近一次遍历建立的"，不是"journal 没断档"。**契约草案 §33**：索引状态接进了诊断面——`doctor` 的 **D7** 新增两个码（`SEARCH_INDEX_DEGRADED` 读不出来 / `SEARCH_RESULT_STALE` 比 `index.max_age_ms` 更旧，remediation 都是 `rebuild` ⇒ `airoot search refresh`），**没有索引时一条都不报**，且 `doctor` 绝不自己去建索引；L0 审计也新增两项守卫（协议 §6.6 的码必须全部注册、协议保留的子命令集合必须与代码一致）。**契约草案 §34**：把"为什么没有快索引"变成**工具能回答的问题**——新增**只读**USN 能力探测（`caps/usn.py`，默认惰性、失败即数据），`search status --probe-native-index` 显式请求才开卷句柄，`implementations`/`explain` 常驻报出 native 候选与 `SEARCH_BACKEND_UNAVAILABLE`(9)；实测事实与"USN 索引器本体仍属 P2"见 **ADR-0020**。形状决策见 ADR-0017/ADR-0018/ADR-0019/ADR-0020。**契约草案 §35**：把对外承诺变成守卫第四组——`AGENTS.md` 里告诉 agent 去跑的每条命令都必须存在、`INVARIANTS` 里的每个诊断码都必须**有办法产生**或进写明理由的例外清单、两份"未实现"清单必须相等、两份文档都必须保留 Everything 级禁令。**契约草案 §36**：搜索结果的**稳定性**（协议 §10 要求"大小写、Unicode、长路径、保留名结果稳定"）——crawl 的 syscall 一律走 **`\\?\` 扩展长度形式**（深叶不再依赖机器的长路径策略），而**报告的路径一律普通形式**（`physical_verify` 与可访问性判断同样处理）；`test_l1_search_stability.py` 12 项把实测事实（339 字符深叶、大小写对称、不做变音折叠、保留名是普通条目、NTFS 大小写不敏感）钉成常驻测试。**契约草案 §37**：agent 面的 reason code 速查（`references/reason-codes.md`）补齐到**全部 93 个码**（此前 41 个没出现，包括几乎整个 `SEARCH_*` 家族），并新增《搜索的降级阶梯》一节；守卫第五组要求"每个注册的码都必须在这份速查里被点名"。**契约草案 §38**：把 §36.5 记下的那处缺口关掉——`\\?\` 扩展长度路径抽成 **`paths.py` 里的唯一原语**（`search` 重新导出，既有调用不动），`discovery` 的遍历与 `probe_pe` 的 `stat`/`open` 都改用它，并保证**对外路径仍是原生形式**；守卫第六组要求"接触用户数据的模块必须用共享助手、不得自带前缀常量"。**契约草案 §39**：把文档**结构**也变成常驻守卫——一次性扫描发现了两处此前从未被任何测试覆盖的缺陷：本草案的 `### 36.6` 落在 `## 38` 之下，搜索协议 `## 五、` 名下的小节却编号为 `3.1`–`3.4`（而 `AGENTS.md` 与 ADR-0017/0019 都在引用那两个旧号）。修法是挪编号 + 同步 4 处引用，并把扫描落成**守卫第七组**：带编号的小节必须归其所属 `##`、`N.M` 递增且不重复、`N.M.K` 必须有 `N.M` 父节、带文档名的协议引用必须解析到真实标题；审计自身还必须理解两种 H2 约定（`## 12.` 与 `## 五、`）与三级编号（`### 8.1.1` **不是** `### 8.1` 的兄弟）。**契约草案 §40**：把"文档里的**选项**也必须真实存在"变成**守卫第八组**——第四组只看动词，于是 `--max-staleness-ms` 哪天改名了 `AGENTS.md` 会安静地骗人。守卫把每个 `airoot ...` 调用里的 `--选项` 解析到真实 argparse 节点，覆盖面是操作面（`AGENTS.md`、`SKILL.md`、`references/*.md`、`agents/airoot.json` 的 `command` 数组）。三条切片规则（一行多个调用 / 表格单元格是单位 / ` -- ` 之后属于子进程）各自都是被**假阳性**教出来的，已连同理由写成自测；`exec --env` 这个冻结表别名抽成 `cli.EXEC_ALIAS_FLAG`，守卫与改写逻辑共读一份事实。**契约草案 §41**：把**禁止项**的两份声明也绑在一起（守卫第九组）——守卫第五组只绑了"未实现"那一半，`agents/airoot.json` 的 `never` 此前只被断言"非空"，而它已经漂移：`SKILL.md` 清单里的"**不编造确定性**"与"**不得声称已实现 / Everything 级**"两条在机器可读面**没有对应**（后者是本项目的头号禁令），而两份清单**数量同为 8** 纯属巧合。修法是把 `never` 改成**带稳定 `id` + `statement` + 锚点**（`skill_item` 镜像清单第 N 条，或 `stated_in` 指向 SKILL 的其他章节）的结构化条目，补上缺的两条，并让守卫**双向**检查：清单多一条没有条目 → 红，条目指向清单之外 → 红；检查逻辑抽成纯函数，六种失败模式用合成输入逐条演练。**契约草案 §42**：把 agent 的**输出面**也钉住（守卫第十组）——`agents/airoot.json` 每个调用条目都写着 `"read": [...]`（"问这个问题时读这些字段"），而它此前的唯一检查是**非空**；字段被改名，agent 就去找一个不存在的东西，而**缺字段与 `false` 长得一样**。新增 `cli/tests/test_l1_agent_read_fields.py`：建一个真实 root（数据根 + 已 adopt 的 reference + 已安装的 owned 实例 + 注入式 seed 的持久化记录），把 **31 个调用**逐个跑出文档，再结构化解析 **117 条 `read` 路径**——全部存在，`UNCOVERED` 为空；跑不出来的调用必须写明理由，覆盖率断言是**精确等式**而非阈值。**契约草案 §43**：把**随包策略数据**的词汇也钉住（守卫第十一组）——白名单与来源清单是**数据**，而代码对不认识的东西一律**静默不匹配**：`discovery.py` 的 `_matches` 对未知谓词类型 `return False`，于是把 `executable_name` 打成 `exectuable_name` 会让该能力的发现**永久关闭**，机器只表现为"没有这个能力"，且这个 typo 在本次改动前**不会让任何测试变红**。修法是把词汇声明成数据（`PREDICATE_TYPES`、由 `PeMetadata` **派生**的 `PE_PREDICATE_FIELDS`、`PE_PREDICATE_OPERATORS`、`VERSION_SOURCE_PREFIX`、`SUBSTITUTION_KEYS`），在**加载期**拒绝不认识的构造（与既有的"拒绝无静态证据条目/未知键/未知校验和格式"同一做法），并对**危险方向**（声明了却没实现）单独用真实解释器目录跑一次必须命中的匹配。**契约草案 §44**：把**散文里的退出码**也钉住（守卫第十二组）——退出码有两张已被机器核对的表，但**散文没有**：`AGENTS.md`/`SKILL.md`/`references/` 里有 **19 条**行内声明（`` `SEARCH_FALLBACK_USED`(2) ``、`` `OWNERSHIP_REQUIRED`(7) ``、`` `SELF_VALIDATION_FAILED`，退出码 8 ``），那正是 agent 回报给用户的数字。19 条今天全部一致，但改一个退出码会让它们全部过时而无人察觉。守卫只认**显式标记**（括号里的单数字、或"退出码"后接数字），因为 `D7` 覆盖三个码、`§17.6` 是章节号、旁边一个数字**不构成**声明——放宽成"代码附近有数字就算"就是把守卫变成噪音源。**契约草案 §45**：把**状态机合法移动表**自洽化并送进验收语料（守卫第十三组）——`tx/states.py` 的 `TRANSITIONS` 是冻结契约 §4.1 的手工转录，但此前只检查了"`EXPIRED` 是死胡同"与"schema 能命名每个状态"，**表自不自洽无人检查**；更关键的是**边集不在验收语料里**：契约文档只给 happy path 与异常状态**清单**、不给**边**，于是 Rust 版可以把每个响应复现得一模一样却对"哪些移动合法"给出不同答案而**没有 fixture 会发现**。新增 `transaction_transitions.json`（11 步路径 / 16 状态 / **29 条边**——ADR-0021 把 §4.1 的"任何阶段"读法落地后为 **62 条** / 2 终态 / 7 个 payload 状态）+ **8 项**结构断言（键集合相等、目标已文档化、happy path 每步合法、**终态 ⟺ 死胡同双向**、全部可达、`PAYLOAD_STATES` 由 happy path 推导、提交点在路径上、代码与 fixture 逐项相等）。重新生成语料是**外科式**的：21 个既有 fixture 逐字节不变。另**如实记下一处措辞歧义**：§4.1 的"任何阶段都可能进入"比这张表宽，两种读法都未被文档裁决——修法是把**表的**边集钉进语料让差异在移植时暴露，**不单方面改写 layer-2 冻结文本**。**契约草案 §46**：把 §45 的教训推广到**别的契约目录**——D1–D10 分组同时存在于**权威诊断码表**与 `doctor.py` 的 `INVARIANTS`，而既有的四组守卫只查"恰好 D1–D10""每个码都已注册""每个码都有产生路径"，**没有任何一条把两处声明互相比对**：一个码从 D5 挪到 D7，那张读者用来判断"D3 漂移还是 D7 陈旧"的表会继续按旧分组说话，且**语料里也没有这张目录**。修法是**两处都补**（这次文档给了分组，所以既能比对也能进语料，与 §45 只能补语料的情形相对照）：守卫第十四组 3 项（双向比对、没有码有两个归属、解析器只读"码"那一列——D4/D7 的**描述**里也引用了反引号码，按整行抓会把散文当目录）+ 新 fixture `invariant_catalogue.json`（D1–D10 / 30 个码）+ 语料全目录断言。今天两处完全一致（含书写顺序，但顺序**不作判据**），本轮**没有改动任何分组**。**契约草案 §47**：处理**同一个词承载多套词汇**这一类隐患——`scope` 有三套含义（binding `system|machine|session|project` / persistence `user|machine` / routing `project|data-root`），CLI 已经暴露**五套 `--scope` 取值**（`where`/`inventory` 全集、`tool pin` **缺 `system`**、`plan` **把 binding 与 routing 混用**、`env persist` 用 persistence），全部是手写字面量且**没有一条守卫**把它们钉到各自的来源上；重叠处正是危险所在（`machine` ∈ binding∩persistence、`project` ∈ binding∩routing）。另外 `capabilities.json` 的 `scope` **没有加载期校验**（`kind`/`side_effects` 都有），而读取方式是 `tuple(str(v) for v in ...)`——写成裸字符串会被**按字符**读成 `('m','a','c',...)`。修法：加载期校验"名字列表 + 词汇成员"（词汇从 `inventory.SCOPES` **import**，不复制）、守卫第十五组 4 项（两处 schema 绑定 + 五套取值逐套钉死含两个子集 + 重叠集合显式断言）、第三个契约目录 `frozen_capabilities.json` 进语料。**如实记下一处开放问题**：冻结清单声明的 `scope` **今天只是声明**（`check_admission` 与路由都不读它），"未声明 `machine` 的能力能否 machine 级绑定"**没有答案**——本轮**不擅自实现强制**，因为它会改变准入与路由语义。**契约草案 §48**：**状态文档不得否认已交付的东西**（守卫第十六组）——审查报告的追加状态节自称描述"当前状态"，却写着"根级 `SKILL.md` 仍未创建，因此本仓库不是可安装 Skill"（`SKILL.md` 早在步骤 9b 就交付了），测试计数还停在 **180**，且**完全没有**管家域与 `search` 面的交付。§40–§47 的守卫都在查"两份声明是否一致"，**没有一条读散文里的事实性否定**。修法：重写状态节（补交付、改计数、把 `SKILL.md` 改成已交付、逐条列出当前仍缺项）、前文两处 "18 个 Schema" 标注为"审查时的数字"、守卫第十六组（**只覆盖那节自称当前的文字**——前文快照里"当时还没有 `SKILL.md`"在**当时是真的**，要求历史快照跟上今天是改写历史）、并把该文档纳入**测试计数**（精确相等）与 **Schema 计数**两项既有守卫——它此前不在任何计数守卫的文档清单里，F2 才活了二十多轮。另如实记下一处**同类但未决**的风险：验证方案的 79 个 P-/S-/T-/C- 编号只有约 30 个在测试里被点名，但"没有被点名"**不等于"没被测"**，本轮不据此下结论。**契约草案 §49**：把 §48.5 记下的那处**未决风险**变成台账（守卫第十七组）——验证方案与管家草案**两份文档各有一张场景表**，合起来定义 **107 个场景编号**（P-021 / S-036 / T-017 / C-033），而**没有任何东西枚举过这个并集**：Rust 版要逐字节复现的验收面只存在于读者脑子里，也没有任何测试知道"107"这个数。用只读脚本实测后得到四个事实：**39 个**被测试点名、**68 个**没有任何测试点名、**0 个**引用了不存在的编号；`S-014` **在两份文档里各定义一次**；`S-010` 与 `S-015` 是**同一个场景登记了两次**（所以 107 个编号实际只有 106 个场景）。做法不是去补测试，而是把"编号 ↔ 证据"从**记忆**变成**可核对的台账**：`cli/tests/scenario_ledger.py`（纯函数解析器 + 逐条处置表）与第四个契约目录 `scenario_ledger.json`（107 条）进语料；守卫第十七组 8 项把**推导出的事实**（编号集合、`status`、`cited_by`）按**精确相等**双向钉住，而把**作者的判断**（`blocked_by` / `evidence`）只做**结构检查**（词汇合法、证据指针的文件与 token 都真实存在）——`status` 只有 `evidenced`/`uncited` 两个值，**故意没有** `covered`，因为覆盖不是守卫能判定的事，把 68 个说成"没被测"和说成"被测了"同样没有根据。过程中踩到两个**自指陷阱**并都做成了守卫：台账模块自己住在 `cli/tests/` 且点名全部 107 个编号，于是第一版把它自己算成证据、报出 **107/107 全有证据**；修法是只扫 `test_*.py`；随后审计文件本身又因为"语法示例里写了编号"被算成 P-001 的证据，修法是**在运行时拼出编号**，并新增一条守卫**禁止本文件看起来引用了任何场景**（代码与散文都算）。另外实测还找出两处**文档缺陷**：`P-021` 的"另一个卷 → 拒绝"是**假的**（跨卷数据根明确允许，`cli.py` 注释写着卷身份 *explicitly not* required to match，`test_cli_steward.py` 直接断言跨卷可接受）——这与 §48 是同一类错误落在另一份文档上，按同样做法**标注取代而不删行**；以及两份文档说 **27 个 golden fixture** 而实际只有 25 个、审查报告说 **48 项审计**而实际是 49 → 顺带把**语料数、场景数、审计检查数**三个从未被检查过的计数并入守卫（审计检查数由该文件自身源码推导，不写死常量）。**契约草案 §50**：把 §46.4 记下的**欠账**按**类**还上（守卫第十八组）——`CONFIRMATION_OPTIONS` 与搜索上限被写进"已知的同类候选项，列在这里以免被当成遗漏"，但**记下来不等于做了**。这一节把类定义成一句话：**改了它，某个拒绝就会改**，然后把这个类里的数全搬进语料（第五个契约目录 `execution_bounds.json`）。理由是一个具体的问题：只读语料 + 已发布 Schema 的 Rust 版**无从知道** shipped 的 `limit` 上限是 2000、索引在 `cache/search/index.db`、三选一是哪三个词、`MAX_ROOTS` 是 64、`https_artifact` 封顶 2 GiB——于是两份实现可以把语料里每个响应都复现得一模一样，却在"请求 2001 是被拒绝还是被接受"上给出不同答案，而**没有 fixture 会发现**（与 §45 的 F2 同构）。实测还发现每个搜索上限其实是**三个数**：**策略值**（`search-policy.json`）、**代码回退上限**（策略缺项时用，`load_search_policy` 明确允许策略文件被删后继续工作）、**schema 最小值**——三者行为上无法区分，而**只有第一个**在数据文件里；语料把三个都记下来，并逐块写明来源（`policy` / `code_constant` / `code_fallback`）。守卫第十八组 5 项的**承重项是"执行"而不是"相等"**：拿**语料里的数字**去构造请求，界内必须被接受、越界必须被拒绝（`EXTENSION_INPUT_INVALID`；cursor 超长是 `SEARCH_QUERY_INVALID`，故意的不同码），因为相等只能防语料过期，防不住"语料写了一个代码根本不执行的界"——这就是 §49 抓到的恒真式的推广。不能低成本触发的界（`crawl.max_records` 25 万条、索引记录上限、2 GiB/8 GiB）进 `RECORDED_ONLY` 并**逐条写明理由**，且守卫要求 `EXERCISED` 与 `RECORDED_ONLY` **划分**全部块（"没检查"和"看起来检查了"必须长得不一样）。**契约草案 §51**：把 agent 面的**按需参考**也钉到代码上（守卫第十九组）——`references/confirmation.md` 是 Skill 告诉 agent"问用户之前先查"的那份文件，而它里面有两样东西**完全没有守卫**。实测：把 `SCOPE_UPGRADE_REQUIRES_APPROVAL`（退出码 4）改成 5、把三选一的 `data-root` 改成 `data_root`，**当时全部测试都是绿的**。找到的缺口有三个：**（F1）** 守卫第十二组只认 `` `CODE`(2) `` 与 `` `CODE`，退出码 N `` 两种写法，而文档里还有**第三种**——`` `CODE`（退出码 N） ``，两种都匹配不上（`（退出码 N）` 既不是裸括号数字，也没有逗号）；**（F2）** 那份三选一代码块没有任何守卫（`SKILL.md` 的确认协议段被绑定，同一套词在 reference 里没人管）；**（F3）** "`.ai/tooling.json` 当前版本**只读**"是关于实现的声明，没有守卫（实测确实只有读入口，没有写入口——所以这句话今天是**真的**）。修法：写法表增加第三种并**要求码与括号紧邻**、未绑定的 `退出码 N` 按**普查**钉住（17 条里 8 条被绑定、**9 条**进普查：有些数字讲的码在另一句里，有些描述的是退出码本身，**无法自动归属**）、三选一按**列表精确相等**双向绑定、`只读` 用**源码普查**钉住（危险方向是"做了却没说"）。**本轮最贵的教训**：我第一版探测按"同一行里有码又有数字"配对，得到 **135 条"错误"**，逐条读回去**全是假阳性**——这条实测直接决定了修法的形状，并在自测里留了一条"隔几个词就不算"的反例。**这与 §44 当年凭直觉拒绝的放宽是同一件事，本轮用实测重新确认**（第四次由假阳性教出切片规则）。**742 项测试通过**（含 65 项常驻跨工件一致性检查 `cli/tests/test_l0_consistency.py`）。

**尚未实现**：ACL、UAC、elevated broker、named-pipe、machine PATH 写入、**machine 级环境变量持久化**（`--scope machine` 现在报 `PRIVILEGE_REQUIRED`）、`exposure\bin` launcher、**签名/来源证明**（TUF / Sigstore 级；来源清单与摘要校验已就绪，v1 只做 digest）、**真实工具的下载与验证**（机制已通，尚未对真实上游执行过）、**`file_search` 的 Native Index**（USN journal / NTFS 元数据 / 常驻索引器；协议面 §31 与 crawl 建的持久索引 §32 已可用）、Everything adapter、内容搜索、跨用户 ACL 过滤、`reconcile`、`path backup\|restore`、`root adopt\|relocate`（已按命令路径登记在 `agents/airoot.json`）、真实 Ed25519 签名、`.ai/tooling.json` 的写入。管家域 §3–§15 的**全部步骤 1–9、11–12 均已落地**；只剩依赖 P2 的 machine 级环境变量（步骤 10）与 ACL 基线（`DATA_ROOT_ACL_DRIFT` 的发射）。

**禁止把本项目描述成“已实现 AIROOT”或“已具备 Everything 级性能”。** 允许的说法见 §8。

**正在提案中的方向变更**：ADR-0004 把定位从“仓库主人”改为**受聘管家**（reference-first：默认不拥有用户已有环境，删掉本体不得带走环境）。契约草案第二版见 `docs/AIROOT-v0.3-管家模型与数据根契约草案.md`（§12–§15 为新增：依赖分流与确认、环境变量持久化、删除语义分级、能力边界）。**管家域的步骤 1–9、11–12 全部落地**：steward-first `where`（ADR-0006）、数据根/reference 诊断、删除分级与能力边界（ADR-0007）。提案里**只剩**依赖 P2 的 machine 级环境变量（步骤 10）与 ACL 基线未落地。

提案里三条硬规则**现已全部落地并成为实现行为**：

1. **环境变量持久化的值禁止指向 `AIROOT\cli\exposure\bin` 的 shim**，必须指向数据根内真实路径（§13.2）；owned 对象**禁止持久化**。指向 shim 会在删掉本体后留下指向空目录的垃圾。
2. **reference 永不提供 `uninstall`**，只有 `forget`；数据根内任何目录永不被删除（§14.2）。
3. **依赖分流判据复活**：被项目清单引用 → 项目内隔离不询问；单文件通用 CLI → 数据根不询问；**装包/建环境/超阈值才是必须确认的三类**（§12.1）。确认退化成噪音会让高风险确认一起失效。

## 2. 仓库地图

| 路径 | 内容 |
|---|---|
| `docs/AIROOT-总体方案规划-v0.3.md` | 主规划（2236 行）：对象模型、目录、CLI、Skill、路线图、验收 |
| `docs/AIROOT-v0.3-三大核心契约方案.md` | 权限与批准、五层状态、事务状态机与崩溃恢复 |
| `docs/AIROOT-搜索能力与工具集成协议方案.md` | `file_search` / Extension profile、Native Index、降级 |
| `docs/AIROOT-v0.3-验证与测试方案.md` | L0–L3 测试分层、必测场景（P-/S-/T-/C- 编号）、质量门槛 |
| `docs/AIROOT-v0.3-规范审查报告.md` | 审查结论、已解决 P0、**文末追加的当前状态节**（自称描述当前状态，故不得否认已交付的产物——守卫第十六组，见契约草案 §48；前文快照与计数是审查当时的事实，仍保留原样并标注"审查时的数字"） |
| `docs/AIROOT-v0.3-实现决策记录.md` | **ADR 日志**：语言路线（Python P1 → Rust P2）、三处 Schema 缺陷处置、**ADR-0004 管家模型** |
| `docs/AIROOT-v0.3-管家模型与数据根契约草案.md` | **提案（未落地，第二版）**：reference-first 管家模型、数据根、能力白名单、`where` 优先级改写、**依赖分流与确认、环境变量持久化、删除语义分级、能力边界声明** |
| `docs/AIROOT-v0.3-诊断码与ReasonCode表.md` | **D1–D10 不变量定义 + reason code → 退出码权威映射** |
| `docs/broker/AIROOT-受保护Broker方案-v1.md` | Protected machine mode 信任边界、IPC、提交算法、审计 |
| `cli/schema/*.schema.json` | **19** 个 JSON Schema（draft 2020-12）= 第 1 层契约 |
| `cli/app/airoot/` | **P1 CLI Core**（Python 包）：`cli.py` 入口、`registry/`、`tx/`、`caps/`、`ext/` |
| `cli/app/airoot/caps/` | **能力域模块总览**（22 个）：`where.py` 选择与降级、`discovery.py` 白名单发现、`probe_pe.py` 只读 PE 静态探测、`version.py` 版本序与约束、`selection.py` 选择策略、`doctor.py` D1–D10 诊断、`inventory.py`/`effective.py` 声明与有效事实视图、`environment.py` 环境变量与模板、`exposure.py` reference 暴露计划、`planner.py` 计划与路由（`CONFIRMATION_OPTIONS`）、`lifecycle.py` 生命周期、`sources.py` 上游校验和解析、`boundary.py` 能力边界准入、`search.py` 搜索协议面（请求/上限/root/cursor/有界 crawl；syscall 走 `\\?\` 扩展长度形式，报告路径仍是普通形式，§31/§36）、`searchindex.py` **crawl 建的持久索引**（SQLite `cache/search/index.db`，整文件原子替换，无 WAL；**不是 USN 索引**，§32）、`usn.py` **只读 USN 能力探测**（默认惰性、失败即数据；USN 索引器本身属 P2，ADR-0020）、`desired.py`/`toolstate.py`/`pathexposure.py`/`session.py`/`rebuild.py`（各自见下行） |
| `SKILL.md` | **Skill 入口**（Skill 根 = 仓库根 `AIROOT`，**不能**放在 `cli/` 下）：行为边界、命令地图、三选一、退出码、绝不做的清单 |
| `agents/airoot.json` | Skill 的机器可读调用元数据：问题 → 命令 → 该读哪些字段 + **结构化的 `never`**（每条带稳定 `id`、`statement` 与锚点 `skill_item`/`stated_in`，与 `SKILL.md` 的《绝不做的清单》**双向对应**，见契约草案 §41）/`not_implemented` |
| `references/` | 按需参考：`reason-codes.md`（按退出码分组的速查）、`confirmation.md`（确认与批准） |
| `cli/bin/airoot.cmd` | 开发期 launcher（CRLF！只设进程内 `PYTHONPATH`，绝不写 PATH） |
| `cli/extensions/airoot-fake-extension.json` | 假扩展 manifest（通信协议演示，无副作用） |
| `cli/tests/` | pytest L0/L1 套件（742 项，含 65 项常驻跨工件一致性审计 `test_l0_consistency.py` 与 `test_l1_agent_read_fields.py`——后者用真实 root 逐个跑出 agent 面文档，验证 `agents/airoot.json` 的 **117 条 `read` 路径**全部存在）+ `fixtures/golden/`（Rust 版验收语料，**27 个 fixture**，含三份 `search_*_response.json`、`doctor_stale_search_index.json`，以及五个**契约目录**：`transaction_transitions.json`（合法移动表）、`invariant_catalogue.json`（D1–D10 分组）、`frozen_capabilities.json`（冻结能力清单）、`scenario_ledger.json`（**107 个场景编号**的完整台账）、`execution_bounds.json`（**决定拒绝的每个数值**：搜索上限三件套 / `MAX_ROOTS` / 爬取与索引边界 / 白名单扫描边界 / 三选一 / 优先级 / artifact 与 PE 检查字节上限 / 五个 policy revision）；见 §45–§50）+ `scenario_ledger.py`、`execution_bounds.py`（这两个目录的解析器与处置表）+ `real_machine_acceptance.py`（真机验收脚本，非 pytest） |
| `cli/app/airoot/policy/discovery-whitelist.json` | **能力白名单**：按 `capability_id` 的证据谓词（不是目录名）；缓存/GUI/服务排除名单（revision `wl-3`） |
| `cli/app/airoot/policy/sources.json` | **可信来源清单**（`src-1`）：允许的 host、每个能力的 artifact 与校验和 URL 模板。**不含 digest**——摘要只来自上游校验和文件 |
| `cli/app/airoot/policy/selection-policy.json` | **选择策略**（`sp-1`）：`where` 里 steward reference 与 owned payload 的机器级先后（`precedence`，默认 `steward`） |
| `cli/app/airoot/policy/search-policy.json` | **搜索策略**（`srch-4`）：`limit`/`max_duration_ms`/`max_staleness_ms` 的**实现上限**、默认值、crawl 边界（深度/记录数/不跟随 reparse）、索引块（`index.path=cache/search/index.db`、记录上限、`max_age_ms`=**doctor 判"索引过旧"的判据**、`native_candidate`=协议里那个 USN 实现的身份与前置条件）。上限是数据不是代码，超出即 `EXTENSION_INPUT_INVALID`(8)（ADR-0017/0019/0020） |
| `cli/extensions/airoot-native-search-extension.json` | **`file_search` 的扩展声明**：`implementation_id=airoot-native-search-crawl`、`implementation_kind=fallback`（这个 build 只有受控 crawl + 它建的索引，**没有 USN 索引**）；声明 `search`/`status`/`explain` 三个只读操作与 `refresh`（写 `cache\search` 的派生缓存） |
| `cli/app/airoot/caps/backends/` | **Install Backend**：`base.py` 九个冻结声明 + 九步协议；`portable_file`（本地无脚本 artifact）、`https_artifact`（HTTPS + 强制 SHA256） |
| `cli/app/airoot/caps/desired.py` | **desired 层**（`state/desired.json`，§17 形状）：`pin`/`clear`/`evaluate`；只写意图，不写 registry、不改 binding |
| `cli/app/airoot/caps/toolstate.py` | **只读观察面**：`tool list/status/verify`（整树 digest + 入口点存在性，绝不执行 payload、绝不修复） |
| `cli/app/airoot/caps/pathexposure.py` | **PATH 不变量检查**：重复 AIROOT 条目 / store 版本目录 / 非授权目录 / launcher 缺失；永不写 PATH |
| `cli/app/airoot/caps/session.py` | **会话快照栈**（`state/sessions/<id>.json`）：嵌套激活、`deactivate` 恢复旧值、`SESSION_STATE_STALE`。不是凭据，不进 registry |
| `cli/app/airoot/caps/rebuild.py` | **派生状态重建**：只重写 `state/registry.json` 与 `logs/audit/events.json`，旧投影归档；数据库永不重建，陌生对象只报告 |
| `cli/app/airoot/policy/capabilities.json` | **冻结能力清单**（`cap-1`）：能力身份/kind/入口/副作用上限/scope。白名单加载期校验"条目必须在此清单内" |
| `cli/fake_vertical_slice/fake_vertical_slice.py` | 旧协议切片，**保留为协议 oracle，不要删** |
| `docs/schema/README.md` | schema 边界表、兼容规则、P1 期间的修正记录 |

## 3. 文档权威层级（改文档前必读）

冲突时按此优先级，**不要静默改高层文档来迁就低层实现**：

1. 已发布的 `cli/schema/*.schema.json` 与 SQLite migration —— 机器可执行的最终契约；
2. `docs/AIROOT-v0.3-三大核心契约方案.md` —— 权限、批准、状态事实、事务恢复；
3. `docs/AIROOT-总体方案规划-v0.3.md` —— 目录、对象模型、CLI、Skill、生命周期；
4. `docs/AIROOT-搜索能力与工具集成协议方案.md` —— `file_search` 与 Extension profile；
5. `docs/AIROOT-v0.3-验证与测试方案.md` —— 只定义验收证据，不改变运行时语义。

规范用语：`必须` = 实现与测试都要满足；`应该` = 默认行为，偏离必须记录；`可以` = 兼容性/实现选择。

**已发现的三处“文档与 Schema 冲突”，一律以 Schema 为准**（详见 ADR-0003）：`transaction` 的 `ROLLED_BACK`、成功码不能用 `OK`、Extension manifest 用 `operations` 而非 `operation_policies`。重复踩这些坑等于浪费一轮。**同类冲突后来又有三处**（ADR-0017/0018）：搜索协议 §6.1 示例 manifest 的 `operation_policies`（同一条规则）、其 `target_scope: declared_roots`（scope 枚举里没有这个值）、以及 §8 示例的 `airoot search --root D:\Projects`（`--root` 在本项目里是 AIROOT root，搜索 root 用 `--search-root`）。

**一处已于步骤 5–6 落地的契约变更**：`where` 的选择优先级（规划 §3.2:174、§9.10）已从 **managed-first** 改为 **steward-first**（ADR-0006）：健康的 external reference 是一等候选，机器级它与 owned payload 的先后由 `policy/selection-policy.json` 的 `precedence` 决定（默认 `steward`）；"owned 坏了 + reference 健康"是**正常降级** `CURRENT_SOURCE_DEGRADED`（退出码 2），`CONFLICT_MANAGED_BROKEN` 不再由 `where` 发射（码仍注册）。`doctor` 也已扩展到数据根与 reference（D1/D3、`--include-unmanaged`）。

## 4. 术语（严格区分，混用即算错误）

| 术语 | 含义 |
|---|---|
| `capability_id` | 对 Agent 暴露的稳定能力，如 `file_search`、`python` |
| `extension_id` | 提供能力协议的模块身份 |
| `implementation_id` | 某 capability 在某 binding key 下的具体实现身份 |
| `install_backend_id` | 获取/验证/stage/commit 受控实例的后端（**不是**能力入口） |
| `tool_id` / `instance_id` | 受控工具逻辑名 / 不可变的版本·平台·架构对象身份 |
| `managed_tool_instance` / `runtime_instance` | AIROOT 拥有生命周期的 payload / 受控运行时 |
| `external_reference` | 指向外部对象的只读引用，**不拥有生命周期** |

**`provider` 不得作为新协议字段。** 两组必须分清：能力扩展（定义“怎么调用”）vs 受控工具实例（定义“哪份 payload 由 AIROOT 维护”）；五层事实 `desired`/`declared`/`physical`/`effective`/`historical`。分区 **R** 受保护可执行、**W** 可写但不参与机器级发现、**P** 项目自治。恒定式：**Zone W 永不进入 machine PATH**。

P1 中 `capability_id` 与 `extension_id` 的实例：能力 `fake-echo` 由假扩展提供；模拟受控工具的能力是 `fake-tool`，其 `install_backend_id` 是 `fake_fixture`。

## 5. 已冻结契约（不得在编码中途改变）

1. v1 默认 **Protected machine mode**；未实现前必须标 `security_mode=policy_only` + `enforcement=same_user_can_bypass`（P1 当前状态）。
2. Skill 根是 `AIROOT`，入口是 `AIROOT\SKILL.md`（**不能**放在 `cli\` 下）；CLI 与运行数据根是 `AIROOT\cli`。
3. `store` 是唯一 payload 存储；`tools`/`env` 只是 binding/view；`cache\search` 是可重建索引。
4. SQLite registry/event 是 declared/historical 权威；JSON 投影与 `logs\audit` 是派生物。
5. 同一 binding key **只有一个 active implementation**（DB 层用 partial unique index 保证）。
6. 事务顺序：

   ```text
   PROPOSED -> APPROVED -> FETCHED -> VERIFIED -> STAGED -> COMMITTED
     -> REGISTERED(inactive) -> ACTIVE_BOUND -> EXPOSED -> VERIFIED_AGAIN -> FINALIZED
   ```

   **`ACTIVE_BOUND` 是唯一可以改变 active binding 的提交点**（与 generation 同一个 SQLite 事务）。回滚只切 binding，新 payload 标 `broken` 并保留为证据。
7. 外部发现**默认不迁移**；`rebuild`/`discover`/`doctor` 发现陌生对象只能报 `unmanaged`/`orphan`/`quarantine`，**永不删除**。
8. 机器 PATH 只允许一个 AIROOT 条目：`AIROOT\cli\exposure\bin`；版本目录绝不直接进 PATH。
9. 退出码 0–9 与 reason code 的映射见 `docs/AIROOT-v0.3-诊断码与ReasonCode表.md`（代码是 `cli/app/airoot/exits.py`）。**成功码是 `SUCCESS`，不是 `OK`。**
10. `where` / Extension envelope / `doctor` 的字段名与 `reason_code` 已冻结；`doctor` 的 `severity`/`code`/`evidence`/`impact`/`remediation` 稳定。
11. `plan_hash` 用 `jcs-rfc8785-compatible` 标签、计算时排除 `plan_hash` 本身（P1 用确定性排序 JSON 作测试替代，已在 schema README 记录）。

## 6. 环境与可运行命令

工具链（实测）：Python **3.11.11**（miniconda `envs\model`）、`jsonschema` **4.25.0**、`pytest` 9.0.2。**没有** requirements/锁文件；唯一第三方运行时依赖是 `jsonschema`。

```powershell
# 全部测试（L0 协议 + L1 registry/事务/where/doctor/扩展/管家域 + CLI + 端到端验收）
python -m pytest cli/tests -q

# 旧协议切片（回归基线，必须保持绿灯；schema_count 现在是 19）
python .\cli\fake_vertical_slice\fake_vertical_slice.py validate-schemas
python .\cli\fake_vertical_slice\fake_vertical_slice.py test

# 真实 CLI（需要先有一个 root；P1 不会自己创建系统位置的 root）
python -m airoot --root <root> doctor --json          # 或设 AIROOT_HOME
cmd /c .\cli\bin\airoot.cmd --root <root> root status --json

# 管家域：声明数据根 → 只读扫描 → 登记引用（全程不拥有、不删文件）
python -m airoot --root <root> data-root add D:\env --role runtime --json
python -m airoot --root <root> discover --json
python -m airoot --root <root> adopt D:\env\java --mode reference --json
python -m airoot --root <root> inventory --class external_reference --json
python -m airoot --root <root> forget external/dr-env/java --json

# 步骤 8：依赖分流 + 计划（路由结论进 metadata.routing，未确认不落盘）
python -m airoot --root <root> scope decide java --json
python -m airoot --root <root> scope decide python --project <项目> --json
python -m airoot --root <root> scope memory --project <项目> --json
python -m airoot --root <root> plan archive --scope data-root --target data-root:dr-env --dry-run --json
python -m airoot --root <root> plan build --scope project --project <项目> --json
python -m airoot --root <root> plan build --scope data-root --target data-root:dr-env --project <项目> --json  # SCOPE_UPGRADE_REQUIRES_APPROVAL(4)

# 步骤 9：环境暴露（会话级只打印脚本；持久化必须带 approval token）
python -m airoot --root <root> env activate external/dr-env/java --shell powershell
python -m airoot --root <root> exec external/dr-env/java -- java -version
python -m airoot --root <root> exec --env external/dr-env/java -- java -version   # §15.1 的写法，同义
# `exec` 的 `--` 之后原样交给子进程；之前的选项属于 AIROOT（写在 id 之后的 `--json` 也认）
python -m airoot --root <root> env persist external/dr-env/java --dry-run --json
python -m airoot --root <root> env persist external/dr-env/java --token-file <token.json>
python -m airoot --root <root> env list --json
python -m airoot --root <root> env forget external/dr-env/java --dry-run --json   # 先看要还原什么
python -m airoot --root <root> env forget external/dr-env/java

# 步骤 5：steward-first 选择 + 数据根诊断
python -m airoot --root <root> where java --json                  # 直接命中 reference
python -m airoot --root <root> where java --version ">=99" --json  # VERSION_UNSATISFIED，不猜
python -m airoot --root <root> doctor --json                       # D1 覆盖每个数据根
python -m airoot --root <root> discover --record --json            # 记录 unmanaged 观测
python -m airoot --root <root> doctor --include-unmanaged --json   # 才看得到 UNMANAGED_OBJECT_PRESENT
python -m airoot --root <root> doctor --verify --json              # 追加 entrypoint 整文件 digest

# 重新生成 golden 语料（仅在有意变更输出后）
$env:PYTHONPATH='D:\AIRoot\cli\app'; python cli/tests/golden.py

# 步骤 9d：派生状态重建（doctor 的 remediation 就是它）
python -m airoot --root <root> rebuild --plan --json    # 只读预演
python -m airoot --root <root> rebuild --json           # 重写投影 + 归档旧快照；数据库不动

# 步骤 9c：可信来源与上游校验和（digest 来自上游，不是自己算的）
python -m airoot --root <root> source list --json
python -m airoot --root <root> source resolve build --version 3.31.6 --offline-checksum <SHA256SUMS> --source-out <resolved.json> --json
python -m airoot --root <root> plan build --source-json <resolved.json> --json   # 构造真实 artifact 计划

# 步骤 9f：只读观察面 + PATH 不变量
python -m airoot --root <root> tool list --json
python -m airoot --root <root> tool status <instance-id> --json
python -m airoot --root <root> tool verify <instance-id> --json     # 整树 digest，不修复
python -m airoot --root <root> path verify --json                   # 只读，永不写 PATH

# 步骤 9g：desired 层（表达意图，不改变 declared）
python -m airoot --root <root> tool pin build --version 3.31.6 --offline-checksum <SHA256SUMS> --json
python -m airoot --root <root> tool pin java --version 25.0.2.0 --json   # 记下愿望；无来源则如实报 plan_blocked_by
python -m airoot --root <root> tool pin build --clear --json

# 步骤 11-12：删除分级（只删 AIROOT 自己装的）+ 能力边界
python -m airoot --root <root> capability list --json
python -m airoot --root <root> capability check D:\env\java --json
python -m airoot --root <root> tool retire <instance-id> --json
python -m airoot --root <root> tool gc --plan --json
python -m airoot --root <root> tool gc --apply --token-file <token.json>
python -m airoot --root <root> uninstall <instance-id> --dry-run --json
python -m airoot --root <root> uninstall <reference-id> --json   # OWNERSHIP_REQUIRED(7)，只输出路径

# 步骤 31-32：搜索协议面 + crawl 建的持久索引（**不是 USN 索引**）
python -m airoot --root <root> search refresh --json               # 建索引：records/coverage/替换了哪个派生文件
python -m airoot --root <root> search java --ext .exe --json       # 有索引 → 退出码 0 / status=ok / fallback=null
python -m airoot --root <root> search --search-root D:\env --json  # 显式搜索 root（--root 仍是 AIROOT root）
python -m airoot --root <root> search java --managed-only --json   # 只要 AIROOT 认识的路径（reference / owned）
python -m airoot --root <root> search --query status --json        # 搜一个叫 status 的文件（保留字只在首位生效）
python -m airoot --root <root> search status --json                # 索引状态 + freshness + 记录数
python -m airoot --root <root> search status --probe-native-index --json  # 只读探一次卷：为什么 native 索引不可用（§34）
python -m airoot --root <root> search explain java --json          # 会用索引还是 crawl 回答
python -m airoot --root <root> search implementations --json       # 声明 file_search 的实现清单
python -m airoot --root <root> search rebuild --json               # refresh 的协议拼写（同样是整次遍历）
python -m airoot --root <root> search java --max-staleness-ms 1 --json  # 索引太旧 → SEARCH_RESULT_STALE(2)

# 真机验收（步骤 5-6 + 8-9 + 31-32 全路径；对真实 D:\env 只读，临时 root 自清理，不写 HKCU）
python cli\tests\real_machine_acceptance.py
```

`pytest` 由 `cli/tests/conftest.py` 注入 `cli/app` 到 `sys.path`；直接 `python -m airoot` 需要自行设置 `PYTHONPATH=cli/app`（或走 `cli\bin\airoot.cmd`）。

**测试根只在 `cli/tests/.tmp/` 下创建并自动清理**。测试必须不污染宿主机的 PATH / 注册表 / ACL / 真实 root —— `conftest.py` 有会话级守卫断言，破坏它测试会失败。

### 运行环境注意（会真实咬人）

- 受限文件沙箱（workspace-write）下，临时目录的 `scandir`/`chmod` 会被拒绝，测试与切片会报 `[WinError 5] 拒绝访问`；需要 `danger-full-access` 或非沙箱终端。
- 受限沙箱下不要用管道捕获外部程序输出（如 `python ... | Select-Object`），会报 `Program 'python.exe' failed to run: Access is denied`。
- **`cli/bin/airoot.cmd` 必须是 CRLF 行尾**，否则 `cmd.exe` 会把 `rem` 行切碎成命令。用文件工具重写它之后要确认行尾。
- **不要用 `Get-Content` 校验 UTF-8 中文文件**：本环境的 pwsh 把它按 GBK 解码，会输出 `锛氱瀹舵ā鍨` 这类乱码并**少算行数**（实测同一文件 `ReadAllLines=630` 而 `Get-Content=465`）。要核对内容用 `read`/`grep` 工具，或用 `[System.IO.File]::ReadAllText(...)` / `ReadAllLines(...)`。
- **标准输出也是 GBK**：`print()` 非 GBK 字符（如 `®`）会抛 `UnicodeEncodeError`。CLI 的 JSON 用 `ensure_ascii` 默认值所以安全；自己写调试脚本时用 `.encode('ascii','replace').decode()` 包一层。
- **`cli/tests/.tmp/` 会被 pytest 的会话夹具整个删掉**：不要把任何需要跨运行保留的 root/数据根放进去（放进去的示例 root 会在下次跑测试时消失）。它也是唯一允许建测试 root 的地方。

## 7. 改动约定

- **改 Schema**：跑 `validate-schemas`（19 个）+ `pytest`，并同步 `docs/schema/README.md` 的边界表与兼容规则。`schema_version: 1` 是唯一主版本；新增**可选**属性是 minor 兼容；改类型/枚举/必填/digest 算法/状态含义需新 schema id（ADR-0003 记录了唯一的例外及其理由；`reference-plan` 是新增边界而非放宽枚举）。
- **改对外 JSON 输出**：必须重新生成 golden 语料（`cli/tests/golden.py`）并在同一个变更里提交，否则 `test_golden_fixtures_reproduce_exactly` 会失败——这是 Rust 迁移的验收面。
- **自校验**：核心在打印任何对外 JSON 之前调用 `schema_io.validate_self`；自校验失败是**实现缺陷**（`SELF_VALIDATION_FAILED`，退出码 8），永远不要把它降级成领域性回滚。`simulate.PROPAGATING_CODES` 就是这条规则。
- **改状态机**：`cli/app/airoot/tx/states.py` 的迁移表是 §14.1 的转录；改它必须同步跨文档测试 `test_every_documented_state_is_representable_in_the_schema`。
- `test_hmac_sha256` **只允许出现在测试/模拟路径**；核心只做**校验**，签名实现放在 `cli/tests/fake_issuer.py`，`airoot approve` 永远不能凭空制造批准。
- 平台范围：v1 只做 Windows provider；macOS/Linux 保留抽象接口。
- 语言风格：文档中文为主、英文术语保留原文；代码标识符与注释英文。新增文档沿用 `docs/AIROOT-*.md` 命名。
- 变更设计语义时，**同时**改契约层文档、Schema 与（如适用）ADR；不要只改一处。
- **取舍默认"放宽"**（**ADR-0021**）：一个问题同时存在"放宽"和"收紧"两种读法或选项时，**默认取放宽的那个**，不必每次再问。它**不**适用于四种情况——图层级（不得违反已发布 Schema / layer-2 冻结契约）、诚实规则（§8 的禁令、"不编造确定性"、"失败即数据"）、管家模型的结构性不变量（数据根内文件永不删除、reference 永不 `uninstall`）、审计守卫（把一致性检查加严不是"收紧权限"）。**当放宽会摧毁一条更强的规则时，不机械照做，按这四种情况处理并如实报出来**：§4.1 的边集就是按放宽落地的（29 → **62** 条边），但 `FINALIZED` 保持死胡同、`EXPIRED` 不从已改过 active binding 的阶段进入。

## 8. 已知缺口与下一步

P1 刻意**没有**发明的东西（需要单独决策，别顺手补一个算法）：

- `machine_id` / `session_id` / `project_id` 的生成算法——P1 只接受注入或显式入参；
- 根定位的卷标扫描（P1 只有 `--root` / `AIROOT_HOME`，失败即关闭）；
- registry migration 工具、event 保留期、`logs\audit` 导出协议（只有骨架）；
- `exposure\bin` launcher——P1 的 `EXPOSED` 是“用一次全新的 registry 读取观察到新 binding”，**没有写任何 launcher 文件**；
- 真实 `ed25519` 校验与受保护 issuer（P1 遇到 `ed25519` token 会显式报 `PROVENANCE_FAILED`）；
- `file_search` 的 **Native Index 进程模型**（USN journal 消费、常驻索引器、`cache\search` 事务）——协议面与受控 crawl 已交付（§31），但"索引器怎么跑"仍需要单独决策。

路线图（规划 §19）：**P0 冻结契约 → P1 最小 Core（已完成）→ P2 Windows Protected State** → P3 `file_search` → P4 Portable Transaction → P5 Runtime → P6 Session/Project → P7 加固 → P8 其他平台。

P2 的第一件事就是 **ADR-0001 的语言切换**：用 AIROOT 自己的 plan/approval/fetch/verify/digest 规范获取 Rust 工具链（推荐 `rustup-init.exe` + HTTPS + `SHA256SUMS` + `--no-modify-path`，免管理员），然后以 `cli/tests/fixtures/golden/` 为验收面逐字节对齐。**MSVC Build Tools 路线不符合 v1 无脚本 portable 基线**，不要默默采用。

**下一步的排序问题**：管家域步骤 1–9、11–12 与 **P3 的协议面 + crawl 建的持久索引（§31–§32）**
已全部交付且不需要管理员权限；剩下的都需要 P2 的受保护状态（ACL / broker / machine PATH /
machine 级环境变量）、P4 的真实安装后端（真实 artifact 下载 + `gc` 作用于真实 payload），
或 P3 本体的**USN 常驻索引器**（journal 消费 + 后台进程模型——它需要 broker 做初始的全量枚举，
因为那一步要提权；§34 已经把"这台机器能不能"变成可探测的事实，见 ADR-0020）。

### 完成判定的口径

允许说：“方案契约与验证计划已具备，P1 协议级 Core 已通过验收；管家域的**非拥有式登记**（数据根 → 只读发现 → reference，含多版本与活跃版本观测）、**依赖分流决策**与 **user 级环境变量持久化**（plan → approval → 写入 → 精确还原）、**steward-first `where`** 与**数据根/reference 诊断**、**删除分级**（只删 AIROOT 自己装的，reference 永不 `uninstall`）、**能力边界**、**Skill 适配层**（`SKILL.md` 是 Skill 根的唯一入口，带漂移守卫）与**可信来源清单**（digest 只来自上游校验和文件）、**`rebuild`**（派生状态可重建、权威不可自重建）、**session 激活的完整语义**（快照栈 + 恢复式 `deactivate` + `SESSION_STATE_STALE`），以及 **`search` 的协议面与 crawl 建的持久索引**（请求/响应按已发布 Schema；实现上限、cursor 绑定索引 generation、root 规则齐备；索引健康时退出码 0 并给出 `freshness=current`，覆盖不足或损坏时如实回落 crawl）已可对真实 `D:\env` 运行（742 项测试、旧切片无回归、`cli/tests/real_machine_acceptance.py` 全项通过，真机上 `search refresh` 建了 51 073 条记录，`search status --probe-native-index` 报出真实卷读数）。生产实现（受保护 broker/ACL、USN 常驻索引器）与依赖它们的 machine 级持久化待后续。”

不允许说：“AIROOT 已实现 / 已可用 / 已具备 Everything 级性能。”`search` 尤其**不能**被说成 Everything 级性能：它的索引由一次目录遍历建立，`freshness.current` 只表示"这份清单是最近一次遍历建立的"，与 USN journal 的"游标没断档"不是一回事。

任何测试都不得污染开发机的 PATH、注册表、ACL 或真实 AIROOT root。

## 9. 维护远端仓库

远端 **`https://github.com/Joyakuku/AIRoot.git`**（**public**），默认分支 `main`；远端仓库根就是 Skill 根
（即本仓库根）。`gh` 已登录（账号 `Joyakuku`）；在新机器上先跑 `gh auth setup-git` 配置 https 的
credential helper。

```powershell
git add -A
git commit -m "..."      # 提交信息用祈使句说明"为什么"，不要写 "update files"
git push
```

- **`.gitattributes` 是 `* -text`，不要改成 `text=auto`。** golden 语料是**逐字节**验收面
  （`test_golden_fixtures_reproduce_exactly`），而 `cli/bin/airoot.cmd` 必须是 CRLF；树里本来就混着
  CRLF 与 LF，任何 EOL 自动转换都会让新克隆与工作树不一致，从而让"语料逐字节可复现"变成一句假话。
- **提交前跑 `python -m pytest cli/tests -q`。** 改了对外 JSON、语料、或文档里的计数时，**在同一个提交里**
  重生语料并同步所有计数——守卫会红，那是设计好的行为，不要靠改守卫绕过。
- 不提交 `cli/tests/.tmp/`、`__pycache__/`、`.pytest_cache/`（见 `.gitignore`）。
- 远端是 **public**：不要把本机凭据、卷序列号、USN journal ID、真实用户名等指纹写进任何被提交的文件。
  当前树里没有这些（已实测扫描过），以后新增"实测事实"时留意这一点。
