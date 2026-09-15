# AIROOT：Everything 类搜索能力与统一工具集成协议

## 结论

AIROOT 可以集成 Everything 类的高速文件搜索能力，但集成对象应该是它的可验证架构机制，而不是 Everything 软件本体。更大的目标是：AIROOT 逐步成为本机工具能力层，覆盖多种不同类型的工具；同一种能力在同一个平台和作用域内只保留一个权威实现。

推荐的目标形态是：

```text
AIROOT CLI
  └── Capability Extension Runtime
        ├── file_search extension          v1：AIROOT Native Index
        ├── content_search extension       后续能力
        ├── archive extension              后续能力
        ├── media/pdf extension            后续能力
        └── project/process extensions     后续能力
```

AIROOT Skill 只负责把自然语言转换成 `airoot search --json` 请求，并解释结果。索引、查询、权限过滤、健康检查和降级由 CLI/后台索引进程负责。

这里的 Extension 是 AIROOT 的能力模块，不是“外部工具维护器”。Everything、`fd`、`rg`、7-Zip 等软件名称不出现在 AIROOT 的默认能力接口中；它们最多作为设计参考、测试基准或用户明确启用的兼容依赖。

## 一、概念边界：能力扩展与 Managed Tool 分离

本协议中的“能力工具”专指 Capability Extension；“受控工具”专指 Managed Tool Instance。两者不是同一个对象：

    Capability Extension  -> 定义能力协议、请求/响应和健康检查
    Managed Tool Instance -> 保存 AIROOT 实际维护的 payload、版本、manifest、binding
    Runtime Instance      -> 保存 AIROOT 管理的 Python、Node 等运行时
    External Reference    -> 只记录外部路径和漂移状态，不拥有源文件生命周期
    Install Backend       -> 获取、验证、stage、commit、rollback 受控实例

AIROOT 必须维护明确纳入范围的 managed tool/runtime，例如 portable jq、ffmpeg、Python 和 Node；但不会因为机器上存在 Everything、fd、rg 或 7-Zip 就自动接管它们。只有显式 reference、import、recreate 或受支持的安装计划成功后，才会建立相应 registry instance 和 binding。

Everything 的核心价值被转化为 AIROOT Native Search Extension 的实现机制；Everything 软件本体仍是外部软件或可选 adapter。搜索发现一个 jq.exe、python.exe 或 ffmpeg.exe 只产生候选和证据，不自动创建 managed instance，也不改变 tools、env、store 或 PATH。
### 能力扩展的边界

一个 Extension 可以包含：

- CLI 子命令；
- 本地库或后台进程；
- 输入输出 schema；
- 权限和数据范围声明；
- 健康检查；
- 取消、超时和错误处理；
- 与 AIROOT registry 的能力绑定。

一个 Extension 不应默认包含：

- 外部软件安装包；
- 自动下载的第三方二进制；
- 外部软件的升级任务；
- 多个同类软件的维护清单；
- 把外部软件直接暴露给 Agent 的私有命令行。

## 二、统一能力层，而不是软件堆

AIROOT 的对象应该是 `capability type`，不是工具文件名：

```text
file_search       文件名/路径搜索
content_search    文本内容搜索
archive           压缩包创建和解压
media_probe       媒体格式和元数据探测
image_transform   图片转换
pdf_extract       PDF 文本/元数据提取
process_inspect   进程和端口检查
```

每一种 capability 在一个完整 binding key（root/machine、project/session identity、platform、scope）中只有一个 `active implementation`。v1 优先使用 AIROOT 内置 Extension；外部适配器只是兼容边界，不是 AIROOT 要维护的软件包列表。

```text
(windows, machine, file_search) -> airoot-native-search-native-index
(windows, machine, archive)     -> airoot-archive-extension
(windows, project, content_search) -> airoot-content-search-extension
```

这样 Agent 只需要知道“如何调用文件搜索能力”，不需要在 Everything、`fd`、PowerShell、Windows Search 之间自行选择。

### 同一类型只保留一个的具体含义

- registry 中同一 capability type 只能有一个 active implementation；
- 不同实现可以用于开发测试或显式降级，但不能同时成为 Agent 的默认入口；
- 实现选择由确定性策略完成：平台、能力覆盖、权限、健康度、性能基线和用户策略；
- 实现切换是一次有记录的 binding transaction；
- fallback 是实现内部的降级路径，不是第二个需要维护的同类软件；
- Skill 和 Agent 永远调用 capability ID，不直接调用 Everything、`rg` 或其他软件名。

### 工具类型示例

下表是架构示例，不是 v1 必须一次实现的工具清单：

| Capability type | 统一入口 | 首选实现示例 | 其他同类工具如何处理 |
|---|---|---|---|
| `file_search` | `airoot search` | AIROOT Native Search Extension，复现 Everything 的索引机制 | Everything/`fd` 只是参考、基准或显式 adapter |
| `content_search` | `airoot content search` | AIROOT Content Search Extension | `rg`、PowerShell 只是实现参考或可选 adapter |
| `archive` | `airoot archive` | AIROOT Archive Extension | 7-Zip、bsdtar 不是 AIROOT 的维护清单 |
| `media_probe` | `airoot media probe` | AIROOT Media Extension | ffprobe 等只是参考或可选外部依赖 |
| `pdf_extract` | `airoot pdf extract` | AIROOT PDF Extension | 其他解析器不进入默认能力入口 |
| `process_inspect` | `airoot process inspect` | AIROOT Windows Process Extension | 不把多个命令行工具叠加进 PATH |

“一个类型一个工具”解决的是能力入口和行为一致性，不等于机器上绝对不能安装其他同类软件。未选中的软件可以被用户正常使用，但 AIROOT 不负责把它们混入默认发现路径。

## 三、Everything 为什么快

Everything 的速度优势不是某一条神秘命令，而是几个架构选择的叠加。以下是可以通过公开 Windows 能力和独立实现复现的机制；不复制其私有源码或内部数据库格式。

### 1. 搜索目标是文件名和路径元数据

它的核心任务是：

```text
文件名、目录名、完整路径、大小、时间、属性
```

不是默认扫描每个文件的内容。因此查询数据量和 I/O 远小于 `grep`、`rg` 或全文搜索。

### 2. NTFS 直接获取文件清单

对 NTFS 卷，文件名和目录信息可以从 NTFS 的元数据中批量获得，不必对每个目录执行递归的 `FindFirstFile`/`FindNextFile`。

AIROOT 的独立实现可以研究并使用公开 Windows 文件系统控制接口，例如 NTFS 元数据枚举和文件 ID，而不是递归扫描整个目录树作为主路径。

### 3. 使用 USN Journal 增量更新

初始建立索引后，不需要每次重新扫描全盘。NTFS USN Change Journal 会记录创建、删除、重命名和属性变化，索引器可以从上次 USN 位置继续消费变化。

```text
initial snapshot
      ↓
remember volume + journal id + USN position
      ↓
read new USN records
      ↓
update index
```

### 4. 常驻索引，而不是每次启动命令重新扫描

Everything 的用户体验依赖一个常驻索引状态。AIROOT 也不能把 `airoot search` 实现为每次递归遍历磁盘的短命脚本。

推荐：

```text
airoot-search-indexer.exe  用户级后台进程
airoot search ...          CLI 查询客户端
```

索引数据库是可重建的派生缓存，不属于受信任执行文件，也不进入 PATH。

### 5. 内存友好的倒排/前缀索引

查询应针对路径和文件名建立专用索引，避免每次对所有记录做字符串全扫描。实现可以选择 SQLite FTS/自定义 radix/trigram 索引，但协议不应绑定具体数据结构。

### 6. 文件 ID 优先于路径字符串

NTFS volume serial + file ID 可以稳定识别文件。重命名时更新路径映射即可，不必删除再重新创建一个全新文件对象。

## 四、AIROOT Search 的边界

### v1 支持

- 本地 NTFS 卷上的文件和目录名搜索；
- 路径、扩展名、大小、修改时间、属性过滤；
- 精确匹配、前缀匹配、包含匹配；
- 增量更新和索引健康检查；
- 受权限约束的结果过滤；
- JSON 输出、稳定 reason code 和退出码；
- 没有索引时的受控 crawl fallback。

### v1 不承诺

- 文件内容全文搜索；
- 云盘远程内容搜索；
- 自动搜索用户无法读取的文件；
- 通过 Everything 二进制、DLL 或安装包提供能力；
- 复制 Everything 的私有协议、数据库格式或实现代码；
- 一次性命令在没有索引的情况下达到常驻索引的速度。

## 五、AIROOT Native Index 设计

### 5.1 进程和数据位置

```text
AIROOT\cli\
  extensions\airoot-native-search\indexer\  受保护的索引器程序
  cache\search\index.db        可重建索引数据库
  cache\search\volumes.db      卷、USN journal 和扫描游标
  logs\search\                  索引与故障日志
  tx\search\                    索引重建事务信息
```

`index.db` 和 `volumes.db` 属于派生 physical cache：

- 损坏后可以删除并重建；
- 不作为 capability registry 的唯一真相源；
- 不允许 Agent 直接编辑；
- 不进入机器 PATH；
- 查询结果仍需在返回前检查当前调用者是否可以访问目标路径。

### 5.2 索引生命周期

```text
UNINITIALIZED
  -> SNAPSHOTTING
  -> READY
  -> INCREMENTAL_UPDATING
  -> DEGRADED
  -> REBUILDING
  -> READY
```

进入 `DEGRADED` 的条件包括：

- USN journal 被清除或发生溢出；
- 卷 ID 或 journal ID 变化；
- 索引数据库损坏；
- 索引游标落后且无法补齐；
- 权限导致关键元数据不可读。

### 5.3 初始索引

优先级：

1. 识别本地 NTFS 卷和卷身份；
2. 读取可用的 NTFS 元数据；
3. 建立 file ID、parent ID、name 和属性关系；
4. 写入一致性 snapshot；
5. 记录 USN journal ID 和最后游标；
6. 切换到增量更新。

如果某个卷不支持所需接口，明确降级到目录 crawl，不把慢路径伪装成高速索引。

### 5.4 增量更新

必须处理：

- create；
- delete；
- rename old/new name；
- parent directory change；
- attribute/time change；
- hard link；
- journal reset/overflow。

USN 记录只能说明发生了变化，不能直接作为最终路径真相。索引器需要重新查询相关 file ID 和父目录链，完成最终路径重建。

## 六、Capability Extension Protocol

这是以后扩展 AIROOT 能力的统一约定。Search 是其中一个 profile；压缩、媒体探测、内容搜索等能力可以复用相同的身份、权限、健康、调用和错误协议。它约定的是扩展模块如何加入 AIROOT，不是外部软件如何被 AIROOT 维护。

### 6.0 通用 Capability Extension Contract

Search Extension 是通用 Capability Extension Contract 的一个 profile。以后接入文件监控、内容抽取、压缩、媒体探测或其他本地能力时，先实现通用契约，再声明具体 profile。

```text
Capability Extension Contract
  ├── identity        extension id/version/platform
  ├── capabilities    能做什么
  ├── requirements    权限、依赖、网络、资源
  ├── invocation      输入、输出、超时、取消
  ├── security        root、ACL、执行和数据边界
  ├── health          可用性、版本、新鲜度
  ├── lifecycle       probe/start/stop/refresh
  └── evidence        来源、版本、日志和可解释原因
```

每个能力扩展都必须提供一个机器可读的 manifest：

```json
{
  "extension_id": "example-extension",
  "extension_version": "1.0.0",
  "protocol_version": 1,
  "capability_types": ["file_search"],
  "implementation_id": "example-implementation",
  "implementation_kind": "builtin | external_adapter",
  "platforms": ["windows"],
  "scopes": ["machine", "session", "project"],
  "required_privilege": "user | broker | user_or_broker",
  "network_access": false,
  "executes_scripts": false,
  "data_roots": ["user_selected"],
  "side_effects": [],
  "supports_cancel": true,
  "supports_json": true,
  "health_checks": ["probe", "version", "self_test"],
  "operation_schemas": {
    "invoke": "schema://example/invoke-v1",
    "status": "schema://example/status-v1"
  },
  "operation_policies": {
    "invoke": {
      "operation_kind": "read",
      "target_scope": "declared_roots",
      "approval_required": false,
      "overwrite_policy": "deny",
      "cancellation_semantics": "best_effort"
    }
  }
}
```

通用调用响应使用统一 envelope：

```json
{
  "schema_version": 1,
  "extension_id": "example-extension",
  "operation": "search",
  "status": "ok | degraded | failed",
  "started_at": "...",
  "finished_at": "...",
  "elapsed_ms": 4,
  "data": {},
  "warnings": [],
  "evidence": [],
  "reason_code": null
}
```

这样 AIROOT Core 不需要知道某个外部软件的私有命令行参数，只需要知道扩展 manifest 和 profile schema。外部软件若被适配，也只是扩展的运行依赖，不能因此获得 AIROOT R 区写权限。

### 6.1 Search Extension profile

Native Search Extension 必须声明：

```json
{
  "extension_id": "airoot-native-search-extension",
  "extension_version": "1.0.0",
  "protocol_version": 1,
  "implementation_id": "airoot-native-search-native-index",
  "implementation_kind": "builtin",
  "capability_types": ["file_search"],
  "platforms": ["windows"],
  "scopes": ["machine", "project"],
  "required_privilege": "user_or_broker",
  "data_roots": ["declared_roots", "AIROOT\\cli\\cache\\search"],
  "writes_derived_cache": true,
  "starts_background_indexer": true,
  "freshness_model": "usn_cursor",
  "operation_schemas": {
    "search": "schema://airoot/search-v1",
    "status": "schema://airoot/search-status-v1",
    "refresh": "schema://airoot/search-refresh-v1"
  },
  "operation_policies": {
    "search": {
      "operation_kind": "read",
      "target_scope": "declared_roots",
      "approval_required": false,
      "overwrite_policy": "deny",
      "cancellation_semantics": "best_effort"
    },
    "refresh": {
      "operation_kind": "write",
      "target_scope": "AIROOT\\cli\\cache\\search",
      "approval_required": false,
      "overwrite_policy": "replace_derived_cache",
      "cancellation_semantics": "resume_or_rebuild"
    }
  }
}
```

### 6.2 最小接口

```text
probe()       是否可用、支持什么、当前健康度
invoke(req)   执行能力操作
status()      当前状态、新鲜度和故障
refresh()     请求更新或重建
explain()     说明当前实现和降级路径
cancel()      取消运行中的操作
```

Search Extension 不负责安装外部软件，也不能因为使用了外部适配器就获得 R 区写权限。创建或更新 AIROOT 受控实例时，必须另走 Artifact/Install Backend Contract；安装成功后由 Managed Tool/Runtime Manager 登记 instance、binding、health 和 generation。

### 6.3 Search 请求

```json
{
  "schema_version": 1,
  "query": "python",
  "match": "contains",
  "target": "name_and_path",
  "roots": ["D:\\Projects", "C:\\Users\\user"],
  "extensions": [".exe", ".py"],
  "include_directories": false,
  "accessible_only": true,
  "include_hidden": false,
  "allow_reparse_points": false,
  "min_size": null,
  "max_size": null,
  "modified_after": null,
  "limit": 100,
  "max_duration_ms": 5000,
  "cursor": null,
  "consistency": "bounded_staleness",
  "max_staleness_ms": 30000
}
```

查询请求必须限制：

- root 范围；
- 最大返回数量；
- 最大执行时间；
- 正则或模糊匹配是否允许；
- 是否可以返回隐藏/system 文件；
- 调用者身份和权限。

边界行为固定如下：

- `roots` 为空时只使用当前 scope 的 policy roots，不默认扫描所有卷；root 必须 canonicalize，越界、UNC、未允许的 reparse point 直接返回 `SEARCH_ROOT_UNAVAILABLE`；
- `limit`、`max_duration_ms` 和 `max_staleness_ms` 必须有实现上限，超出上限返回 `EXTENSION_INPUT_INVALID`，不能由调用者无限放大；
- `cursor` 绑定 query hash、root set、scope、implementation generation 和 index generation；任一变化都返回 `SEARCH_CURSOR_INVALID`，不能把旧 cursor 静默解释成第一页；
- `include_hidden=true` 或 `allow_reparse_points=true` 只能在 policy 允许时生效，结果仍按访问 token 过滤；
- `physical_verify` 对每个返回项重新确认存在性、类型和可访问性；查询期间对象消失或重命名时保留结果但标记 `verification=changed`，不伪装为当前事实；
- 索引缺少某个卷、journal 断档或权限不足时，响应必须标记 `freshness.state` 和 `coverage`，不能把部分索引当成全机完整结果；
- 查询被取消或超时不改变 active binding；后台 refresh 可以继续或安全停止，结果必须标记 `cancelled`/`timed_out`；
- `include_directories=false` 时目录命中不得返回；同一个 file ID 的多个 hard link 可以返回多个合法路径，但不能重复计数为多个物理对象。

### 6.4 Search 响应

```json
{
  "schema_version": 1,
  "extension_id": "airoot-native-search-extension",
  "operation": "search",
  "status": "ok",
  "started_at": "2026-09-14T10:00:00Z",
  "finished_at": "2026-09-14T10:00:00.004Z",
  "elapsed_ms": 4,
  "data": {
    "implementation_id": "airoot-native-search-native-index",
    "query": "python",
    "freshness": {
      "state": "current",
      "last_indexed_at": "2026-09-14T10:00:00Z",
      "lag_ms": 420
    },
    "results": [
      {
        "path": "C:\\Users\\Example\\AIROOT\\cli\\exposure\\bin\\python.exe",
        "name": "python.exe",
        "kind": "file",
        "size": 1048576,
        "modified_at": "2026-09-12T02:00:00Z",
        "attributes": ["archive"],
        "management": "managed",
        "capability_id": "python",
        "accessible": true
      }
    ],
    "next_cursor": null,
    "stats": {
      "matched": 1,
      "returned": 1,
      "index_records_examined": 1
    },
    "fallback": null
  },
  "warnings": [],
  "evidence": [],
  "reason_code": null
}
```

`freshness` 必须是响应的一部分。高速但有延迟的索引和慢速但实时的 crawl 不能返回同一种“确定正确”语义。

### 6.5 一致性等级

```text
best_effort       使用当前索引，允许短暂滞后
bounded_staleness  要求不超过指定时间的索引
refresh_then_read  先消费增量，再查询
physical_verify   对返回结果做实时文件存在性确认
```

AI Agent 查找可执行文件时，默认使用 `bounded_staleness`，安装提交后或修复后使用 `physical_verify`。

### 6.6 错误码

```text
SEARCH_NOT_READY
SEARCH_INDEX_DEGRADED
SEARCH_JOURNAL_GAP
SEARCH_ROOT_UNAVAILABLE
SEARCH_PERMISSION_FILTERED
SEARCH_QUERY_INVALID
SEARCH_CURSOR_INVALID
SEARCH_TIMEOUT
SEARCH_BACKEND_UNAVAILABLE
SEARCH_FALLBACK_USED
SEARCH_RESULT_STALE
```

通用协议还应保留以下跨 extension 错误码：

```text
EXTENSION_NOT_FOUND
EXTENSION_VERSION_UNSUPPORTED
EXTENSION_PERMISSION_DENIED
EXTENSION_DEPENDENCY_MISSING
EXTENSION_INPUT_INVALID
EXTENSION_TIMEOUT
EXTENSION_CANCELLED
EXTENSION_OUTPUT_INVALID
EXTENSION_SIDE_EFFECT_BLOCKED
EXTENSION_HEALTH_DEGRADED
```

错误响应也必须使用同一 envelope。即使没有结果，`data` 仍应包含可用的 `freshness`、`fallback`、`candidates` 或过滤统计；不存在的字段使用 `null`，不能把错误对象改成另一套顶层 schema。典型映射为：

| Search reason code | 通用 exit code | 语义 |
|---|---:|---|
| `SEARCH_NOT_READY`、`SEARCH_BACKEND_UNAVAILABLE` | 9 | active implementation 不可调用 |
| `SEARCH_INDEX_DEGRADED`、`SEARCH_JOURNAL_GAP`、`SEARCH_RESULT_STALE` | 2 | 可返回降级结果或需要 rebuild |
| `SEARCH_ROOT_UNAVAILABLE`、`SEARCH_PERMISSION_FILTERED` | 2 | 请求范围部分不可访问；不得泄露被过滤路径 |
| `SEARCH_QUERY_INVALID` | 8 | 请求字段或约束错误 |
| `SEARCH_CURSOR_INVALID` | 8 | cursor 与请求或 index generation 不匹配 |
| `SEARCH_TIMEOUT` | 2 | 超时；可重试或使用更小范围 |
| `SEARCH_FALLBACK_USED` | 2 | crawl fallback 成功但结果不是索引健康语义 |

`SEARCH_FALLBACK_USED` 只有在 fallback 产生可用数据时才返回；fallback 被禁止、取消或超时则同时保留原始失败 reason code。

## 七、权威能力实现选择和降级

AIROOT 不把多个同类实现同时暴露给 Agent。选择流程是：

```text
discover candidates
        ↓
evaluate policy/capability/health
        ↓
select exactly one active implementation
        ↓
use internal fallback only when policy allows
```

对 `file_search`，v1 的 active implementation 应该是 `airoot-native-search-native-index`，由 `airoot-native-search-extension` 提供协议。它直接在 CLI/后台索引进程中复现 Everything 的核心机制。

如果 Native Index 暂时不可用，AIROOT 可以执行 crawl 作为一次性降级操作，但状态必须是：

```text
active implementation: airoot-native-search-native-index
execution: crawl fallback
status: degraded
```

如果用户明确允许使用已安装的 Everything，才可以临时让 `everything-adapter` 为 Native Search Extension 提供兼容实现。此操作不代表 AIROOT 接管 Everything，也不把它加入 AIROOT 的维护范围；更推荐把 Everything 只用于性能对照，不把它变成 AIROOT 的运行依赖。

### Everything Adapter 的定位

它可以作为可选适配器：

- 探测本机已有 Everything 服务或公开 API；
- 通过公开接口发送查询；
- 转换为 AIROOT 统一响应；
- 不下载、安装或携带 Everything；
- 不把 Everything 的数据库当作 AIROOT registry；
- 结果仍要经过 AIROOT 的 root 和访问权限过滤。

如果用户没有安装 Everything，AIROOT Native Index 应独立工作。这样 AIROOT 不会变成“依赖另一个工具才能搜索”的系统。Everything 适配器不是 AIROOT 要维护的第二套软件，而是同一 `file_search` capability 的可选实现适配。

### Active binding 和 scope 规则

Search 的 active binding key 使用总体契约定义的形式：

```text
machine = (root_instance_id, machine_id, platform, file_search, machine)
project = (root_instance_id, machine_id, project_id, project_root_digest, platform, file_search, project)
```

Native Search Extension 的 machine index 可以服务多个用户，但每次返回结果仍必须按当前调用者 token 重新做 ACL/accessibility 过滤；索引内容不能因为由机器级 indexer 建立就向无权用户公开。project scope 只能在声明的 project root 内查询。session scope 不是 Native Index 的独立索引类型，若 session 需要不同 root 或策略，应通过 query binding 选择 machine/project index，而不是注册第二个同类实现。

索引器运行模型必须在 manifest 中说明：普通用户可以写入 `AIROOT\\cli\\cache\\search` 的派生数据；读取受保护 NTFS 元数据、跨用户路径或 USN Journal 需要 broker 时，indexer 只能请求受限 broker 操作，不能获得 R 区或 registry 写权限。卷卸载、journal reset、权限改变和用户注销分别进入 `DEGRADED`/`REBUILDING`，不继续使用无法证明连续性的 cursor。

同一 binding key 只能有一个 `active implementation`。crawl fallback、Everything adapter 和旧索引都是 execution mode 或候选，不得在 registry 中同时标记为 active；如果用户显式切换到 adapter，必须提交新的 binding generation，并在响应中标记 `implementation_id`、`selection_reason`、`fallback` 和旧实现。

实现切换复用 Core 事务顺序：`COMMITTED -> REGISTERED(inactive) -> ACTIVE_BOUND -> EXPOSED -> VERIFIED_AGAIN -> FINALIZED`。Search refresh、rebuild 或 fallback 失败不能直接改 active binding；只有显式 implementation switch 经过 policy/approval 和 generation CAS 才能进入 `ACTIVE_BOUND`。

## 八、CLI 设计

### 基本查询

```bash
airoot search python --json
airoot search "*.onnx" --root D:\Projects --json
airoot search ffmpeg --ext .exe --managed-only --json
```

### 索引管理

```bash
airoot search status --json
airoot search refresh
airoot search rebuild --volume D:
airoot search implementations --json
```

`status`、`refresh`、`rebuild`、`implementations` 和 `explain` 是保留子命令。若要搜索同名文件，使用 `airoot search --query status --json`；解析器不得把用户的查询误当作索引管理命令。

### 解释后端选择

```bash
airoot search explain python --json
```

返回：

```text
selected: airoot-native-search-native-index
reason: local NTFS index is healthy
freshness: 420ms
fallback: not used
```

### Agent 场景

用户说：

> “找出这个机器上可以用的 ffmpeg。”

Skill 应调用：

```text
airoot search ffmpeg --ext .exe --accessible-only --json
```

然后再对候选路径调用：

```text
airoot where ffmpeg --json
```

`search` 是通用文件定位；`where` 是 capability 解析。两者不能混成一个接口。

### 多种工具能力的统一调用

以后增加工具类型时，CLI 入口遵循同一规则：

```bash
airoot search <query> --json
airoot content search <query> --json
airoot archive list <file> --json
airoot media probe <file> --json
airoot process inspect <name> --json
```

Agent 调用的是稳定的 capability 命令。具体 `implementation_id`、可执行文件路径和参数由 AIROOT registry 决定；Install Backend 不会出现在 search 调用入口。

## 九、与其他开源工具的关系

| 项目 | 借鉴点 | AIROOT 的边界 |
|---|---|---|
| Everything | NTFS 元数据、USN 增量、常驻索引、文件名搜索 | 作为 `file_search` Extension 的机制参考，不携带软件或复制私有实现 |
| `fd` | 高效目录遍历、过滤参数、用户体验 | 作为无索引 fallback 参考，不把遍历当主路径 |
| `ripgrep` | 并行扫描、忽略规则、可预测 CLI | 适合内容搜索或 crawl fallback，不替代文件名索引 |
| Watchman | 文件变更订阅和项目级状态 | 参考 project scope 的 watch 接口 |
| `plocate/mlocate` | 预建数据库、快速查找和 stale 语义 | 参考索引新鲜度，不照搬 Unix 路径模型 |
| Windows Search | 系统索引和内容查询 | 作为后续内容搜索 adapter |
| SQLite | 一致性事务和可重建数据库 | 保存索引和游标，不让 Agent 直接编辑 |

## 十、测试和验收

### 正确性

- 初始索引覆盖所有允许的 NTFS root；
- 新建、删除、重命名、跨目录移动均能反映；
- USN journal 溢出后进入 degraded/rebuild；
- volume serial 或 journal ID 改变后不继续使用旧游标；
- hard link、隐藏文件、system 文件和 reparse point 有明确策略；
- 大小写、Unicode、长路径和保留名称结果稳定；
- 索引返回的路径经过访问权限确认；
- rebuild 不会把陌生对象自动标记为 managed。

### 性能

测试数据至少覆盖：

```text
10 万文件
100 万文件
1000 万文件
大量重复文件名
深层目录
频繁 rename/create/delete
```

比较：

```text
airoot native index
Everything adapter（如果环境有）
fd / rg --files
PowerShell Get-ChildItem -Recurse
```

指标包括：

- 初始索引耗时；
- warm query p50/p95；
- 增量更新延迟；
- 内存占用；
- 索引体积；
- journal gap 后的恢复耗时；
- fallback 触发比例。

不要提前承诺“和 Everything 一样快”。应在固定 Windows runner 上建立基线，再公布目标。

### 故障注入

- 索引进程在写入任意批次时被终止；
- SQLite 文件损坏；
- USN journal 被清除；
- volume 被卸载；
- 查询期间文件被删除或重命名；
- 权限在索引后发生变化；
- Everything Adapter 不存在或接口超时；
- fallback 扫描超时；
- 返回结果超过 limit 或 cursor 失效。

## 十一、安全和法律边界

AIROOT 应采用 clean-room 实现：

- 使用公开 Windows API 和公开文档；
- 不反编译、复制或嵌入 Everything 的私有代码；
- 不将 Everything 可执行文件、DLL 或数据库打包进 Skill/CLI；
- 如果提供 Everything Adapter，明确要求用户已经安装并授权使用其公开接口；
- 对搜索结果进行 root、ACL 和调用者权限过滤，避免把用户不可访问的路径暴露给 Agent。

搜索索引只描述“物理文件事实”，不能直接证明文件是 AIROOT managed，也不能因为搜索到 `python.exe` 就把它加入 capability registry。

## 十二、最终协议原则

1. AIROOT 管理稳定的 capability type，并对明确纳入范围的 Managed Tool/Runtime Instance 负责生命周期。
2. 同一 binding key（含 root/machine、project/session identity、platform、scope 和 capability type）只有一个 active implementation。
3. “能力工具”指 Capability Extension；“受控工具”指 Managed Tool/Runtime Instance；External Software 和 External Reference 默认不进入受控生命周期。
4. Everything 是 `file_search` Extension 的架构参考和可选 adapter，不是 AIROOT 的内置依赖。
5. Native Index 使用 Windows NTFS 元数据 + USN Journal + 常驻索引复现核心性能机制。
6. 索引是可重建的派生缓存，不替代 declared registry。
7. `search` 负责找文件，`where` 负责解析可用 capability。
8. 所有 Extension 都必须遵守通用 Capability Extension Contract，并报告身份、能力、健康度、新鲜度、权限和降级原因。
9. Managed Tool/Runtime Instance 通过独立的 Install Backend 和事务管理；Extension 不直接拥有 payload 生命周期。
10. Search、content、archive、media 等能力使用 profile 扩展，不能各自发明不兼容的调用格式。
11. implementation 切换是受控 binding transaction，fallback 不得造成两个默认能力实现并存。
12. 后端可以替换，CLI JSON envelope、错误码和取消语义不能随意变化。
13. 没有索引时必须诚实降级为 crawl，不伪装成高速查询。
14. 结果必须经过访问权限和 root 范围检查。
15. 搜索发现是 read-only discovery；只有显式安装/导入事务成功后，工具才进入 Managed Tool 生命周期。
16. 先实现 filename/path search，再考虑全文内容搜索和其他能力扩展。
