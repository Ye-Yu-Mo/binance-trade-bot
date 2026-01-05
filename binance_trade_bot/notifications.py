"""
邮件通知模块 - 使用标准库 smtplib
替换 apprise，更简单可靠
"""
import queue
import smtplib
import ssl
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from os import path
import configparser


class NotificationHandler:
    def __init__(self, enabled=True):
        self.enabled = False

        if not enabled:
            return

        # 尝试从 config/email.ini 读取配置
        config_path = "config/email.ini"
        if not path.exists(config_path):
            print(f"邮件通知未启用: 配置文件不存在 {config_path}")
            return

        try:
            config = configparser.ConfigParser()
            config.read(config_path)

            self.smtp_server = config.get('smtp', 'server')
            self.smtp_port = config.getint('smtp', 'port')
            self.sender_email = config.get('smtp', 'sender')
            self.password = config.get('smtp', 'password')
            self.receiver_email = config.get('smtp', 'receiver')

            # 测试连接
            self._test_connection()

            # 创建发送队列
            self.queue = queue.Queue()
            self.start_worker()
            self.enabled = True

            print(f"✓ 邮件通知已启用: {self.receiver_email}")

        except Exception as e:
            print(f"邮件通知配置失败: {e}")
            self.enabled = False

    def _test_connection(self):
        """测试 SMTP 连接"""
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, context=context, timeout=5) as server:
            server.login(self.sender_email, self.password)

    def start_worker(self):
        """启动异步发送线程"""
        threading.Thread(target=self.process_queue, daemon=True).start()

    def process_queue(self):
        """处理发送队列"""
        import time
        while True:
            message = self.queue.get()
            try:
                self._send_email(message)
                # 成功后等待1秒，避免频率限制
                time.sleep(1)
            except Exception as e:
                # 失败后等待更长时间再重试
                try:
                    time.sleep(3)
                    self._send_email(message)
                    time.sleep(1)
                except Exception as retry_error:
                    # 静默失败，不影响其他消息
                    pass
            finally:
                self.queue.task_done()

    def _send_email(self, message):
        """实际发送邮件"""
        try:
            context = ssl.create_default_context()

            # 创建邮件
            msg = MIMEMultipart()
            msg['From'] = f"Binance Trade Bot <{self.sender_email}>"
            msg['To'] = self.receiver_email
            msg['Subject'] = f"[Binance Bot] {datetime.now().strftime('%H:%M:%S')}"

            msg.attach(MIMEText(message, 'plain', 'utf-8'))

            # 发送
            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, context=context, timeout=15) as server:
                server.login(self.sender_email, self.password)
                server.send_message(msg)
        except Exception as e:
            # 重新抛出异常，让上层处理重试
            raise e

    def send_notification(self, message, attachments=None):
        """发送通知（保持接口兼容）"""
        if self.enabled:
            # 忽略 attachments 参数（暂不支持附件）
            self.queue.put(message)
