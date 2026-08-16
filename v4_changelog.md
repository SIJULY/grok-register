# V4 版本更新日志

**更新时间:** 2026-08-16

**核心功能:**
修复多账号并发注册后的 SSO 授权转 Token 时遭遇 Cloudflare 盾 (Click to reveal) 拦截的问题，实现了真正的隐形后台自动授权。

**主要改动:**
1. **多域名 Cookie 注入 (`device_mint.py`)**: 恢复标准的跨域 Cookie 注入 `.x.ai`，并设置合理的 `sameSite` 策略。
2. **拟真鼠标轨迹绕过 (Human Mouse Movement)**: 在自动点击 "Continue" 或 "Allow" 以及 CF 质询的 "Click to reveal" 时，不再调用粗暴的 `.click()`，而是通过计算控件坐标，模拟随机抖动和变速的鼠标移动轨迹、按下松开的延迟，成功欺骗 Cloudflare 的人机验证。
3. **完美无头模式 (Perfect Headless)**:
   - 彻底摒弃 Playwright 传统无头模式。
   - 使用 Chrome 最新无头引擎 `--headless=new`。
   - 注入真实浏览器 UA，移除 `HeadlessChrome` 标识。
   - 注入 Stealth 初始化 JS，抹除 `navigator.webdriver` 等明显的机器自动化特征。

**如何恢复至此版本:**
可以通过 Git Tag 进行恢复：
```bash
git checkout v4