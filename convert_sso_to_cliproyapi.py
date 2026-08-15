"""
把注册机产出的 SSO 转成 CLIProxyAPI/CPA 可用的 auth JSON。

输入：
  - 默认读取 keys/accounts.txt，格式：email:password:sso，并转换全部账号
  - 或通过 --sso / --email 转换单个 SSO

输出：
  - 默认写入 ./auths/xai-<email>.json
  - 可用 GROK_AUTH_DIR 或 --output-dir 指定输出目录

说明：
  SSO 本身只是 x.ai Web 登录 cookie，CLIProxyAPI 需要的是 OAuth
  access_token/refresh_token，所以必须走一次 OAuth Device Flow 授权。
"""
import argparse
import json
import os
import sys
import time
import urllib.parse

from curl_cffi import requests as cffi_requests
from curl_cffi import CurlMime

from device_mint import CLIENT_ID, PROXY, sso_to_device


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ACCOUNTS = os.path.join(SCRIPT_DIR, "keys", "accounts.txt")
DEFAULT_AUTH_DIR = os.path.abspath(os.path.expanduser(
    os.getenv("GROK_AUTH_DIR") or os.path.join(SCRIPT_DIR, "auths")
))
TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"
REDIRECT_URI = "http://127.0.0.1:56121/callback"


def safe_email_name(email: str) -> str:
    return (email or "unknown").replace("@", "_").replace(".", "_")


def load_accounts(path: str):
    accounts = []
    if not os.path.exists(path):
        return accounts
    with open(path, "r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) < 3:
                print(f"[WARN] 跳过第 {line_no} 行：不是 email:password:sso 格式")
                continue
            accounts.append({
                "email": parts[0].strip(),
                "password": parts[1],
                # JWT 内通常不含冒号；这里仍兼容后续字段里带冒号的情况。
                "sso": ":".join(parts[2:]).strip(),
            })
    return accounts


def archive_account(email: str, accounts_path: str):
    """转换成功后，将账号从 accounts.txt 移动到 accounts_done.txt 中存档"""
    if not os.path.exists(accounts_path):
        return
    archived_path = os.path.join(os.path.dirname(accounts_path), "accounts_done.txt")
    
    try:
        with open(accounts_path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
            
        new_lines = []
        archived_line = None
        for line in lines:
            if line.strip() and not line.startswith("#"):
                parts = line.split(":")
                if parts[0].strip() == email:
                    archived_line = line
                    continue
            new_lines.append(line)
            
        if archived_line:
            with open(accounts_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            with open(archived_path, "a", encoding="utf-8") as f:
                f.write(archived_line)
            print(f"[{email}] 已从待转换列表移除，并存档至: {archived_path}")
    except Exception as e:
        print(f"[WARN] {email} 账号归档失败: {e}")

def auth_path(output_dir: str, email: str) -> str:
    return os.path.join(output_dir, f"xai-{safe_email_name(email)}.json")


def save_cliproyapi_auth(email: str, token_data: dict, output_dir: str, mint_method: str = "device_code") -> str:
    os.makedirs(output_dir, exist_ok=True)
    now = time.time()
    expires_in = int(token_data.get("expires_in") or 21600)
    record = {
        "type": "xai",
        "auth_kind": "oauth",
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", ""),
        "token_type": token_data.get("token_type", "Bearer"),
        "expires_in": expires_in,
        "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + expires_in)),
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "email": email,
        "base_url": "https://cli-chat-proxy.grok.com/v1",
        "token_endpoint": TOKEN_ENDPOINT,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "disabled": False,
        "mint_method": mint_method,
        "protocol_flow": "device_code",
        "headers": {
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-grok-client-version": "0.2.93",
            "x-grok-client-identifier": "grok-shell",
        },
    }
    if token_data.get("id_token"):
        record["id_token"] = token_data["id_token"]

    path = auth_path(output_dir, email)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return path, record


def _normalize_cpa_auth_files_url(api_url: str) -> str:
    normalized = (api_url or "").strip().rstrip("/")
    lower_url = normalized.lower()
    if not normalized:
        return ""
    if lower_url.endswith("/auth-files"):
        return normalized
    if lower_url.endswith("/v0/management") or lower_url.endswith("/management"):
        return f"{normalized}/auth-files"
    if lower_url.endswith("/v0"):
        return f"{normalized}/management/auth-files"
    return f"{normalized}/v0/management/auth-files"


def upload_to_cpa(record: dict) -> bool:
    cpa_enabled = os.getenv("GROK_AUTO_UPLOAD_CPA", "").strip().lower() in ("1", "true", "yes", "on")
    if not cpa_enabled:
        return True

    api_url = os.getenv("GROK_CPA_API_URL", "").strip()
    api_token = os.getenv("GROK_CPA_API_TOKEN", "").strip()

    if not api_url or not api_token:
        print(f"[CPA] 未配置 GROK_CPA_API_URL 或 GROK_CPA_API_TOKEN，跳过上传")
        return False

    upload_url = _normalize_cpa_auth_files_url(api_url)
    filename = f"xai-{safe_email_name(record['email'])}.json"
    file_content = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")

    try:
        import urllib.request
        import urllib.error
        
        print(f"[CPA] 正在上传 {record['email']} 到 CPA: {upload_url}")
        
        # 使用 raw JSON 后端上传，最稳妥不丢包不被防火墙拦截，绕过 cffi 的奇怪代理 bug
        raw_url = f"{upload_url}?name={urllib.parse.quote(filename)}"
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
        
        req = urllib.request.Request(raw_url, data=file_content, headers=headers, method="POST")
        
        # 强制构建一个无代理的环境执行请求，确保解析本地直接打到真实 IP
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        
        with opener.open(req, timeout=30) as response:
            status_code = response.getcode()
            if status_code in (200, 201):
                print(f"[CPA] {record['email']} 上传成功 (状态码: {status_code})")
                return True
            else:
                body = response.read().decode('utf-8', errors='ignore')
                print(f"[CPA] 上传失败 HTTP {status_code}: {body[:200]}")
                return False
                
    except urllib.error.HTTPError as e:
        status_code = e.code
        body = e.read().decode('utf-8', errors='ignore')
        print(f"[CPA] 上传失败 HTTP {status_code}: {body[:200]}")
        return False
    except Exception as e:
        print(f"[CPA] 上传异常: {e}")
        return False


def convert_one(email: str, sso: str, output_dir: str, force: bool = False, manual: bool = False, wait_seconds=None, interactive: bool = False) -> bool:
    path = auth_path(output_dir, email)
    if os.path.exists(path) and not force:
        print(f"[{email}] SKIP：已存在 {path}，如需覆盖加 --force")
        return True

    print(f"\n[{email}] 开始 SSO → CLIProxyAPI OAuth auth JSON")
    print(f"[{email}] 代理：{PROXY or 'DIRECT'}")
    token_data = sso_to_device(sso, email, manual=manual, wait_seconds=wait_seconds, interactive=interactive)
    if not token_data or not token_data.get("access_token"):
        print(f"[{email}] FAIL：未拿到 access_token")
        return False

    saved_path, record = save_cliproyapi_auth(email, token_data, output_dir)
    print(f"[{email}] OK：已保存 {saved_path}")

    if upload_to_cpa(record):
        record["cpa_uploaded"] = True
        with open(saved_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    return True


def main():
    parser = argparse.ArgumentParser(description="SSO -> CLIProxyAPI/CPA auth JSON")
    parser.add_argument("--accounts", default=DEFAULT_ACCOUNTS, help="accounts.txt 路径，默认 keys/accounts.txt")
    parser.add_argument("--output-dir", default=DEFAULT_AUTH_DIR, help="输出 auth JSON 目录，默认 ./auths 或 GROK_AUTH_DIR")
    parser.add_argument("--sso", help="单个 SSO token")
    parser.add_argument("--email", help="单个 SSO 对应邮箱")
    parser.add_argument("--all", action="store_true", help="转换 accounts.txt 中所有账号（默认行为，可不填）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 auth JSON")
    parser.add_argument("--dry-run", action="store_true", help="只列出待转换账号，不打开浏览器授权")
    parser.add_argument("--manual", action="store_true", help="手动打开 Verify URL 授权，脚本只负责轮询并保存 token")
    parser.add_argument("--interactive", action="store_true", help="自动打开可见浏览器并注入 SSO Cookie，用户手动点击授权（默认行为，可不填）")
    parser.add_argument("--headless-auto", action="store_true", help="使用无头浏览器自动点击授权；不加此参数时默认弹出可见浏览器")
    parser.add_argument("--wait-seconds", type=int, help="Device Flow 轮询等待秒数；manual 默认 300 秒")
    args = parser.parse_args()

    if args.sso:
        email = args.email or "unknown"
        items = [{"email": email, "sso": args.sso}]
    else:
        items = load_accounts(args.accounts)
        if not args.all and len(items) > 1:
            print("检测到多个账号，未指定 --sso/--email，默认转换全部账号。")

    if not items:
        print(f"没有找到待转换 SSO：{args.accounts}")
        return 1

    interactive = args.interactive or not args.headless_auto
    if interactive and not args.manual:
        print("默认使用可见浏览器交互授权。浏览器弹出后请手动点击 Continue/Allow。")

    pending = [x for x in items if args.force or not os.path.exists(auth_path(args.output_dir, x["email"]))]
    print(f"总数: {len(items)}，待转换: {len(pending)}，输出目录: {args.output_dir}")
    if args.dry_run:
        for item in pending:
            print(f"  [DRY RUN] {item['email']} SSO={item['sso'][:30]}...")
        return 0

    ok = 0
    for item in pending:
        if convert_one(item["email"], item["sso"], args.output_dir, force=args.force, manual=args.manual, wait_seconds=args.wait_seconds, interactive=interactive):
            ok += 1
            archive_account(item["email"], args.accounts)
        time.sleep(2)
    print(f"\n完成: {ok}/{len(pending)} 成功")
    return 0 if ok == len(pending) else 1


if __name__ == "__main__":
    raise SystemExit(main())