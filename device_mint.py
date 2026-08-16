"""
SSO → OAuth Device Flow 铸造模块 (2026-08-06)
替换被 CF 拦截的 PKCE 方案 (sso_to_cpa.py)。

原理: x.ai OAuth Device Authorization Grant
- 关键: scope 必须为 7 个, 不能加 conversations/workspaces (该 client 未授权, 加了 Access denied)
- 授权: 有头 Chrome 注入 SSO cookie → 打开授权页自动点"继续/允许" → 轮询 token

接口: sso_to_device(sso, email) -> cpa_data (与 sso_to_cpa.py 返回结构一致)
"""
import json, sys, time, base64, asyncio, urllib.request, urllib.error, urllib.parse, os, re
from dotenv import load_dotenv
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
_proxy_env = (os.getenv("GROK_PROXY") or "").strip()
PROXY = "" if _proxy_env.lower() in ("", "none", "direct", "false", "0", "no") else _proxy_env


def _http_json(url, method="GET", form=None, timeout=40):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": PROXY, "http": PROXY}) if PROXY else urllib.request.ProxyHandler({}))
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "grok-register-cpa/1.0")
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


async def _browser_authorize(vuc, sso_jwt, interactive=False, wait_seconds=110):
    """无头 Chromium + 代理, 注入 SSO cookie, 按页面状态自动授权。

    注意不要泛化点击 button[type=submit]：x.ai 的 /oauth2/device/approve
    页面如果重复/错误提交会返回 "Invalid action"。
    """
    from patchright.async_api import async_playwright

    debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_device")

    def _safe_name(text):
        return re.sub(r"[^a-zA-Z0-9_.-]+", "_", text or "unknown")[:80]

    async def _save_debug(page, reason):
        os.makedirs(debug_dir, exist_ok=True)
        stamp = int(time.time())
        name = f"{stamp}_{_safe_name(reason)}"
        html_path = os.path.join(debug_dir, f"{name}.html")
        png_path = os.path.join(debug_dir, f"{name}.png")
        try:
            html = await page.content()
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as e:
            html_path = f"保存失败: {type(e).__name__}: {str(e)[:80]}"
        try:
            await page.screenshot(path=png_path, full_page=True, timeout=5000)
        except Exception as e:
            png_path = f"保存失败: {type(e).__name__}: {str(e)[:80]}"
        print(f"  [-] 已保存页面诊断: html={html_path} screenshot={png_path}", flush=True)

    async def _visible_buttons(page):
        try:
            return await page.evaluate("""
        () => Array.from(document.querySelectorAll('button,input[type=submit]')).map((el) => ({
            tag: el.tagName.toLowerCase(),
            text: (el.innerText || el.textContent || el.value || '').trim(),
            type: el.getAttribute('type') || '',
            name: el.getAttribute('name') || '',
            value: el.getAttribute('value') || '',
            visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
        })).filter(x => x.visible)
        """)
        except Exception:
            return []

    async def _click_by_text(page, patterns, log=True):
        import random
        buttons = await _visible_buttons(page)
        if log:
            print(f"  [*] URL: {page.url}", flush=True)
            print(f"  [*] Buttons: {[b.get('text') or b.get('value') for b in buttons]}", flush=True)
        for pattern in patterns:
            locators = [
                page.get_by_role("button", name=pattern).first,
                page.locator(f"button:has-text('{pattern}')").first,
                page.locator(f"input[type=submit][value*='{pattern}']").first,
            ]
            for loc in locators:
                try:
                    if await loc.count() and await loc.is_visible(timeout=1000):
                        print(f"  [*] 拟真点击控件: {pattern}", flush=True)
                        try:
                            box = await loc.bounding_box()
                            if box:
                                target_x = box["x"] + box["width"] / 2
                                target_y = box["y"] + box["height"] / 2
                                await page.mouse.move(target_x + random.randint(-5, 5), target_y + random.randint(-5, 5), steps=8)
                                await page.wait_for_timeout(random.randint(150, 400))
                                await page.mouse.down()
                                await page.wait_for_timeout(random.randint(50, 150))
                                await page.mouse.up()
                            else:
                                await loc.click(timeout=5000, delay=100)
                        except Exception:
                            await loc.click(timeout=5000, delay=100)
                        await page.wait_for_timeout(2500)
                        return True
                except Exception:
                    pass
            try:
                clicked = await page.evaluate("""
                (pattern) => {
                  const els = Array.from(document.querySelectorAll('button,input[type=submit]'));
                  const el = els.find((x) => {
                    const text = (x.innerText || x.textContent || x.value || '').trim();
                    const visible = !!(x.offsetWidth || x.offsetHeight || x.getClientRects().length);
                    return visible && text.includes(pattern);
                  });
                  if (!el) return false;
                  el.click();
                  return true;
                }
                """, pattern)
                if clicked:
                    print(f"  [*] JS 点击控件: {pattern}", flush=True)
                    await page.wait_for_timeout(2500)
                    return True
            except Exception:
                pass
        return False

    async def _click_exact_visible_text(page, texts):
        import random
        for text in texts:
            try:
                loc = page.get_by_text(text, exact=True).first
                if await loc.count() and await loc.is_visible(timeout=1000):
                    print(f"  [*] 拟真点击验证控件: {text}", flush=True)
                    try:
                        box = await loc.bounding_box()
                        if box:
                            target_x = box["x"] + box["width"] / 2
                            target_y = box["y"] + box["height"] / 2
                            # 模拟随机鼠标移动轨迹
                            await page.mouse.move(target_x + random.randint(-10, 10), target_y + random.randint(-10, 10), steps=10)
                            await page.wait_for_timeout(random.randint(200, 500))
                            await page.mouse.move(target_x, target_y, steps=5)
                            await page.mouse.down()
                            await page.wait_for_timeout(random.randint(50, 150))
                            await page.mouse.up()
                        else:
                            await loc.click(delay=100)
                    except Exception:
                        await loc.click(delay=100)
                    await page.wait_for_timeout(4000)
                    return True
            except Exception:
                pass
        return False

    async def _body_text_contains(page, needle, timeout=1000):
        try:
            return needle in (await page.locator("body").inner_text(timeout=timeout))
        except Exception:
            # 页面仍在加载/跳转时 body 可能短时间不可读；交互模式下这不是致命错误。
            return False

    async with async_playwright() as p:
        launch_kwargs = {
            # CF Turnstile 对无头模式(Headless)拦截率极高。
            # 为了保证连贯自动授权不掉登录态，默认关闭无头模式。
            "headless": False if interactive else (os.getenv("GROK_HEADLESS", "0").strip().lower() not in ("0", "false", "no")),
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--incognito"
            ],
        }
        if PROXY:
            launch_kwargs["proxy"] = {"server": PROXY}
        browser = await p.chromium.launch(**launch_kwargs)
        ctx = await browser.new_context(
            viewport={"width": 1000, "height": 700}
        )
        await ctx.add_cookies([{
            "name": "sso", "value": sso_jwt, "domain": ".x.ai", "path": "/",
            "secure": True, "httpOnly": True, "sameSite": "Lax",
        }])
        page = await ctx.new_page()
        keep_browser_open = False
        try:
            print(f"  [*] 打开授权页: {vuc}", flush=True)
            try:
                await page.goto(vuc, timeout=60000 if interactive else 45000, wait_until="commit")
            except Exception as e:
                print(f"  [-] 页面加载超时/异常，继续尝试授权: {type(e).__name__}: {str(e)[:120]}", flush=True)
            if interactive:
                keep_browser_open = True
                print("  [*] 交互模式：浏览器已注入 SSO Cookie。请在弹出的浏览器里点击 Continue/Allow。", flush=True)
                print("  [*] 如果仍跳到登录页，说明当前 SSO Cookie 已失效或未被 accounts.x.ai 接受。", flush=True)
                deadline = time.time() + wait_seconds
                while time.time() < deadline:
                    await page.wait_for_timeout(1500)
                    if "/device/done" in page.url:
                        print("  [OK] 授权页已完成", flush=True)
                        return True
                    if await _body_text_contains(page, "Invalid action"):
                        print("  [-] 授权页返回 Invalid action", flush=True)
                        return False
                print(f"  [-] 交互授权等待超时，最终 URL: {page.url}", flush=True)
                return False

            deadline = time.time() + wait_seconds
            auto_round = 0
            last_state = None
            stagnant_rounds = 0
            while time.time() < deadline:
                auto_round += 1
                await page.wait_for_timeout(1500)
                if "/device/done" in page.url:
                    print("  [OK] 授权页已完成", flush=True)
                    return True
                if await _body_text_contains(page, "Invalid action"):
                    print("  [-] 授权页返回 Invalid action，停止自动点击", flush=True)
                    await _save_debug(page, "invalid_action")
                    return False
                url = page.url
                buttons = await _visible_buttons(page)
                state = (url, tuple(b.get("text") or b.get("value") or "" for b in buttons))
                if state == last_state:
                    stagnant_rounds += 1
                else:
                    stagnant_rounds = 0
                    print(f"  [*] 自动授权状态: round={auto_round}, url={url}, buttons={list(state[1])}", flush=True)
                last_state = state

                if await _click_exact_visible_text(page, ["Click to reveal"]):
                    continue

                # x.ai Device Flow 通常是两步：
                # 1) device 页面点击 Continue/继续
                # 2) consent/approve 页面点击 Allow/允许
                # 之前只按 URL 分支点击；如果页面路径或跳转时机变化，可能漏掉第二步。
                # 这里每轮先全局尝试“明确按钮文本”，不点击裸 submit，避免 Invalid action。
                if await _click_by_text(page, ["继续", "Continue", "Next"], log=False):
                    continue
                if await _click_by_text(page, ["全部允许", "Allow all", "Allow All", "允许", "Allow"], log=False):
                    continue
                if await _click_by_text(page, ["确认我的选择", "Confirm my choices", "Confirm"], log=False):
                    continue

                if "/oauth2/device" in url and "consent" not in url and "approve" not in url:
                    await _click_by_text(page, ["继续", "Continue", "Next"], log=False)
                    continue
                if "consent" in url:
                    clicked = await _click_by_text(page, ["全部允许", "Allow all", "Allow All", "Allow"], log=False)
                    if clicked:
                        continue
                    await _click_by_text(page, ["确认我的选择", "Confirm my choices", "Confirm", "Continue"], log=False)
                    continue
                if "approve" in url:
                    # approve 页只允许点击明确的继续/允许按钮，不点击裸 submit，避免 Invalid action。
                    await _click_by_text(page, ["继续", "Continue", "允许", "Allow"], log=False)
                    continue
                if stagnant_rounds in (4, 8):
                    await _save_debug(page, f"stagnant_round_{stagnant_rounds}")
                if auto_round % 5 == 0:
                    print(f"  [*] 自动授权等待中: round={auto_round}, url={page.url}", flush=True)
            print(f"  [-] 授权页最终 URL: {page.url}", flush=True)
            await _save_debug(page, "auto_authorize_timeout")
        except Exception as e:
            print(f"  [-] 浏览器授权异常: {type(e).__name__}: {str(e)[:160]}", flush=True)
        finally:
            if keep_browser_open:
                print("  [*] 保持交互浏览器打开，等待本轮 token 轮询结束后再关闭。", flush=True)
                # 不在这里关闭：避免用户刚点击 Continue/Allow 时页面被脚本关闭。
                # 进程结束时浏览器会随 patchright 上下文退出。
            else:
                await browser.close()
    return False


def sso_to_device(sso_token, email="", manual=False, wait_seconds=None, interactive=False):
    """Device Flow 铸造: SSO cookie → OAuth AT/RT。
    返回 dict(access_token/refresh_token/expires_in/token_type) 或 None"""
    try:
        s, p = _http_json("https://auth.x.ai/oauth2/device/code", "POST",
                          {"client_id": CLIENT_ID, "scope": SCOPE})
        if s != 200 or not isinstance(p, dict) or "device_code" not in p:
            print(f"  [-] device/code 失败 HTTP {s}: {str(p)[:150]}")
            return None
        dc = p["device_code"]
        vuc = p.get("verification_uri_complete")
        print(f"  [*] User Code: {p.get('user_code', '')}", flush=True)
        print(f"  [*] Verify URL: {vuc}", flush=True)
        wait_seconds = int(wait_seconds or os.getenv("GROK_DEVICE_WAIT", "300" if (manual or interactive) else "90"))
        if manual:
            print("  [*] 手动授权模式：请在浏览器打开上面的 Verify URL，登录/确认授权后保持本程序运行。", flush=True)
            print("  [*] 本程序会自动轮询 token，无需在终端输入。", flush=True)
        elif interactive:
            ok = asyncio.run(asyncio.wait_for(
                _browser_authorize(vuc, sso_token, interactive=True, wait_seconds=wait_seconds),
                timeout=wait_seconds + 20,
            ))
            if not ok:
                print("  [-] 交互授权未确认, 继续轮询", flush=True)
        else:
            ok = asyncio.run(asyncio.wait_for(_browser_authorize(vuc, sso_token), timeout=110))
            if not ok:
                print("  [-] 授权未确认, 继续轮询")
        deadline = time.time() + wait_seconds
        interval = max(int(p.get("interval", 5)), 1)
        poll_no = 0
        while time.time() < deadline:
            time.sleep(interval)
            poll_no += 1
            s, t = _http_json("https://auth.x.ai/oauth2/token", "POST", {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": dc, "client_id": CLIENT_ID})
            if s == 200 and isinstance(t, dict) and t.get("access_token"):
                print(f"  [OK] token 成功: poll={poll_no}", flush=True)
                return {
                    "access_token": t["access_token"],
                    "refresh_token": t.get("refresh_token", ""),
                    "expires_in": int(t.get("expires_in") or 21600),
                    "token_type": t.get("token_type", "Bearer"),
                }
            err = t.get("error") if isinstance(t, dict) else None
            desc = t.get("error_description", "") if isinstance(t, dict) else str(t)[:120]
            if err == "authorization_pending":
                remain = max(int(deadline - time.time()), 0)
                print(f"  [*] poll={poll_no} authorization_pending，剩余等待 {remain}s", flush=True)
                continue
            if err in ("access_denied", "expired_token"):
                print(f"  [-] token 失败: {err}: {desc[:160]}")
                return None
            if err == "slow_down":
                interval += 5
                print(f"  [*] poll={poll_no} slow_down，调整 interval={interval}s", flush=True)
                continue
            print(f"  [-] poll={poll_no} HTTP {s}: {str(t)[:220]}", flush=True)
        print(f"  [-] 轮询超时：{wait_seconds}s 内未检测到授权完成")
        return None
    except Exception as e:
        print(f"  [-] 异常: {type(e).__name__}: {str(e)[:120]}")
        return None


if __name__ == "__main__":
    # 单测: 从 accounts.txt 取一个未使用账号测试
    sso_map = {}
    for l in open(os.path.join(os.path.dirname(__file__), "keys/accounts.txt"),
                  encoding="utf-8-sig").read().splitlines():
        parts = l.split(":")
        if len(parts) >= 3:
            sso_map[parts[0]] = ":".join(parts[2:])
    email = sys.argv[1] if len(sys.argv) > 1 else list(sso_map.keys())[0]
    print(f"测试铸造: {email}")
    result = sso_to_device(sso_map.get(email, ""), email)
    if result:
        print(f"✅ 成功: AT={result['access_token'][:30]}... RT={result['refresh_token'][:20]}...")
    else:
        print("❌ 失败")
