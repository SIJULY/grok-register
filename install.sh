#!/bin/bash

# Grok-Register 一键安装脚本 (适用于 Ubuntu/Debian/CentOS)
# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${GREEN}欢迎使用 Grok-Register 一键安装脚本${NC}"
echo "=========================================="

# 检查 root 权限
if [ "$EUID" -ne 0 ]; then
  echo -e "${YELLOW}建议使用 root 权限运行此脚本，否则可能会在安装依赖时失败。${NC}"
fi

INSTALL_DIR="/opt/grok-register"

show_menu() {
    echo -e "${CYAN}请选择操作:${NC}"
    echo "1. 全新安装 (安装到 $INSTALL_DIR)"
    echo "2. 更新安装 (保留配置文件，更新代码和依赖)"
    echo "3. 完全卸载 (删除 $INSTALL_DIR 及所有配置)"
    echo "0. 退出"
    echo -e "${CYAN}==========================================${NC}"
    read -p "请输入选项 [0-3]: " choice
}

install_dependencies() {
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
}

setup_python_env() {
    cd "$INSTALL_DIR" || exit 1
    
    # 配置虚拟环境和安装 Python 依赖
    echo -e "${GREEN}配置 Python 虚拟环境...${NC}"
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi
    source .venv/bin/activate

    echo -e "${GREEN}安装 Python 依赖库...${NC}"
    pip install --upgrade pip
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt
    elif command -v uv &> /dev/null; then
        uv pip install -r requirements.txt || pip install -r requirements.txt
    else
        # Fallback default
        pip install fastapi "uvicorn[standard]" requests loguru python-dotenv websockets beautifulsoup4 curl_cffi patchright
    fi

    # 检查并配置 .env
    if [ ! -f ".env" ]; then
        echo -e "${GREEN}创建并初始化 .env 配置文件...${NC}"
        cp .env.example .env
        echo -e "${YELLOW}请记得在安装完成后，编辑 ${INSTALL_DIR}/.env 文件，填入你的 YESCAPTCHA_KEY 等信息。${NC}"
    fi

    # 安装 patchright 的浏览器二进制文件 (如果需要)
    echo -e "${GREEN}检查并安装 patchright 依赖 (部分脚本可能需要)...${NC}"
    patchright install --with-deps chromium firefox 2>/dev/null || echo "跳过 patchright 安装"
}

start_service() {
    cd "$INSTALL_DIR" || exit 1
    source .venv/bin/activate
    
    # 查找并杀掉旧进程
    pkill -f "python webui.py" || true
    
    echo -e "${GREEN}正在启动 Grok-Register 后台服务...${NC}"
    nohup python webui.py > run.log 2>&1 &
    
    echo "=========================================="
    echo -e "${GREEN}安装与启动完成！${NC}"
    echo -e "日志将写入 ${INSTALL_DIR}/run.log"
    echo -e "请通过浏览器访问: ${CYAN}http://你的服务器IP:5001${NC}"
    echo "=========================================="
}

do_install() {
    install_dependencies
    
    echo -e "${GREEN}正在克隆仓库到 ${INSTALL_DIR}...${NC}"
    if [ -d "$INSTALL_DIR" ]; then
        echo -e "${YELLOW}目录 ${INSTALL_DIR} 已存在，如果想重新安装请先卸载，或者选择更新安装。${NC}"
        exit 1
    fi
    
    git clone https://github.com/SIJULY/grok-register.git "$INSTALL_DIR"
    
    setup_python_env
    start_service
}

do_update() {
    if [ ! -d "$INSTALL_DIR" ]; then
        echo -e "${RED}未找到安装目录 ${INSTALL_DIR}，请先执行全新安装。${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}正在拉取最新代码...${NC}"
    cd "$INSTALL_DIR" || exit 1
    git reset --hard
    git pull
    
    setup_python_env
    start_service
}

do_uninstall() {
    echo -e "${RED}警告: 此操作将删除 ${INSTALL_DIR} 下的所有文件，包括账号、配置、环境等！${NC}"
    read -p "确定要继续卸载吗? [y/N]: " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}正在停止服务...${NC}"
        pkill -f "python webui.py" || true
        
        echo -e "${YELLOW}正在删除目录 ${INSTALL_DIR}...${NC}"
        rm -rf "$INSTALL_DIR"
        
        echo -e "${GREEN}卸载完成。${NC}"
    else
        echo "已取消卸载。"
    fi
}

show_menu

case $choice in
    1)
        do_install
        ;;
    2)
        do_update
        ;;
    3)
        do_uninstall
        ;;
    0)
        echo "退出脚本。"
        exit 0
        ;;
    *)
        echo -e "${RED}无效选项，请重新运行脚本并输入 0-3。${NC}"
        exit 1
        ;;
esac