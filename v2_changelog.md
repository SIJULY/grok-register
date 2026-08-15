# Grok-Register v2.0.0

## 核心更新
- 完善了 WebUI 可视化面板 (`webui.py`)，提供可视化管理与配置界面。
- 修复了因为新版本 FastAPI 引起在 `TemplateResponse` 时发生的 `TypeError: cannot use 'tuple' as a dict key` (500 报错) 的问题，通过回退 `fastapi` 至 `0.107.0` 并调整代码签名解决。
- 新增 `websockets` 和 `uvicorn[standard]` 依赖并正确安装至虚拟环境 (`.venv`)，完美修复了任务日志页面的 WebSocket `404 Not Found (Unsupported upgrade request)` 问题，实现了控制台日志在 Web 页面的实时同步输出。
- 前后端对接更加稳定，自动上传功能与页面展示解耦完善，整体 WebUI 体验打通。

**本文件作为 v2.0.0 里程碑版本的本地记忆与记录点。**