"""通过自建 HTTP 接口拉取域名邮箱邮件。"""

import os
import random
import re
import string
import urllib.parse
from typing import Any, Optional

import requests


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _domain_mail_settings() -> dict:
    """读取自建域名邮箱 HTTP 接口配置。"""
    return {
        "mode": str(os.getenv("DOMAIN_MAIL_MODE") or "random").strip().lower(),
        "domain": str(os.getenv("DOMAIN_MAIL_DOMAIN") or "").strip().lstrip("@"),
        "api_url": str(os.getenv("DOMAIN_MAIL_API_URL") or "").strip(),
        "api_key": str(os.getenv("DOMAIN_MAIL_API_KEY") or "").strip(),
        "api_timeout": int(str(os.getenv("DOMAIN_MAIL_API_TIMEOUT") or "15").strip() or "15"),
        "prefix": str(os.getenv("DOMAIN_MAIL_PREFIX") or "grok").strip() or "grok",
        "random_length": int(str(os.getenv("DOMAIN_MAIL_RANDOM_LENGTH") or "10").strip() or "10"),
    }


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
        mode: str = "random",
        api_key: str = "",
    ):
        self.mode = mode
        self.api_key = api_key
        if self.mode == "random":
            self._configure_address(domain, prefix, random_length)
        else:
            self.email = ""
            
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
        if self.mode == "api":
            if not self.api_key:
                raise RuntimeError("缺少 DOMAIN_MAIL_API_KEY")
            
            # 使用 拉信接口 URL + api_key 自动拼接成完整的生成邮箱 URL
            separator = "&" if "?" in self.api_url else "?"
            generate_api_url = f"{self.api_url}{separator}api_key={self.api_key}"
            
            response = self.session.get(generate_api_url, timeout=self.timeout)
            if response.status_code != 200:
                raise RuntimeError(f"获取邮箱失败, HTTP 状态码: {response.status_code}, 内容: {response.text[:200]}")
            data = response.json()
            self.email = data.get("email") or data.get("mail")
            if not self.email:
                raise RuntimeError("API 返回结果中缺少邮箱地址")
            return self.email
        else:
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
            client = DomainMailHTTPClient(
                domain=settings["domain"],
                api_url=settings["api_url"],
                prefix=settings["prefix"],
                random_length=settings["random_length"],
                timeout=settings["api_timeout"],
                proxies=self.proxies,
                mode=settings["mode"],
                api_key=settings["api_key"],
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