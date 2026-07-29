# Yuxi 业务智能体 Java 接入 Demo

该 Demo 使用 JDK 17 `HttpClient` 和 Jackson，演示外部 Java 服务调用已经在
Yuxi 中配置好的建筑施工过程规则校验 Agent。

本版本专门用于直接运行和断点调试：

- 不读取环境变量；
- 不读取命令行参数；
- 所有参数都直接写在 `main` 方法中；
- `main` 调用普通 Java 方法 `validateConstructionRules(...)`；
- 默认打印请求地址、请求体、HTTP 状态、响应体、`run_id` 和轮询状态；
- 不打印 API Key；
- 标准输出仍只打印最终业务 JSON，调试日志写入标准错误。

该规则校验场景不接收附件和图片。

## 一、填写调用参数

打开：

```text
src/main/java/demo/YuxiBusinessAgentDemo.java
```

在 `main` 方法顶部填写：

```java
String baseUrl = "http://127.0.0.1:5050";
String apiKey = "yxkey_替换为真实APIKey";
String agentSlug = "construction-process-rule-validator";
String requestId = "construction-validation-" + UUID.randomUUID();
```

注意：

- `baseUrl` 不包含末尾的 `/api`；
- `apiKey` 必须是能访问目标 Agent 的完整 Key；
- `agentSlug` 必须与 Yuxi 管理端中的智能体标识完全一致；
- 不要把填写了真实 API Key 的 Demo 提交到 Git。

随后直接编辑 `activeRulesJson` 和 `sceneDataJson` 两个 Java 文本块。
前者必须是 JSON 数组，后者必须是 JSON 对象。Demo 会在发送请求前先解析这两段
JSON，格式错误会立即打印异常，不会发送 HTTP 请求。

## 二、普通函数调用

核心调用方式如下：

```java
YuxiBusinessAgentDemo demo =
        new YuxiBusinessAgentDemo(baseUrl, apiKey, true);

JsonNode validationResult = demo.validateConstructionRules(
        agentSlug,
        requestId,
        activeRulesJson,
        sceneDataJson,
        Duration.ofMinutes(10),
        Duration.ofSeconds(2)
);
```

参数含义：

| 参数 | 说明 |
| --- | --- |
| `agentSlug` | Yuxi 中已经配置好的施工规则校验 Agent |
| `requestId` | 本次调用的幂等 ID，网络重试时复用原值 |
| `activeRulesJson` | 当前激活规则 JSON 数组 |
| `sceneDataJson` | 当前施工场景 JSON 对象 |
| `waitTimeout` | 最长等待时间 |
| `pollInterval` | 查询运行状态的间隔 |

该方法对调用方表现为普通阻塞方法，返回值是已经解析好的最终业务 JSON。方法内部
使用 `async_mode=true` 创建 Yuxi run，再根据 `run_id` 轮询结果，避免同步 HTTP
请求长时间无任何反馈。

## 三、运行

在 Demo 目录执行：

```powershell
mvn -q clean compile exec:java
```

也可以直接在 IntelliJ IDEA 中运行 `main` 方法并设置断点。

如果 Maven 报：

```text
不支持发行版本 17
```

说明 Maven 使用的不是 JDK 17。确认 IDE 的 Project SDK、Maven Runner JRE 或
`JAVA_HOME` 指向 JDK 17。

## 四、调试输出

`debugEnabled=true` 时可以看到完整调用过程：

```text
[YUXI-DEBUG] 准备调用 Yuxi
[YUXI-DEBUG] baseUrl=http://127.0.0.1:5050
[YUXI-DEBUG] agentSlug=construction-process-rule-validator
[YUXI-DEBUG] requestId=construction-validation-...
[YUXI-DEBUG] HTTP -> POST http://127.0.0.1:5050/api/agent-invocation/agent-call/runs
[YUXI-DEBUG] HTTP -> requestBody={...}
[YUXI-DEBUG] HTTP <- status=200, elapsed=45ms
[YUXI-DEBUG] HTTP <- responseBody={"run_id":"...","status":"pending",...}
[YUXI-DEBUG] run 创建成功：runId=..., status=pending
[YUXI-DEBUG] 轮询结果：runId=..., status=running, elapsed=2s
```

如果连接失败、HTTP 返回 `4xx/5xx`、响应体不是 JSON、Agent 输出不是 JSON，程序
会打印错误摘要和完整 Java 堆栈。

调试日志全部写到 `System.err`。只有最终规则校验 JSON 写到 `System.out`，因此
后续把该方法移入业务服务时仍可单独处理最终结果。确认接入稳定后，可把构造方法
中的第三个参数改为 `false` 关闭调试日志。

## 五、一直停在 pending

如果日志已经出现：

```text
[YUXI-DEBUG] run 创建成功：runId=..., status=pending
```

说明 Java 请求已经成功到达 Yuxi，API Key、URL 和请求体已经通过创建接口。若后续
长期保持 `pending`，问题通常在 Yuxi Worker，而不是 Java 客户端。

本地拆分部署时，在原 Worker 终端按 `Ctrl+C`，然后从仓库根目录重新启动：

```powershell
.\scripts\split-deploy\Start-HostService.ps1 -Service Worker
```

启动后确认存在实际的：

```text
arq server.worker_main.WorkerSettings
```

并观察 run 是否从 `pending` 进入 `running`。不要同时启动生产 Docker Compose
中的同名 Worker。

## 六、结果要求

Demo 会检查：

- Yuxi run 状态必须是 `completed`；
- `output` 不能为空；
- `output` 必须是合法 JSON 对象；
- JSON 必须包含文本类型的 `validation_status`；
- JSON 必须包含对象类型的 `summary`；
- JSON 必须包含数组类型的 `results`。

目标 Agent 仍需在系统提示词中要求只输出 JSON，不添加 Markdown 代码围栏、标题、
解释或寒暄。
