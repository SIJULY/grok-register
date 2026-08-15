"""
邮箱服务类
对外保持接口不变：
- create_email() -> (token_like, email)
- fetch_first_email(token_like) -> str | None

支持 provider:
- domain（默认，自建域名邮箱，IMAP/catch-all）
- gptmail（GPTMail 免费 API）
- mailtm（免费，mail.tm API，无需 Key，推荐）
- luckmail（购买邮箱 + token 轮询）
- mailnest（MailNest 付费 API）
"""

import json
import os
import re
import random
import string as _string
import urllib.parse
import email as _email_mod
import email.header as _email_header
import email.utils as _email_utils
from typing import Any, Dict, List, Optional

from curl_cffi import requests

# 标准 requests 用于 mail.tm（避免 curl_cffi TLS 兼容问题）
import requests as std_requests

try:
    from luckmail import LuckMailClient
    from luckmail.exceptions import LuckMailError
except Exception:
    LuckMailClient = None
    class LuckMailError(Exception):
        pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _luckmail_settings() -> dict:
    return {
        "base_url": str(os.getenv("LUCKMAIL_BASE_URL") or "https://mails.luckyous.com").strip().rstrip("/"),
        "api_key": str(os.getenv("LUCKMAIL_API_KEY") or "").strip(),
        "api_secret": str(os.getenv("LUCKMAIL_API_SECRET") or "").strip(),
        "use_hmac": str(os.getenv("LUCKMAIL_USE_HMAC") or "").strip().lower() in {"1", "true", "yes", "y", "on"},
        "project_code": str(os.getenv("LUCKMAIL_PROJECT_CODE") or "grok").strip(),
        "email_type": str(os.getenv("LUCKMAIL_EMAIL_TYPE") or "ms_imap").strip(),
        "domain": str(os.getenv("LUCKMAIL_DOMAIN") or "outlook.com").strip(),
    }


def _mailnest_settings() -> dict:
    return {
        "api_key": str(os.getenv("MAILNEST_API_KEY") or "").strip(),
        "project_code": str(os.getenv("MAILNEST_PROJECT_CODE") or "x-ai001").strip(),
    }


def _domain_mail_settings() -> dict:
    """自建域名邮箱配置。

    优先支持 HTTP 拉信接口：DOMAIN_MAIL_API_URL="http://host/Mail?token=xxx&mail={mail}"
    如果未配置 HTTP 拉信接口，则回退到 IMAP/catch-all 模式。
    """
    return {
        "domain": str(os.getenv("DOMAIN_MAIL_DOMAIN") or os.getenv("CUSTOM_MAIL_DOMAIN") or "").strip().lstrip("@"),
        "api_url": str(os.getenv("DOMAIN_MAIL_API_URL") or os.getenv("CUSTOM_MAIL_API_URL") or "").strip(),
        "api_timeout": int(str(os.getenv("DOMAIN_MAIL_API_TIMEOUT") or "15").strip() or "15"),
        "imap_host": str(os.getenv("DOMAIN_MAIL_IMAP_HOST") or os.getenv("CUSTOM_MAIL_IMAP_HOST") or "").strip(),
        "imap_port": int(str(os.getenv("DOMAIN_MAIL_IMAP_PORT") or os.getenv("CUSTOM_MAIL_IMAP_PORT") or "993").strip() or "993"),
        "username": str(os.getenv("DOMAIN_MAIL_USERNAME") or os.getenv("CUSTOM_MAIL_USERNAME") or "").strip(),
        "password": str(os.getenv("DOMAIN_MAIL_PASSWORD") or os.getenv("CUSTOM_MAIL_PASSWORD") or "").strip(),
        "mailbox": str(os.getenv("DOMAIN_MAIL_MAILBOX") or "INBOX").strip() or "INBOX",
        "prefix": str(os.getenv("DOMAIN_MAIL_PREFIX") or "grok").strip() or "grok",
        "random_length": int(str(os.getenv("DOMAIN_MAIL_RANDOM_LENGTH") or "10").strip() or "10"),
        "ssl": str(os.getenv("DOMAIN_MAIL_SSL") or "true").strip().lower() not in {"0", "false", "no", "off"},
        "filter_from": str(os.getenv("DOMAIN_MAIL_FILTER_FROM") or "x.ai").strip(),
        "strict_recipient": str(os.getenv("DOMAIN_MAIL_STRICT_RECIPIENT") or "true").strip().lower() not in {"0", "false", "no", "off"},
    }


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    try:
        parts = _email_header.decode_header(value)
        decoded = []
        for chunk, charset in parts:
            if isinstance(chunk, bytes):
                decoded.append(chunk.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(str(chunk))
        return "".join(decoded)
    except Exception:
        return str(value)


class GPTMailClient:
    """与现有 grok-register 保持一致的 GPTMail 访问方式"""

    def __init__(self, proxies: Any = None):
        self.base_url = "https://mail.chatgpt.org.uk"
        self.session = requests.Session(proxies=proxies, impersonate="chrome")
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": f"{self.base_url}/",
            }
        )

    def _init_browser_session(self):
        try:
            resp = self.session.get(self.base_url, timeout=15)
            gm_sid = self.session.cookies.get("gm_sid")
            if gm_sid:
                self.session.headers.update({"Cookie": f"gm_sid={gm_sid}"})
            token_match = re.search(r"(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)", resp.text)
            if token_match:
                self.session.headers.update({"x-inbox-token": token_match.group(1)})
        except Exception:
            pass

    def generate_email(self) -> str:
        self._init_browser_session()
        resp = self.session.get(f"{self.base_url}/api/generate-email", timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"GPTMail 生成失败: {resp.status_code}")
        data = resp.json()
        email = str(((data.get("data") or {}).get("email") or "")).strip()
        token = str(((data.get("auth") or {}).get("token") or "")).strip()
        if token:
            self.session.headers.update({"x-inbox-token": token})
        if not email:
            raise RuntimeError("GPTMail 返回邮箱为空")
        return email

    def list_emails(self, email: str) -> List[Dict[str, Any]]:
        encoded_email = urllib.parse.quote(email)
        url = f"{self.base_url}/api/emails?email={encoded_email}"
        resp = self.session.get(url, timeout=15)
        if resp.status_code == 200:
            return ((resp.json() or {}).get("data") or {}).get("emails") or []
        return []


class MailTMClient:
    """mail.tm 免费临时邮箱 — 无需 API Key，无需注册
    API 文档: https://docs.mail.tm/

    用法:
        client = MailTMClient()
        email = client.create_email()
        content = client.fetch_first_email()  # 阻塞轮询，最多 60s
    """

    BASE = "https://api.mail.tm"

    def __init__(self, proxies: Any = None):
        self.session = std_requests.Session()
        if proxies:
            self.session.proxies.update(proxies)
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json",
        })
        self.address = ""
        self._token = ""
        self._password = ""

    def _random_user(self) -> str:
        return "grok" + "".join(random.choices(_string.ascii_lowercase + _string.digits, k=10))

    def create_email(self) -> str:
        """创建临时邮箱，返回邮箱地址"""
        # 1. 获取可用域名
        r = self.session.get(f"{self.BASE}/domains", timeout=15)
        r.raise_for_status()
        data = r.json()
        # API 有时返回 list，有时返回 hydra:Collection 包装
        if isinstance(data, list):
            domains = data
        else:
            domains = data.get("hydra:member", [])
        if not domains:
            raise RuntimeError("mail.tm: 无可用域名")
        domain = domains[0]["domain"]

        # 2. 注册账号
        self._password = "Gk" + "".join(random.choices(_string.ascii_letters + _string.digits, k=14)) + "!1"
        self.address = f"{self._random_user()}@{domain}"
        r2 = self.session.post(f"{self.BASE}/accounts", json={
            "address": self.address,
            "password": self._password,
        }, timeout=15)
        if r2.status_code != 201:
            raise RuntimeError(f"mail.tm 创建账号失败: {r2.status_code} {r2.text[:200]}")
        account = r2.json()
        self.address = account["address"]

        # 3. 获取 JWT token
        r3 = self.session.post(f"{self.BASE}/token", json={
            "address": self.address,
            "password": self._password,
        }, timeout=15)
        r3.raise_for_status()
        self._token = r3.json()["token"]

        return self.address

    def fetch_first_email(self) -> Optional[str]:
        """单次检查收件箱，返回第一封邮件内容（或 None）。
        调用方负责轮询（grok.py 外层每 5s 调用一次，共 12 次 = 60s）。"""
        if not self._token:
            return None
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            r = self.session.get(f"{self.BASE}/messages", headers=headers, timeout=10)
            if r.status_code == 401:
                # token 过期，尝试刷新
                r2 = self.session.post(f"{self.BASE}/token", json={
                    "address": self.address, "password": self._password,
                }, timeout=10)
                if r2.status_code == 200:
                    self._token = r2.json()["token"]
                    headers = {"Authorization": f"Bearer {self._token}"}
                    r = self.session.get(f"{self.BASE}/messages", headers=headers, timeout=10)
            if r.status_code != 200:
                return None
            _msg_data = r.json()
            messages = _msg_data if isinstance(_msg_data, list) else _msg_data.get("hydra:member", [])
            if not messages:
                return None
            msg_id = messages[0]["id"]
            # 获取完整邮件内容
            r3 = self.session.get(f"{self.BASE}/messages/{msg_id}", headers=headers, timeout=10)
            if r3.status_code != 200:
                return None
            msg = r3.json()
            parts = []
            if msg.get("subject"):
                parts.append(msg["subject"])
            if msg.get("text"):
                parts.append(msg["text"])
            if msg.get("html"):
                parts.append("\n".join(msg["html"]) if isinstance(msg["html"], list) else str(msg["html"]))
            if not parts:
                parts.append(msg.get("intro", ""))
                parts.append(str(msg))
            return "\n".join(parts) if parts else str(msg)
        except Exception:
            return None


class LuckMailInbox:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str = "",
        use_hmac: bool = False,
        project_code: str = "grok",
        email_type: str = "ms_imap",
        domain: str = "outlook.com",
        timeout: int = 30,
    ):
        if LuckMailClient is None:
            raise RuntimeError("LuckMail SDK 不可用")
        if not base_url:
            raise RuntimeError("缺少 LUCKMAIL_BASE_URL")
        if not api_key:
            raise RuntimeError("缺少 LUCKMAIL_API_KEY")

        self.client = LuckMailClient(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret or None,
            use_hmac=bool(use_hmac),
            timeout=float(timeout),
        )
        self.project_code = project_code or "grok"
        self.email_type = email_type or "ms_imap"
        self.domain = domain or "outlook.com"
        self.address = ""
        self.token = ""

    def create_email(self):
        try:
            result = self.client.user.purchase_emails(
                project_code=self.project_code,
                quantity=1,
                email_type=self.email_type,
                domain=self.domain,
            )
        except LuckMailError as e:
            raise RuntimeError(f"LuckMail 购买邮箱失败: {e}") from e
        except Exception as e:
            raise RuntimeError(f"LuckMail 初始化失败: {e}") from e

        purchases = list((result or {}).get("purchases") or [])
        if not purchases:
            raise RuntimeError("LuckMail 购买邮箱失败：未返回 purchases")

        purchase = purchases[0] or {}
        self.address = str(purchase.get("email_address") or "").strip()
        self.token = str(purchase.get("token") or "").strip()
        if not self.address or not self.token:
            raise RuntimeError("LuckMail 购买邮箱失败：缺少 email_address 或 token")
        return {"provider": "luckmail", "token": self.token, "email": self.address, "client": self}, self.address

    def fetch_first_email(self) -> Optional[str]:
        if not self.token:
            return None
        try:
            result = self.client.user.get_token_code(self.token)
            chunks: List[str] = []
            if getattr(result, "verification_code", None):
                chunks.append(str(getattr(result, "verification_code", "") or ""))
            if getattr(result, "mail", None):
                chunks.append(json.dumps(getattr(result, "mail", None) or {}, ensure_ascii=False))

            mail_list = self.client.user.get_token_mails(self.token)
            mails = list(getattr(mail_list, "mails", []) or [])
            if mails:
                mail = mails[0]
                message_id = str(getattr(mail, "message_id", "") or "").strip()
                chunks.extend([
                    str(getattr(mail, "subject", "") or ""),
                    str(getattr(mail, "body", "") or ""),
                    str(getattr(mail, "html_body", "") or ""),
                ])
                if message_id:
                    try:
                        detail = self.client.user.get_token_mail_detail(self.token, message_id)
                        chunks.extend([
                            str(getattr(detail, "subject", "") or ""),
                            str(getattr(detail, "body_text", "") or ""),
                            str(getattr(detail, "body_html", "") or ""),
                            str(getattr(detail, "verification_code", "") or ""),
                        ])
                    except Exception:
                        pass
            text = "\n".join([c for c in chunks if c])
            return text or None
        except Exception as e:
            print(f"获取 LuckMail 邮件失败: {e}")
            return None


class MailNestInbox:
    def __init__(
            self,
            api_key: str,
            project_code: str = "z-ai001",
            timeout: int = 30,
    ):
        if not api_key:
            raise RuntimeError("缺少 MailNest_API_KEY")

        self.api_key = api_key
        self.project_code = project_code
        self.timeout = timeout

    def __req(self, method, url, params=None, json=None):
        resp = requests.request(
            method,
            url,
            params=params,
            json=json,
            headers={
                "Authorization": f"Bearer {self.api_key}",
            },
            verify=False,
        )
        if resp.status_code == 401:
            print('api-key 无效')
            raise Exception('api-key 无效')
        resp.raise_for_status()
        resp_json = resp.json()
        print(resp_json)
        if resp_json['code'] != '00000':
            raise Exception(f'{resp_json}')
        return resp_json['data']

    def create_email(self):
        email = ''
        try:
            email = self.__req(
                'POST',
                "https://mailnest.top/api/v1/email/temporary/buy",
                json={
                    "project_code": self.project_code,
                    "count": 1,
                }
            )[0]['email']
        except:
            pass
        # 当项目邮箱数量不足时会获取失败 或 没有这个项目 购买独占邮箱
        if not email:
            try:
                email = self.__req(
                    'POST',
                    "https://mailnest.top/api/v1/email/exclusive/buy",
                    json={
                        "count": 1,
                    }
                )[0]['email']
            except:
                pass
        if not email:
            raise RuntimeError("MailNest 购买邮箱失败")
        return {"provider": "mailnest", "token": self.api_key, "email": email, "client": self}, email

    def fetch_first_email(self, email) -> Optional[str]:
        if not self.api_key:
            return None
        try:
            mails = self.__req(
                'POST',
                f'https://mailnest.top/api/v1/email/receive',
                json={
                    "email": email,
                },
            )
            if not mails:
                return None
            return '\n'.join([
                mails[0]['subject'],
                mails[0]['body_preview'],
                mails[0]['body'],
            ]) or None
        except Exception as e:
            print(f"获取 MailNest 邮件失败: {e}")
            return None


class GPTMailInboxV2:
    """GPTMail V2 客户端 — 使用新版 API（2026-07）

    API 流程:
      1. GET  /api/domains/public  → 获取活跃域名列表
      2. 客户端拼邮箱: prefix@random_domain
      3. POST /api/inbox-token     → 注册邮箱，获取 JWT token
      4. GET  /api/emails?email=.. → 轮询邮件
      5. GET  /api/email/{id}      → 获取邮件详情
    """

    def __init__(self, proxies: Any = None):
        self.base_url = "https://mail.chatgpt.org.uk"
        self.session = requests.Session()
        if proxies:
            self.session.proxies.update(proxies)
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json",
        })
        self.email = ""
        self.token = ""
        self._domains = []

    def _warmup(self):
        try:
            self.session.get(f"{self.base_url}/", timeout=10)
        except Exception:
            pass

    def _get_domains(self):
        if self._domains:
            return self._domains
        self._warmup()
        r = self.session.get(f"{self.base_url}/api/domains/public", timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"获取域名失败: {r.status_code}")
        data = r.json()
        domains_list = (data.get("data") or {}).get("domains") or []
        self._domains = [d["domain_name"] for d in domains_list if d.get("is_active")]
        if not self._domains:
            raise RuntimeError("无活跃域名")
        return self._domains

    def create_email(self) -> str:
        domains = self._get_domains()
        prefix = "".join(random.choices(_string.ascii_lowercase + _string.digits, k=10))
        domain = random.choice(domains)
        self.email = f"{prefix}@{domain}"

        r = self.session.post(
            f"{self.base_url}/api/inbox-token",
            headers={"Content-Type": "application/json"},
            json={"email": self.email},
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"inbox-token 失败: {r.status_code}")
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"inbox-token 返回失败: {data}")
        self.token = (data.get("auth") or {}).get("token") or ""
        if not self.token:
            raise RuntimeError("未获取到 inbox token")
        return self.email

    def fetch_first_email(self) -> Optional[str]:
        if not self.token:
            return None
        try:
            encoded = urllib.parse.quote(self.email)
            r = self.session.get(
                f"{self.base_url}/api/emails?email={encoded}",
                headers={"x-inbox-token": self.token},
                timeout=15,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            emails = (data.get("data") or {}).get("emails") or data.get("data") or []
            if isinstance(emails, dict):
                emails = [emails]
            for msg in (emails if isinstance(emails, list) else []):
                subject = str(msg.get("subject") or "")
                body = str(msg.get("text") or msg.get("html") or msg.get("body") or "")
                text = subject + " " + body
                # 先检查是否有验证码
                m = re.search(r"([A-Z0-9]{3})-?([A-Z0-9]{3})", text)
                if m:
                    return text
                # 有内容就返回
                if len(text) > 10:
                    return text
            # 尝试获取详情
            for msg in (emails if isinstance(emails, list) else []):
                msg_id = msg.get("id") or msg.get("message_id")
                if msg_id:
                    r2 = self.session.get(
                        f"{self.base_url}/api/email/{urllib.parse.quote(str(msg_id))}",
                        headers={"x-inbox-token": self.token},
                        timeout=15,
                    )
                    if r2.status_code == 200:
                        detail = r2.json()
                        d = (detail.get("data") or detail)
                        text = str(d.get("subject") or "") + " " + str(d.get("text") or d.get("html") or d.get("body") or "")
                        if len(text) > 10:
                            return text
        except Exception:
            pass
        return None


class DomainMailIMAPClient:
    """自建域名邮箱客户端 — 随机生成 域名前缀@自有域名，并通过 IMAP 读取验证码。

    适用场景：
      - 你的 VPS 邮箱已配置 catch-all，所有 *@你的域名 都进入同一个收件箱；推荐。
      - 或者你只使用一个真实邮箱地址，此时可把 DOMAIN_MAIL_PREFIX 配成固定前缀，
        DOMAIN_MAIL_RANDOM_LENGTH=0，并把 DOMAIN_MAIL_STRICT_RECIPIENT=false。
    """

    def __init__(
        self,
        domain: str,
        imap_host: str,
        username: str,
        password: str,
        imap_port: int = 993,
        mailbox: str = "INBOX",
        prefix: str = "grok",
        random_length: int = 10,
        use_ssl: bool = True,
        filter_from: str = "x.ai",
        strict_recipient: bool = True,
        proxies: Any = None,
    ):
        if not domain:
            raise RuntimeError("缺少 DOMAIN_MAIL_DOMAIN")
        if not imap_host:
            raise RuntimeError("缺少 DOMAIN_MAIL_IMAP_HOST")
        if not username:
            raise RuntimeError("缺少 DOMAIN_MAIL_USERNAME")
        if not password:
            raise RuntimeError("缺少 DOMAIN_MAIL_PASSWORD")

        self.domain = domain.lstrip("@").strip().lower()
        self.imap_host = imap_host.strip()
        self.username = username.strip()
        self.password = password
        self.imap_port = int(imap_port or 993)
        self.mailbox = mailbox or "INBOX"
        self.prefix = re.sub(r"[^a-zA-Z0-9._+-]", "", prefix or "grok") or "grok"
        self.random_length = max(0, int(random_length or 0))
        self.use_ssl = bool(use_ssl)
        self.filter_from = filter_from.strip()
        self.strict_recipient = bool(strict_recipient)
        self.proxies = proxies
        self.email = ""
        self._imap = None
        self._last_uid = 0

    def _connect(self):
        import imaplib

        if self._imap:
            try:
                self._imap.noop()
                return
            except Exception:
                try:
                    self._imap.logout()
                except Exception:
                    pass
                self._imap = None

        if self.use_ssl:
            self._imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        else:
            self._imap = imaplib.IMAP4(self.imap_host, self.imap_port)
        self._imap.login(self.username, self.password)
        status, _ = self._imap.select(self.mailbox)
        if status != "OK":
            raise RuntimeError(f"选择 IMAP 邮箱目录失败: {self.mailbox}")

    def _build_address(self) -> str:
        suffix = "".join(random.choices(_string.ascii_lowercase + _string.digits, k=self.random_length))
        local_part = f"{self.prefix}{suffix}"
        return f"{local_part}@{self.domain}".lower()

    def create_email(self) -> str:
        self.email = self._build_address()
        # 记录创建地址时已有的最新 UID，后续只查新邮件，避免读到旧验证码。
        try:
            self._connect()
            _, data = self._imap.uid("SEARCH", None, "ALL")
            uids = data[0].split() if data and data[0] else []
            self._last_uid = int(uids[-1]) if uids else 0
        except Exception as e:
            raise RuntimeError(f"自建邮箱 IMAP 连接失败: {e}") from e
        return self.email

    def _message_matches_recipient(self, msg) -> bool:
        if not self.strict_recipient:
            return True
        target = self.email.lower()
        headers = []
        for name in ("To", "Delivered-To", "X-Original-To", "Envelope-To", "Cc"):
            value = msg.get(name)
            if value:
                headers.append(str(value))
        raw = "\n".join(headers).lower()
        if target in raw:
            return True
        for _, addr in _email_utils.getaddresses(headers):
            if addr.lower() == target:
                return True
        return False

    def _extract_text(self, msg) -> str:
        chunks: List[str] = []
        subject = _decode_header_value(str(msg.get("Subject") or ""))
        from_header = _decode_header_value(str(msg.get("From") or ""))
        if subject:
            chunks.append(subject)
        if from_header:
            chunks.append(from_header)

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disposition = str(part.get("Content-Disposition") or "").lower()
                if "attachment" in disposition:
                    continue
                if ctype not in ("text/plain", "text/html"):
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                chunks.append(payload.decode(charset, errors="replace"))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                chunks.append(payload.decode(charset, errors="replace"))
        return "\n".join([c for c in chunks if c])

    def fetch_first_email(self) -> Optional[str]:
        if not self.email:
            return None
        try:
            self._connect()
            criteria = f'(UID {self._last_uid + 1}:*)'
            if self.filter_from:
                criteria = f'(FROM "{self.filter_from}" UID {self._last_uid + 1}:*)'
            _, data = self._imap.uid("SEARCH", None, criteria)
            uids = data[0].split() if data and data[0] else []
            if not uids and self.filter_from:
                # 有些自建邮箱 IMAP 对 FROM 搜索支持不稳定，退回到只搜新邮件。
                _, data = self._imap.uid("SEARCH", None, f'(UID {self._last_uid + 1}:*)')
                uids = data[0].split() if data and data[0] else []
            if not uids:
                return None

            for uid in reversed(uids[-20:]):
                latest_uid = int(uid)
                _, msg_data = self._imap.uid("FETCH", uid, "(BODY.PEEK[])" )
                if not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple):
                    continue
                msg = _email_mod.message_from_bytes(msg_data[0][1])
                self._last_uid = max(self._last_uid, latest_uid)
                if not self._message_matches_recipient(msg):
                    continue
                text = self._extract_text(msg)
                if text:
                    return text
            return None
        except Exception as e:
            print(f"[DomainMail] fetch error: {e}")
            return None


class DomainMailHTTPClient:
    """自建域名邮箱 HTTP 拉信客户端。

    适配类似：
      http://89.168.116.152:2099/Mail?token=2088&mail={mail}
    """

    def __init__(
        self,
        domain: str,
        api_url: str,
        prefix: str = "grok",
        random_length: int = 10,
        timeout: int = 15,
        proxies: Any = None,
    ):
        if not domain:
            raise RuntimeError("缺少 DOMAIN_MAIL_DOMAIN")
        if not api_url:
            raise RuntimeError("缺少 DOMAIN_MAIL_API_URL")
        self.domain = domain.lstrip("@").strip().lower()
        self.api_url = api_url.strip()
        self.prefix = re.sub(r"[^a-zA-Z0-9._+-]", "", prefix or "grok") or "grok"
        self.random_length = max(0, int(random_length or 0))
        self.timeout = int(timeout or 15)
        self.proxies = proxies
        self.email = ""
        self.session = std_requests.Session()
        if proxies:
            self.session.proxies.update(proxies)
        self.session.headers.update({"User-Agent": UA})
        self.debug = _env_truthy("DOMAIN_MAIL_DEBUG")

    def _build_address(self) -> str:
        suffix = "".join(random.choices(_string.ascii_lowercase + _string.digits, k=self.random_length))
        return f"{self.prefix}{suffix}@{self.domain}".lower()

    def _build_url(self) -> str:
        encoded_mail = urllib.parse.quote(self.email, safe="")
        if "{mail}" in self.api_url:
            return self.api_url.replace("{mail}", encoded_mail)
        if "{}" in self.api_url:
            return self.api_url.format(encoded_mail)
        sep = "&" if "?" in self.api_url else "?"
        return f"{self.api_url}{sep}mail={encoded_mail}"

    def create_email(self) -> str:
        self.email = self._build_address()
        return self.email

    def fetch_first_email(self) -> Optional[str]:
        if not self.email:
            return None
        try:
            url = self._build_url()
            resp = self.session.get(url, timeout=self.timeout)
            text = resp.text or ""
            if self.debug:
                preview = text[:200].replace("\n", " ").replace("\r", " ")
                print(f"[DomainMailHTTP] {self.email} GET {url} -> {resp.status_code}, len={len(text)}, preview={preview!r}", flush=True)
            if resp.status_code != 200:
                print(f"[DomainMailHTTP] {self.email} 拉信接口非 200: {resp.status_code}, body={text[:200]!r}", flush=True)
                return None
            if not text.strip():
                return None

            m = re.search(r'<div\s+class=["\']code["\'][^>]*>[^0-9A-Z]{0,20}([A-Z0-9]{3}-?[A-Z0-9]{3}|[0-9]{6})</div>', text, re.I)
            if m:
                return f"{m.group(1)}\n{text}"

            m = re.search(r"([A-Z0-9]{3}-[A-Z0-9]{3}|[A-Z0-9]{6}|[0-9]{6})", text, re.I)
            if m:
                return text
            return text if len(text) > 20 else None
        except Exception as e:
            print(f"[DomainMailHTTP] fetch error: {e}")
            return None


class GmailIMAPClient:
    """Gmail IMAP 客户端 — 用 +alias 无限衍生邮箱，IMAP 轮询收验证码"""

    def __init__(self, proxies: Any = None):
        import imaplib
        import email as _email_mod
        self.proxies = proxies
        self.email = ""
        self._imap = None
        self._base_email = "shuhaoxin41@gmail.com"
        self._app_password = "xnuf tpxm gyxi xcbh"
        self._imap_host = "imap.gmail.com"
        self._imap_port = 993
        self._last_uid = 0  # 记录上次检查到的最新邮件 UID

    def _connect(self):
        import imaplib
        if self._imap:
            try:
                self._imap.noop()
                return
            except Exception:
                pass
        self._imap = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
        self._imap.login(self._base_email, self._app_password)
        self._imap.select("INBOX")

    def create_email(self) -> str:
        tag = "".join(random.choice(_string.ascii_lowercase + _string.digits) for _ in range(8))
        self.email = f"{self._base_email.split('@')[0]}+grok{tag}@{self._base_email.split('@')[1]}"
        # 连 IMAP 获取当前最新 UID 作为基准
        try:
            self._connect()
            _, data = self._imap.uid("SEARCH", None, "ALL")
            uids = data[0].split()
            self._last_uid = int(uids[-1]) if uids else 0
        except Exception:
            self._last_uid = 0
        return self.email

    def fetch_first_email(self) -> Optional[str]:
        import email as _email_mod
        try:
            self._connect()
            # 只搜 From x.ai 的邮件（避免 Windsurf 等干扰）
            _, data = self._imap.uid("SEARCH", None, f'(FROM "x.ai" UID {self._last_uid + 1}:*)')
            new_uids = data[0].split()
            if not new_uids:
                return None
            # 取最新一封
            latest_uid = int(new_uids[-1])
            self._last_uid = max(self._last_uid, latest_uid)
            _, msg_data = self._imap.uid("FETCH", str(latest_uid).encode(), "(BODY.PEEK[])")
            raw = msg_data[0][1]
            msg = _email_mod.message_from_bytes(raw)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if ctype in ("text/plain", "text/html"):
                        payload = part.get_payload(decode=True)
                        if payload:
                            body += payload.decode("utf-8", errors="replace") + "\n"
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
            return body
        except Exception as e:
            print(f"[Gmail] fetch error: {e}")
            return None


class EmailService:
    """统一邮箱服务门面，兼容旧调用方"""

    def __init__(self, proxies: Any = None, provider: str = "domain"):
        self.proxies = proxies
        self.provider = str(provider or os.getenv("EMAIL_PROVIDER") or "domain").strip().lower()
        if self.provider in {"custom", "custommail", "domainmail", "imap"}:
            self.provider = "domain"
        if self.provider not in {"domain", "gptmail", "mailtm", "luckmail", "mailnest", "gmail"}:
            raise ValueError(f"不支持的邮箱提供商: {self.provider}")

    def create_email(self):
        if self.provider == "domain":
            try:
                settings = _domain_mail_settings()
                if settings["api_url"]:
                    client = DomainMailHTTPClient(
                        domain=settings["domain"],
                        api_url=settings["api_url"],
                        prefix=settings["prefix"],
                        random_length=settings["random_length"],
                        timeout=settings["api_timeout"],
                        proxies=self.proxies,
                    )
                else:
                    client = DomainMailIMAPClient(
                        domain=settings["domain"],
                        imap_host=settings["imap_host"],
                        imap_port=settings["imap_port"],
                        username=settings["username"],
                        password=settings["password"],
                        mailbox=settings["mailbox"],
                        prefix=settings["prefix"],
                        random_length=settings["random_length"],
                        use_ssl=settings["ssl"],
                        filter_from=settings["filter_from"],
                        strict_recipient=settings["strict_recipient"],
                        proxies=self.proxies,
                    )
                email = client.create_email()
                token_like = {"provider": "domain", "client": client, "email": email}
                print(f"[+] 自建域名邮箱已创建: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] 自建域名邮箱出错: {e}")
                return None, None
        elif self.provider == "mailtm":
            try:
                client = MailTMClient(self.proxies)
                email = client.create_email()
                token_like = {"provider": "mailtm", "client": client, "email": email}
                print(f"[+] mail.tm 邮箱已创建: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] 请求 mail.tm 出错: {e}")
                return None, None
        elif self.provider == "luckmail":
            try:
                settings = _luckmail_settings()
                inbox = LuckMailInbox(
                    base_url=settings["base_url"],
                    api_key=settings["api_key"],
                    api_secret=settings["api_secret"],
                    use_hmac=settings["use_hmac"],
                    project_code=settings["project_code"],
                    email_type=settings["email_type"],
                    domain=settings["domain"],
                )
                token_like, email = inbox.create_email()
                print(f"[+] LuckMail 邮箱已购买: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] 请求 LuckMail 出错: {e}")
                return None, None
        elif self.provider == 'mailnest':
            try:
                settings = _mailnest_settings()
                inbox = MailNestInbox(
                    api_key=settings["api_key"],
                    project_code=settings["project_code"],
                )
                token_like, email = inbox.create_email()
                print(f"[+] MailNest 邮箱已购买: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] 请求 MailNest 出错: {e}")
                return None, None
        elif self.provider == "gmail":
            try:
                client = GmailIMAPClient(self.proxies)
                email = client.create_email()
                token_like = {"provider": "gmail", "client": client, "email": email}
                print(f"[+] Gmail 别名已创建: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] Gmail 出错: {e}")
                return None, None
        # gptmail: 使用 V2 API
        try:
            client = GPTMailInboxV2(self.proxies)
            email = client.create_email()
            token_like = {"provider": "gptmail-v2", "client": client, "email": email}
            print(f"[+] GPTMail 邮箱已创建: {email}")
            return token_like, email
        except Exception as e:
            print(f"[Error] 请求 GPTMail 出错: {e}")
            return None, None

    def fetch_first_email(self, token_like):
        try:
            if not isinstance(token_like, dict):
                return None

            provider = str(token_like.get("provider") or "gptmail").strip().lower()
            client = token_like.get("client")
            if not client:
                return None

            if provider in ("domain", "mailtm", "luckmail", "gptmail-v2", "gmail"):
                return client.fetch_first_email()
            elif provider == "mailnest":
                return client.fetch_first_email(token_like.get("email"))

            # legacy gptmail
            email = str(token_like.get("email") or "").strip()
            if not email:
                return None

            emails = client.list_emails(email)
            if not emails:
                return None

            first = emails[0] or {}
            subject = str(first.get("subject") or "")
            from_name = str(((first.get("from") or {}).get("name") or ""))
            from_email = str(((first.get("from") or {}).get("address") or first.get("from_address") or ""))
            body_text = str(first.get("text") or first.get("content") or "")
            body_html = str(first.get("html") or first.get("html_content") or "")
            return "\n".join([f">{subject}<", subject, from_name, from_email, body_text, body_html])
        except Exception as e:
            print(f"获取邮件失败: {e}")
            return None
