# Grok-Register v1.0.0

## 核心更新
- 修复了 CPA 自动上传在使用本地代理时的 DNS 解析失败问题 (curl: (6) Could not resolve host)。
- 将上传核心由 `curl_cffi` 替换为纯 Python 内置网络库 (`urllib.request`)，内置隔离代理处理器 (ProxyHandler({})) 强制实现直连。
- 添加了环境变量模板：`GROK_AUTO_UPLOAD_CPA`, `GROK_CPA_API_URL`, `GROK_CPA_API_TOKEN`，全流程支持跑通并直达 CPA 面板。

**本文件作为 v1.0.0 里程碑版本的本地记忆与记录点。**
