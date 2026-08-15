import os
import signal
import asyncio
import subprocess
import sys
from pathlib import Path
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from pydantic import BaseModel
from typing import List
import json

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))
from convert_sso_to_cliproyapi import upload_to_cpa
ENV_PATH = BASE_DIR / ".env"

app = FastAPI(title="Grok-Register WebUI")

# 设置静态文件和模板
static_dir = BASE_DIR / "web" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "web" / "templates")

def get_env_config():
    config = {}
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    config[k.strip()] = v.strip().strip("'\"")
    return config

def save_env_config(config: dict):
    # 保留原有注释和顺序，更新现有的键，追加新的键
    lines = []
    keys_found = set()
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in config:
                    f.write(f"{k}={config[k]}\n")
                    keys_found.add(k)
                else:
                    f.write(line)
            else:
                f.write(line)
                
        # 追加没找到的键
        for k, v in config.items():
            if k not in keys_found:
                f.write(f"{k}={v}\n")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    auths_dir = BASE_DIR / "auths"
    unuploaded_count = 0
    uploaded_count = 0
    all_count = 0
    
    if auths_dir.exists():
        for f in auths_dir.glob("*.json"):
            all_count += 1
            try:
                with open(f, "r", encoding="utf-8") as file_obj:
                    data = json.load(file_obj)
                    if data.get("cpa_uploaded"):
                        uploaded_count += 1
                    else:
                        unuploaded_count += 1
            except Exception:
                unuploaded_count += 1
                
    config = get_env_config()
    auto_upload_cpa = str(config.get("GROK_AUTO_UPLOAD_CPA", "1")).lower()
        
    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "unuploaded_count": unuploaded_count,
        "uploaded_count": uploaded_count,
        "all_count": all_count,
        "auto_upload_cpa": auto_upload_cpa
    })

@app.get("/api/stats")
async def get_stats():
    auths_dir = BASE_DIR / "auths"
    unuploaded_count = 0
    uploaded_count = 0
    all_count = 0
    
    if auths_dir.exists():
        for f in auths_dir.glob("*.json"):
            all_count += 1
            try:
                with open(f, "r", encoding="utf-8") as file_obj:
                    data = json.load(file_obj)
                    if data.get("cpa_uploaded"):
                        uploaded_count += 1
                    else:
                        unuploaded_count += 1
            except Exception:
                unuploaded_count += 1
                
    return {
        "unuploaded_count": unuploaded_count,
        "uploaded_count": uploaded_count,
        "all_count": all_count
    }

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    config = get_env_config()
    return templates.TemplateResponse(request, "settings.html", {"request": request, "config": config})

@app.get("/auths", response_class=HTMLResponse)
async def auths_page(request: Request):
    return templates.TemplateResponse(request, "auths.html", {"request": request})

@app.get("/api/auths")
async def get_auths_files():
    auths_dir = BASE_DIR / "auths"
    files_list = []
    if auths_dir.exists():
        for f in auths_dir.glob("*.json"):
            size_kb = round(f.stat().st_size / 1024, 2)
            cpa_uploaded = False
            try:
                with open(f, "r", encoding="utf-8") as file_obj:
                    data = json.load(file_obj)
                    cpa_uploaded = data.get("cpa_uploaded", False)
            except Exception:
                pass
            files_list.append({
                "name": f.name,
                "size": f"{size_kb} KB",
                "cpa_uploaded": cpa_uploaded
            })
    return {"files": files_list}

@app.get("/api/auths/{filename}")
async def get_auth_file(filename: str):
    file_path = BASE_DIR / "auths" / filename
    if not file_path.exists() or not filename.endswith(".json"):
        return {"error": "File not found"}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/accounts/pending")
async def get_pending_accounts():
    accounts_file = BASE_DIR / "keys" / "accounts.txt"
    accounts = []
    if accounts_file.exists():
        with open(accounts_file, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) >= 3:
                    accounts.append({
                        "email": parts[0].strip(),
                        "password": parts[1].strip(),
                        "sso_preview": parts[2].strip()[:20] + "..."
                    })
    return {"accounts": accounts}

class ConvertSingleRequest(BaseModel):
    email: str

@app.post("/api/accounts/convert_single")
async def convert_single_account(req: ConvertSingleRequest):
    accounts_file = BASE_DIR / "keys" / "accounts.txt"
    target_sso = None
    if accounts_file.exists():
        with open(accounts_file, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split(":")
                    if parts[0].strip() == req.email and len(parts) >= 3:
                        target_sso = ":".join(parts[2:]).strip()
                        break
                        
    if not target_sso:
        return {"status": "error", "error": "未找到对应的 SSO"}

    cmd = [
        "python", str(BASE_DIR / "convert_sso_to_cliproyapi.py"),
        "--email", req.email,
        "--sso", target_sso,
        "--headless-auto"
    ]
    
    env = os.environ.copy()
    env["GROK_AUTO_UPLOAD_CPA"] = "0"  # 页面单转时不自动上传，交由未上传标签页处理

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(BASE_DIR),
        env=env
    )
    stdout, _ = await process.communicate()
    output = stdout.decode("utf-8", errors="replace")
    
    if process.returncode == 0:
        return {"status": "success", "output": output}
    else:
        return {"status": "error", "error": output}

class AuthActionRequest(BaseModel):
    filenames: List[str]

@app.post("/api/auths/delete")
async def delete_auths_files(request: AuthActionRequest):
    deleted = 0
    for filename in request.filenames:
        file_path = BASE_DIR / "auths" / filename
        if file_path.exists() and filename.endswith(".json"):
            file_path.unlink()
            deleted += 1
    return {"status": "success", "deleted": deleted}

@app.post("/api/auths/upload")
async def upload_auths_files(request: AuthActionRequest):
    # Temporarily force CPA enabled for this manual upload
    os.environ["GROK_AUTO_UPLOAD_CPA"] = "1"
    results = []
    for filename in request.filenames:
        file_path = BASE_DIR / "auths" / filename
        if file_path.exists() and filename.endswith(".json"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    record = json.load(f)
                success = upload_to_cpa(record)
                
                # 如果是 web 端批量强制上传且成功了，但如果 convert_sso_to_cliproyapi 中上传逻辑
                # 因为没找到原始路径写入失败，我们在 web 端兜底写入一次
                if success:
                    record["cpa_uploaded"] = True
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(record, f, ensure_ascii=False, indent=2)
                        
                results.append({"name": filename, "success": success})
            except Exception as e:
                results.append({"name": filename, "success": False, "error": str(e)})
    return {"status": "success", "results": results}

@app.post("/settings")
async def save_settings(request: Request):
    form_data = await request.form()
    config = dict(form_data)
    save_env_config(config)
    return RedirectResponse(url="/settings?success=1", status_code=303)

@app.post("/api/settings/update_single")
async def update_single_setting(request: Request):
    """供前端 AJAX 局部更新某个环境配置项"""
    try:
        data = await request.json()
        save_env_config(data)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# 全局任务管理，解耦前端 WebSocket 与后台进程
class TaskManager:
    def __init__(self):
        self.process = None
        self.log_history = []
        self.clients = set()
        self.read_task = None
        
    async def broadcast(self, message: str):
        self.log_history.append(message)
        # 限制历史记录，防止内存溢出 (约保留最新的 2000 行)
        if len(self.log_history) > 2000:
            self.log_history.pop(0)
            
        dead_clients = set()
        for client in self.clients:
            try:
                await client.send_text(message)
            except Exception:
                dead_clients.add(client)
        for client in dead_clients:
            self.clients.remove(client)
            
    async def process_reader(self):
        try:
            while True:
                if not self.process or not self.process.stdout:
                    break
                line = await self.process.stdout.readline()
                if not line:
                    break
                await self.broadcast(line.decode("utf-8", errors="replace"))
        except Exception as e:
            await self.broadcast(f"\n[系统] 读取日志时出错: {e}\n")
        finally:
            if self.process:
                await self.process.wait()
            self.process = None
            await self.broadcast("\n[系统] 任务已结束。\n")
            await self.broadcast("STATUS:STOPPED\n")

task_manager = TaskManager()

@app.websocket("/ws/task")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    task_manager.clients.add(websocket)
    
    # 推送历史日志
    if task_manager.log_history:
        try:
            await websocket.send_text("".join(task_manager.log_history))
        except Exception:
            pass

    # 推送当前状态
    is_running = task_manager.process is not None and task_manager.process.returncode is None
    try:
        await websocket.send_text("STATUS:RUNNING\n" if is_running else "STATUS:STOPPED\n")
    except Exception:
        pass
        
    try:
        while True:
            data = await websocket.receive_text()
            if data == "STOP":
                if task_manager.process and task_manager.process.returncode is None:
                    # 使用 .stop_flag 优雅停止
                    flag_path = BASE_DIR / ".stop_flag"
                    try:
                        with open(flag_path, "w") as f:
                            f.write("STOP")
                        await task_manager.broadcast("\n[系统] 已发送停止指令！等待当前账号处理完毕后安全退出...\n")
                    except Exception as e:
                        await task_manager.broadcast(f"\n[系统] 发送停止指令失败: {e}\n")
                continue
                
            if data.startswith("START_GROK"):
                if task_manager.process and task_manager.process.returncode is None:
                    await websocket.send_text("\n[系统] 任务已经在运行中，请勿重复启动。\n")
                    continue
                    
                parts = data.split(":")
                auto_upload = parts[1] if len(parts) > 1 else "1"
                
                # 启动前清理旧的 flag
                flag_path = BASE_DIR / ".stop_flag"
                if flag_path.exists():
                    try:
                        flag_path.unlink()
                    except:
                        pass
                
                env = os.environ.copy()
                env["GROK_AUTO_UPLOAD_CPA"] = "1" if auto_upload == "1" else "0"
                
                task_manager.log_history.clear()
                
                cmd = ["python", str(BASE_DIR / "grok.py"), "--threads", "1"]
                task_manager.process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(BASE_DIR),
                    env=env,
                    preexec_fn=os.setsid
                )
                
                if task_manager.read_task:
                    task_manager.read_task.cancel()
                task_manager.read_task = asyncio.create_task(task_manager.process_reader())
                
                await task_manager.broadcast("STATUS:RUNNING\n")
                await task_manager.broadcast(f"[系统] 正在启动任务: grok.py... (自动上传CPA: {'开启' if auto_upload=='1' else '关闭'})\n\n")
                
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in task_manager.clients:
            task_manager.clients.remove(websocket)
        # 注意这里不再杀进程或写 .stop_flag
