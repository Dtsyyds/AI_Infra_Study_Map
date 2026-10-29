# 模型接口核对

2026-10-24核对官方 [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)。Responses采用 `text.format` JSON Schema，strict模式仍需处理拒绝与incomplete；本地严格校验也不会省略。

保留锁定httpx 0.28.1，直接调用REST；未安装/升级OpenAI SDK，未改变MCP版本。默认不开启、未选择模型，实际模型兼容性/账户可用性/费用 UNKNOWN；必须由授权配置指定支持该schema的模型再做真实smoke。reference/create页面抓取因过大失败，采用已打开的官方Structured Outputs正文，不把搜索片段当已跑接口证据。

模型仅产出exact replacement patch，外层流程仍由确定性节点控制。无自动重试，失败不回退Fake。平台在请求前持久预留全局/单Task请求数和保守token额度（输入UTF-8字节数+固定余量+最大输出token），未知账单不退款；不承诺exactly-once计费。provider响应model/request_id/usage原样单独记录，缺测为null。真实路径未获授权/预算时BLOCKED。

2026-10-26复核：当前Patch Schema的object均完整required且additionalProperties=false；本地检查及单个dev实验草案由`examples.model_smoke_plan`生成。具体模型仍未选择，temperature=0及Schema关键字的模型兼容性不能仅凭通用API文档宣布通过。3请求/100000本地预留单位只是待批准草案，不是生效预算或费用上限；当前真实调用0。细节见[三项研究协议](three_track_research.md)。
