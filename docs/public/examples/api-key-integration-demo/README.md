# Yuxi 建筑施工规则校验 API Key Demo

该 Demo 使用 Python 标准库调用 Yuxi 的建筑施工过程规则校验 Agent，不需要安装第三方依赖。

## 配置

Bash：

```bash
export YUXI_BASE_URL="https://yuxi.example.com"
export YUXI_API_KEY="yxkey_替换为完整密钥"
export YUXI_AGENT_SLUG="construction-process-rule-validator"
```

PowerShell：

```powershell
$env:YUXI_BASE_URL = "https://yuxi.example.com"
$env:YUXI_API_KEY = "yxkey_替换为完整密钥"
$env:YUXI_AGENT_SLUG = "construction-process-rule-validator"
```

`YUXI_BASE_URL` 是实例根地址，不要包含末尾的 `/api`。

## 运行

从仓库根目录执行同步调用：

```bash
python docs/public/examples/api-key-integration-demo/yuxi_agent_demo.py \
  "【当前激活规则 active_rules】[{\"rule_id\":\"CONCRETE-POUR-001\",\"condition\":\"钢筋隐蔽工程验收状态必须为passed\"}]【当前施工场景数据 scene_data】{\"work_type\":\"混凝土浇筑\",\"building_no\":\"1号楼\",\"floor\":\"8层\",\"rebar_inspection_status\":\"pending\"}"
```

查询可见 Agent：

```bash
python docs/public/examples/api-key-integration-demo/yuxi_agent_demo.py --list-agents
```

异步调用并轮询结果：

```bash
python docs/public/examples/api-key-integration-demo/yuxi_agent_demo.py \
  --async-mode \
  "【当前激活规则 active_rules】[{\"rule_id\":\"SAFETY-EDGE-001\",\"condition\":\"scene_data.edge_protection_status == 'complete'\"}]【当前施工场景数据 scene_data】{\"building_no\":\"1号楼\",\"floor\":\"8层\",\"edge_protection_status\":\"missing\"}"
```

查看全部参数：

```bash
python docs/public/examples/api-key-integration-demo/yuxi_agent_demo.py --help
```

每次执行都是独立的规则校验，不复用 `thread_id`。Demo 只把响应 `output` 解析为 JSON 对象并打印，标准输出中没有 API 包装或其他说明。

生产环境建议使用 `--async-mode`，并由调用方保存 `request_id`、`run_id` 和最终 `result_json`。不要记录或提交真实 API Key。
