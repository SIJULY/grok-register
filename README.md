# Grok-Register

Grok-Register 是一个自动化工具，旨在管理和监控与特定服务交互的任务与日志。本项目提供了简单的配置支持、任务实时监控、以及完善的 WebUI 界面供用户操作。

## 核心特性
- **自动注册**: 自动化设备注册与轮换。
- **Web 可视化面板**: 支持可视化的后台管理配置和任务实时日志流式推送展示（WebSocket）。
- **多种验证码与代理支持**: 支持 YesCaptcha 以及灵活的自定义代理 (clash) 配置。
- **多邮箱提供商支持**: 支持 LuckMail, MailNest, GPTMail。

## 一键安装部署（推荐，适用于 Linux/VPS）

针对 Ubuntu/Debian 或 CentOS 系统的 VPS 环境，我们提供了一键安装脚本：

```bash
# 下载一键安装脚本
curl -O https://raw.githubusercontent.com/SIJULY/grok-register/main/install.sh

# 赋予执行权限
chmod +x install.sh

# 执行安装
./install.sh
```

> **注意：** 建议使用 `root` 用户运行以确保能够正常安装依赖的系统环境。

安装成功后，请根据提示，在项目目录下的 `.env` 文件中配置你的相关密钥。

### 启动项目

你可以选择前台运行或后台运行：

**方式一：前台运行（适合测试，关闭 SSH 终端后会退出）**
```bash
cd grok-register
source .venv/bin/activate
python webui.py
```

**方式二：后台运行（推荐，关闭 SSH 终端后程序继续运行）**
```bash
cd grok-register
source .venv/bin/activate
nohup python webui.py > run.log 2>&1 &
```

启动后，默认将在服务器的 `5001` 端口运行。你可以在浏览器中访问 `http://<服务器IP>:5001`。
如果你需要停止后台运行的程序，可以使用以下命令：
```bash
pkill -f "python webui.py"
```

## 手动安装说明

如果你希望手动安装或在非 Linux 环境部署，请参考以下步骤：

1. **克隆项目**
   ```bash
   git clone https://github.com/SIJULY/grok-register.git
   cd grok-register
   ```

2. **配置 Python 环境** (推荐使用 python 3.9+)
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

4. **配置环境变量**
   ```bash
   cp .env.example .env
   # 然后使用你熟悉的编辑器编辑 .env 文件，填写必填的 YESCAPTCHA_KEY, 邮箱 API KEY，及按需开启的代理配置。
   ```

5. **启动项目**
   同样地，手动安装后你也可以选择使用 `nohup` 来保持其在后台运行：
   ```bash
   nohup python webui.py > run.log 2>&1 &
   ```
   默认将在本地监听 `http://127.0.0.1:5001`，可以通过浏览器访问。

## 注意事项与配置说明

- `.env` 文件不会被提交到远程仓库，它包含你的私密信息（如 YesCaptcha 密钥、邮箱提供商的 API 密钥）。不要向任何人泄露。
- 当开启代理时，如果在控制台出现 `Could not resolve host` 等网络报错，请检查配置文件中指定的 `GROK_PROXY` 是否正确且代理软件在对应端口提供服务。
- 自动化产生的用户配置文件 (`auths`)、调试页面信息 (`debug_device`) 与相关缓存将保留在项目目录下并在 `.gitignore` 规则中进行屏蔽。

## 更新日志

详情可参考 [v1_changelog.md](v1_changelog.md) 和 [v2_changelog.md](v2_changelog.md)。

## 许可协议

This project is licensed under the MIT License - see the LICENSE file for details.