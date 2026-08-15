import os
import uvicorn
from web.app import app

if __name__ == "__main__":
    host = os.getenv("WEBUI_HOST", "0.0.0.0")
    port = int(os.getenv("WEBUI_PORT", "5001"))  # 端口冲突，改用 5001
    print(f"[*] 正在启动 Grok-Register WebUI，访问地址: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
