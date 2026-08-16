import os
import time
import logging
import requests
from typing import Optional
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置日志记录器
logger = logging.getLogger(__name__)


# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TurnstileService:
    def __init__(self):
        # 读取 2Captcha 的密钥环境变量 (兼容 TWOCAPTCHA_KEY 和 TWOCAPTCHA_KEY)
        self.twocaptcha_key = os.getenv('TWOCAPTCHA_KEY', '').strip()
        # 2Captcha 的现代 JSON API 接口
        self.api_base = "https://api.2captcha.com"
        self.timeout = float(os.getenv("TWOCAPTCHA_TIMEOUT", "20"))

        # 使用 Session 复用 TCP 连接
        self.session = requests.Session()

    def create_task(self, siteurl: str, sitekey: str) -> str:
        """
        创建 2Captcha 打码任务
        :return: 成功返回 taskId
        """
        if not self.twocaptcha_key:
            raise ValueError("缺少 TWOCAPTCHA_KEY，无法创建任务")

        url = f"{self.api_base}/createTask"
        payload = {
            "clientKey": self.twocaptcha_key,
            "task": {
                # 2Captcha 中对于 Cloudflare Turnstile (无代理模式) 的类型声明
                "type": "TurnstileTaskProxyless",
                "websiteURL": siteurl,
                "websiteKey": sitekey
            }
            # 注：如果你的 2Captcha 账号有开发者 softId，可以在此处添加
            # "softId": 1234
        }

        response = self.session.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()

        data = response.json()
        if data.get('errorId') != 0:
            raise RuntimeError(f"2Captcha创建任务失败: {data.get('errorDescription', '未知错误')}")

        return data['taskId']

    def get_response(self, task_id: str, max_retries: int = 30, initial_delay: int = 5, retry_delay: int = 2) -> \
    Optional[str]:
        """
        轮询获取 2Captcha 打码结果
        :return: 成功返回 token，失败或超时返回 None
        """
        if not self.twocaptcha_key:
            raise ValueError("缺少 TWOCAPTCHA_KEY，无法获取结果")

        # Turnstile 验证码通常需要几秒钟时间解决，初始等待可以减少无效请求
        time.sleep(initial_delay)

        for attempt in range(max_retries):
            try:
                url = f"{self.api_base}/getTaskResult"
                payload = {
                    "clientKey": self.twocaptcha_key,
                    "taskId": task_id
                }

                response = self.session.post(url, json=payload, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()

                if data.get('errorId') != 0:
                    logger.error(f"2Captcha获取结果失败: {data.get('errorDescription', '未知错误')}")
                    return None

                status = data.get('status')
                if status == 'ready':
                    token = data.get('solution', {}).get('token')
                    if token:
                        return token
                    logger.warning("2Captcha返回结果中没有token")
                    return None

                elif status == 'processing':
                    # 正在处理中，继续等待
                    time.sleep(retry_delay)

                else:
                    logger.warning(f"2Captcha未知状态: {status}")
                    time.sleep(retry_delay)

            except requests.RequestException as e:
                logger.error(f"网络请求异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                time.sleep(retry_delay)
            except Exception as e:
                logger.error(f"获取Turnstile响应发生未知异常: {e}")
                time.sleep(retry_delay)

        logger.error("达到最大重试次数，获取 2Captcha 响应超时")
        return None