"""自建域名邮箱服务，支持 HTTP 拉信和 IMAP/catch-all。"""

import email as email_module
import email.header
import email.utils
import os
import random
import re
import string
import urllib.parse
from typing import Any, List, Optional

import requests


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _domain_mail_settings() -> dict:
    """读取自建域名邮箱配置，优先使用 HTTP 接口，否则使用 IMAP。"""
    return {
        "domain": str(os.getenv("DOMAIN_MAIL_DOMAIN") or "").strip().lstrip("@"),
        "api_url": str(os.getenv("DOMAIN_MAIL_API_URL") or "").strip(),
        "api_timeout": int(str(os.getenv("DOMAIN_MAIL_API_TIMEOUT") or "15").strip() or "15"),
        "imap_host": str(os.getenv("DOMAIN_MAIL_IMAP_HOST") or "").strip(),
        "imap_port": int(str(os.getenv("DOMAIN_MAIL_IMAP_PORT") or "993").strip() or "993"),
        "username": str(os.getenv("DOMAIN_MAIL_USERNAME") or "").strip(),
        "password": str(os.getenv("DOMAIN_MAIL_PASSWORD") or "").strip(),
        "mailbox": str(os.getenv("DOMAIN_MAIL_MAILBOX") or "INBOX").strip() or "INBOX",
        "prefix": str(os.getenv("DOMAIN_MAIL_PREFIX") or "grok").strip() or "grok",
        "random_length": int(str(os.getenv("DOMAIN_MAIL_RANDOM_LENGTH") or "10").strip() or "10"),
        "ssl": str(os.getenv("DOMAIN_MAIL_SSL") or "true").strip().lower()
        not in {"0", "false", "no", "off"},
        "filter_from": str(os.getenv("DOMAIN_MAIL_FILTER_FROM") or "x.ai").strip(),
        "strict_recipient": str(os.getenv("DOMAIN_MAIL_STRICT_RECIPIENT") or "true").strip().lower()
        not in {"0", "false", "no", "off"},
    }


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    try:
        decoded = []
        for chunk, charset in email.header.decode_header(value):
            if isinstance(chunk, bytes):
                decoded.append(chunk.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(str(chunk))
        return "".join(decoded)
    except Exception:
        return str(value)


class _DomainAddressMixin:
    def _configure_address(self, domain: str, prefix: str, random_length: int) -> None:
        if not domain:
            raise RuntimeError("缺少 DOMAIN_MAIL_DOMAIN")
        self.domain = domain.lstrip("@").strip().lower()
        self.prefix = re.sub(r"[^a-zA-Z0-9._+-]", "", prefix or "grok") or "grok"
        self.random_length = max(0, int(random_length or 0))
        self.email = ""

    def _build_address(self) -> str:
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=self.random_length))
        return f"{self.prefix}{suffix}@{self.domain}".lower()


class DomainMailIMAPClient(_DomainAddressMixin):
    """通过 catch-all 收件箱的 IMAP 接口读取自建域名邮件。"""

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
        self._configure_address(domain, prefix, random_length)
        if not imap_host:
            raise RuntimeError("缺少 DOMAIN_MAIL_IMAP_HOST")
        if not username:
            raise RuntimeError("缺少 DOMAIN_MAIL_USERNAME")
        if not password:
            raise RuntimeError("缺少 DOMAIN_MAIL_PASSWORD")
        self.imap_host = imap_host.strip()
        self.username = username.strip()
        self.password = password
        self.imap_port = int(imap_port or 993)
        self.mailbox = mailbox or "INBOX"
        self.use_ssl = bool(use_ssl)
        self.filter_from = filter_from.strip()
        self.strict_recipient = bool(strict_recipient)
        self._imap = None
        self._last_uid = 0

    def _connect(self) -> None:
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
        imap_class = imaplib.IMAP4_SSL if self.use_ssl else imaplib.IMAP4
        self._imap = imap_class(self.imap_host, self.imap_port)
        self._imap.login(self.username, self.password)
        status, _ = self._imap.select(self.mailbox)
        if status != "OK":
            raise RuntimeError(f"选择 IMAP 邮箱目录失败: {self.mailbox}")

    def create_email(self) -> str:
        self.email = self._build_address()
        try:
            self._connect()
            _, data = self._imap.uid("SEARCH", None, "ALL")
            uids = data[0].split() if data and data[0] else []
            self._last_uid = int(uids[-1]) if uids else 0
        except Exception as exc:
            raise RuntimeError(f"自建邮箱 IMAP 连接失败: {exc}") from exc
        return self.email

    def _message_matches_recipient(self, message) -> bool:
        if not self.strict_recipient:
            return True
        headers = [
            str(message.get(name))
            for name in ("To", "Delivered-To", "X-Original-To", "Envelope-To", "Cc")
            if message.get(name)
        ]
        if self.email.lower() in "\n".join(headers).lower():
            return True
        return any(address.lower() == self.email.lower() for _, address in email.utils.getaddresses(headers))

    @staticmethod
    def _extract_text(message) -> str:
        chunks: List[str] = []
        subject = _decode_header_value(str(message.get("Subject") or ""))
        sender = _decode_header_value(str(message.get("From") or ""))
        chunks.extend(value for value in (subject, sender) if value)
        parts = message.walk() if message.is_multipart() else [message]
        for part in parts:
            if "attachment" in str(part.get("Content-Disposition") or "").lower():
                continue
            if part.get_content_type() not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if payload:
                chunks.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
        return "\n".join(chunks)

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
                _, data = self._imap.uid("SEARCH", None, f'(UID {self._last_uid + 1}:*)')
                uids = data[0].split() if data and data[0] else []
            for uid in reversed(uids[-20:]):
                latest_uid = int(uid)
                _, message_data = self._imap.uid("FETCH", uid, "(BODY.PEEK[])")
                if not message_data or not message_data[0] or not isinstance(message_data[0], tuple):
                    continue
                message = email_module.message_from_bytes(message_data[0][1])
                self._last_uid = max(self._last_uid, latest_uid)
                if self._message_matches_recipient(message):
                    text = self._extract_text(message)
                    if text:
                        return text
        except Exception as exc:
            print(f"[DomainMail] fetch error: {exc}")
        return None


class DomainMailHTTPClient(_DomainAddressMixin):
    """通过自建 HTTP 拉信接口读取域名邮件。"""

    def __init__(
        self,
        domain: str,
        api_url: str,
        prefix: str = "grok",
        random_length: int = 10,
        timeout: int = 15,
        proxies: Any = None,
    ):
        self._configure_address(domain, prefix, random_length)
        if not api_url:
            raise RuntimeError("缺少 DOMAIN_MAIL_API_URL")
        self.api_url = api_url.strip()
        self.timeout = int(timeout or 15)
        self.session = requests.Session()
        if proxies:
            self.session.proxies.update(proxies)
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.debug = _env_truthy("DOMAIN_MAIL_DEBUG")

    def _build_url(self) -> str:
        encoded_mail = urllib.parse.quote(self.email, safe="")
        if "{mail}" in self.api_url:
            return self.api_url.replace("{mail}", encoded_mail)
        if "{}" in self.api_url:
            return self.api_url.format(encoded_mail)
        separator = "&" if "?" in self.api_url else "?"
        return f"{self.api_url}{separator}mail={encoded_mail}"

    def create_email(self) -> str:
        self.email = self._build_address()
        return self.email

    def fetch_first_email(self) -> Optional[str]:
        if not self.email:
            return None
        try:
            url = self._build_url()
            response = self.session.get(url, timeout=self.timeout)
            text = response.text or ""
            if self.debug:
                preview = text[:200].replace("\n", " ").replace("\r", " ")
                print(
                    f"[DomainMailHTTP] {self.email} GET {url} -> "
                    f"{response.status_code}, len={len(text)}, preview={preview!r}",
                    flush=True,
                )
            if response.status_code != 200:
                print(
                    f"[DomainMailHTTP] {self.email} 拉信接口非 200: "
                    f"{response.status_code}, body={text[:200]!r}",
                    flush=True,
                )
                return None
            return text if text.strip() and len(text) > 20 else None
        except Exception as exc:
            print(f"[DomainMailHTTP] fetch error: {exc}")
            return None


class EmailService:
    """自建域名邮箱门面，保持现有注册流程的调用接口不变。"""

    def __init__(self, proxies: Any = None, provider: str = "domain"):
        selected_provider = str(provider or "domain").strip().lower()
        if selected_provider != "domain":
            raise ValueError("仅支持自建域名邮箱 provider: domain")
        self.proxies = proxies

    def create_email(self):
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
            address = client.create_email()
            print(f"[+] 自建域名邮箱已创建: {address}")
            return {"provider": "domain", "client": client, "email": address}, address
        except Exception as exc:
            print(f"[Error] 自建域名邮箱出错: {exc}")
            return None, None

    @staticmethod
    def fetch_first_email(token_like):
        if not isinstance(token_like, dict) or token_like.get("provider") != "domain":
            return None
        client = token_like.get("client")
        return client.fetch_first_email() if client else None