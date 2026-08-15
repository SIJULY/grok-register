#!/bin/bash

# Grok-Register 一键安装脚本 (适用于 Ubuntu/Debian/CentOS)
# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}欢迎使用 Grok-Register 一键安装脚本${NC}"
echo "=========================================="

# 检查 root 权限
if [ "$EUID" -ne 0 ]; then
  echo -e "${YELLOW}建议使用 root 权限运行此脚本，否则可能会在安装依赖时失败。${NC}"
fi

# 检查系统类型并安装依赖
if [ -f /etc/debian_version ]; then
    echo -e "${GREEN}检测到 Debian/Ubuntu 系统，正在安装系统依赖...${NC}"
    apt-get update
    apt-get install -y python3 python3-venv python3-pip git curl xvfb libasound2 libatk-bridge2.0-0 libgtk-3-0 libnss3 libx11-xcb1
elif [ -f /etc/redhat-release ]; then
    echo -e "${GREEN}检测到 CentOS/RHEL 系统，正在安装系统依赖...${NC}"
    yum update -y
    yum install -y python3 python3-pip git curl xorg-x11-server-Xvfb alsa-lib atk gtk3 nss libX11-xcb
else
    echo -e "${YELLOW}未知的 Linux 发行版，跳过系统依赖自动安装。请自行确认已安装 python3, git, curl 等。${NC}"
fi

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}未找到 Python3，请手动安装后重试。${NC}"
    exit 1
fi

# 获取当前路径
INSTALL_DIR=$(pwd)/grok-register
echo -e "${GREEN}即将安装到: ${INSTALL_DIR}${NC}"

# 克隆仓库
if [ -d "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}目录 ${INSTALL_DIR} 已存在，正在拉取最新代码...${NC}"
    cd "$INSTALL_DIR"
    git pull
else
    echo -e "${GREEN}正在克隆仓库...${NC}"
    git clone https://github.com/SIJULY/grok-register.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 配置虚拟环境和安装 Python 依赖
echo -e "${GREEN}配置 Python 虚拟环境...${NC}"
python3 -m venv .venv
source .venv/bin/activate

echo -e "${GREEN}安装 Python 依赖库...${NC}"
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
elif command -v uv &> /dev/null; then
    uv pip install -r requirements.txt || pip install -r requirements.txt
else
    # Fallback default
    pip install fastapi uvicorn requests bs4 selenium loguru python-dotenv websockets
fi

# 检查并配置 .env
if [ ! -f ".env" ]; then
    echo -e "${GREEN}创建并初始化 .env 配置文件...${NC}"
    cp .env.example .env
    echo -e "${YELLOW}请在安装完成后，编辑 ${INSTALL_DIR}/.env 文件，填入你的 YESCAPTCHA_KEY 及邮箱 API 信息。${NC}"
fi

# 安装 Playwright 的浏览器二进制文件 (如果需要)
echo -e "${GREEN}检查并安装 Playwright 依赖 (部分脚本可能需要)...${NC}"
playwright install --with-deps chromium firefox 2>/dev/null || echo "跳过 playwright 安装"

# 尝试启动测试
echo "=========================================="
echo -e "${GREEN}安装完成！${NC}"
echo -e "启动命令："
echo -e "  cd ${INSTALL_DIR}"
echo -e "  source .venv/bin/activate"
echo -e "  python webui.py"
echo "=========================================="