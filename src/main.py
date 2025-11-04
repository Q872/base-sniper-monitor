import os
import sys
import time
import json
import asyncio
import random
import requests
from datetime import datetime
from typing import Dict, Any, List

# ==============================================================
# 环境变量读取（从 GitHub Secrets 自动注入）
# ==============================================================

BOT_TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
)
CHAT_ID = (
    os.getenv("CHANNEL_ID")
    or os.getenv("TELEGRAM_CHAT_ID")
)

if not BOT_TOKEN or not CHAT_ID:
    print("❌ 未配置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，请检查 GitHub Secrets。")
    sys.exit(1)

# ==============================================================
# 参数配置
# ==============================================================

SCAN_INTERVAL = 60  # 每60秒扫描一次
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "10000"))
API_KEY = os.getenv("BASESCAN_API_KEY", "")

DEX_API = "https://api.dexscreener.com/latest/dex/tokens"
RISK_THRESHOLD_HIGH = 13
RISK_THRESHOLD_MEDIUM = 6

# ==============================================================
# 日志模块
# ==============================================================

class Logger:
    COLORS = {
        "INFO": "\033[92m",
        "WARN": "\033[93m",
        "ERR": "\033[91m",
        "RESET": "\033[0m",
    }

    @staticmethod
    def log(level: str, message: str):
        color = Logger.COLORS.get(level, "")
        reset = Logger.COLORS["RESET"]
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{color}[{now}] [{level}] {message}{reset}")

    @staticmethod
    def info(msg): Logger.log("INFO", msg)
    @staticmethod
    def warn(msg): Logger.log("WARN", msg)
    @staticmethod
    def err(msg):  Logger.log("ERR", msg)


# ==============================================================
# Telegram 通知模块
# ==============================================================

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send(self, text: str, parse_mode: str = "HTML"):
        try:
            payload = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}
            resp = requests.post(self.url, json=payload, timeout=15)
            if resp.status_code != 200:
                Logger.warn(f"Telegram 推送失败: {resp.text}")
        except Exception as e:
            Logger.err(f"发送 Telegram 消息失败: {e}")

notifier = TelegramNotifier(BOT_TOKEN, CHAT_ID)


# ==============================================================
# 数据请求与错误重试
# ==============================================================

def safe_request(url: str, retries: int = 3, delay: float = 1.5) -> Any:
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            else:
                Logger.warn(f"请求失败 {r.status_code}: {url}")
        except Exception as e:
            Logger.warn(f"请求异常: {e}")
        time.sleep(delay)
    return None


# ==============================================================
# 获取流动性与DEX信息
# ==============================================================

class LiquidityChecker:
    @staticmethod
    def get_liquidity_usd(token_address: str) -> float:
        """此处使用 DexScreener 模拟，可接真实API"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            data = safe_request(url)
            if not data or "pairs" not in data:
                return 0
            pairs = data["pairs"]
            if not pairs:
                return 0
            liquidity = float(pairs[0].get("liquidity", {}).get("usd", 0))
            return liquidity
        except Exception as e:
            Logger.warn(f"流动性获取失败: {e}")
            return 0

# ==============================================================
# 创建者行为分析模块（撤池检测 / v3行为）
# ==============================================================

class CreatorAnalyzer:
    BASESCAN_API = "https://api.basescan.org/api"

    @staticmethod
    def check_creator_info(creator_address: str) -> Dict[str, Any]:
        """
        检查创建者地址是否为新钱包、余额、最近交易、是否有撤池行为。
        """
        result = {
            "is_new": False,
            "eth_balance": 0,
            "last_txs": [],
            "rug_history": False,
        }

        try:
            # 获取余额
            balance_url = f"{CreatorAnalyzer.BASESCAN_API}?module=account&action=balance&address={creator_address}&tag=latest"
            balance_data = safe_request(balance_url)
            if balance_data and balance_data.get("status") == "1":
                result["eth_balance"] = round(int(balance_data["result"]) / 1e18, 5)

            # 获取最近5笔交易
            tx_url = f"{CreatorAnalyzer.BASESCAN_API}?module=account&action=txlist&address={creator_address}&page=1&offset=5&sort=desc"
            tx_data = safe_request(tx_url)
            if tx_data and tx_data.get("status") == "1":
                result["last_txs"] = tx_data["result"]

                # 检查是否为新地址
                first_tx_time = int(tx_data["result"][-1]["timeStamp"])
                age_days = (datetime.utcnow() - datetime.utcfromtimestamp(first_tx_time)).days
                result["is_new"] = age_days <= 7

            # 检测是否有撤池行为（模拟检测 v3 Router）
            if CreatorAnalyzer._check_rug_v3_activity(creator_address):
                result["rug_history"] = True

        except Exception as e:
            Logger.warn(f"创建者分析失败: {e}")

        return result

    @staticmethod
    def _check_rug_v3_activity(address: str) -> bool:
        """
        检测用户是否与 V3 Router 有撤池记录。
        """
        try:
            # 模拟检测Base链V3路由合约交互
            v3_router = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
            tx_url = f"{CreatorAnalyzer.BASESCAN_API}?module=account&action=txlist&address={address}&page=1&offset=30&sort=desc"
            data = safe_request(tx_url)
            if not data or data.get("status") != "1":
                return False
            for tx in data["result"]:
                to_addr = tx.get("to", "").lower()
                method = tx.get("methodId", "")
                if to_addr == v3_router.lower() and method.startswith("0x"):
                    # 粗略匹配 removeLiquidity 或类似操作
                    if "remove" in tx.get("functionName", "").lower():
                        return True
            return False
        except Exception as e:
            Logger.warn(f"撤池检测错误: {e}")
            return False


# ==============================================================
# 风险评分系统
# ==============================================================

class RiskAnalyzer:
    """
    代币风险评分模型
    - 检查开源状态
    - 检查创建者撤池史
    - 检查流动性
    - 检查创建者是否新钱包
    - 检查社交信息完整性
    """

    @staticmethod
    def analyze(token_info: Dict[str, Any], creator_info: Dict[str, Any]) -> Dict[str, Any]:
        score = 0
        reasons = []

        # 1️⃣ 开源状态
        if token_info.get("is_open_source"):
            score += 2
        else:
            score += 5
            reasons.append("🔒 未开源")

        # 2️⃣ 创建者是否新钱包
        if creator_info.get("is_new"):
            score += 3
            reasons.append("🧧 创建者是新钱包")

        # 3️⃣ 是否有撤池历史
        if creator_info.get("rug_history"):
            score += 6
            reasons.append("💀 创建者曾撤池")

        # 4️⃣ 流动性检测
        liq = token_info.get("liquidity_usd", 0)
        if liq < 10000:
            score += 2
            reasons.append("💧 流动性不足")

        # 5️⃣ 社交信息完整度
        if not token_info.get("telegram") and not token_info.get("website"):
            score += 3
            reasons.append("❌ 无官网或TG")

        # 6️⃣ 代币是否疑似蜜罐
        if token_info.get("is_honeypot"):
            score += 8
            reasons.append("🚫 疑似蜜罐")

        # 风险等级
        if score > RISK_THRESHOLD_HIGH:
            level = "🔥 高危"
        elif score >= RISK_THRESHOLD_MEDIUM:
            level = "⚠️ 中风险"
        else:
            level = "✅ 优质"

        return {
            "score": score,
            "level": level,
            "reasons": reasons,
        }
# ==============================================================
# 代币扫描器（核心逻辑）
# ==============================================================

class TokenScanner:
    """
    扫描 Base 链新创建的流动性池或新代币。
    """

    BASESCAN_API = "https://api.basescan.org/api"

    @staticmethod
    def get_new_tokens(limit: int = 5) -> List[Dict[str, Any]]:
        """
        获取最新创建的代币（模拟）。
        实际使用中建议接入 DEX Screener 或 DEXTools 订阅 API。
        """
        try:
            # 示例数据（模拟返回）
            mock_data = [
                {
                    "address": f"0x{random.randint(10**38, 10**39-1):x}",
                    "symbol": "TEST",
                    "name": "Example Token",
                    "website": "https://example.org",
                    "telegram": "https://t.me/example",
                    "is_open_source": bool(random.getrandbits(1)),
                    "is_honeypot": bool(random.getrandbits(1)),
                    "creator": f"0x{random.randint(10**38, 10**39-1):x}",
                }
                for _ in range(limit)
            ]
            return mock_data
        except Exception as e:
            Logger.err(f"代币扫描失败: {e}")
            return []


# ==============================================================
# 主控制器
# ==============================================================

class TokenMonitor:
    def __init__(self):
        self.notifier = notifier
        self.checked_tokens = set()

    def format_message(self, token_info: Dict[str, Any], risk: Dict[str, Any], creator_info: Dict[str, Any]) -> str:
        """
        生成 Telegram 消息。
        """
        msg = f"<b>{token_info['name']} ({token_info['symbol']})</b>\n"
        msg += f"🌐 <b>地址:</b> <code>{token_info['address']}</code>\n"
        msg += f"💰 <b>流动性:</b> ${token_info.get('liquidity_usd', 0):,.2f}\n"
        msg += f"⚙️ <b>风险等级:</b> {risk['level']} ({risk['score']}分)\n\n"

        # 社交信息
        socials = []
        if token_info.get("website"): socials.append(f"<a href='{token_info['website']}'>官网</a>")
        if token_info.get("telegram"): socials.append(f"<a href='{token_info['telegram']}'>Telegram</a>")
        if socials:
            msg += "🔗 " + " | ".join(socials) + "\n"

        # 原因列表
        if risk["reasons"]:
            msg += "\n❗ <b>风险因素:</b>\n" + "\n".join([f" - {r}" for r in risk["reasons"]])

        # 创建者信息
        msg += "\n\n👤 <b>创建者分析</b>\n"
        msg += f" - 地址: <code>{token_info['creator']}</code>\n"
        msg += f" - ETH余额: {creator_info.get('eth_balance', 0)}\n"
        msg += f" - 是否新钱包: {'✅' if creator_info.get('is_new') else '❌'}\n"
        msg += f" - 曾撤池: {'💀是' if creator_info.get('rug_history') else '🚫否'}\n"

        msg += "\n\n⏰ 扫描时间: " + datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        return msg

    async def run_once(self):
        Logger.info("开始扫描 Base 链新代币...")

        new_tokens = TokenScanner.get_new_tokens(limit=5)
        if not new_tokens:
            Logger.warn("暂无新代币。")
            return

        for token in new_tokens:
            if token["address"] in self.checked_tokens:
                continue

            Logger.info(f"检测到新代币: {token['symbol']} ({token['address']})")

            # 获取流动性
            liq = LiquidityChecker.get_liquidity_usd(token["address"])
            token["liquidity_usd"] = liq
            if liq < MIN_LIQUIDITY_USD:
                Logger.warn(f"{token['symbol']} 流动性过低 (${liq:.2f})，跳过。")
                continue

            # 分析创建者
            creator_info = CreatorAnalyzer.check_creator_info(token["creator"])

            # 风险分析
            risk = RiskAnalyzer.analyze(token, creator_info)

            # 判断是否推送
            if risk["score"] > RISK_THRESHOLD_HIGH:
                Logger.warn(f"{token['symbol']} 高危项目（{risk['score']}分）⚠️")
            elif risk["score"] >= RISK_THRESHOLD_MEDIUM:
                Logger.info(f"{token['symbol']} 中风险项目（{risk['score']}分）")
            else:
                Logger.info(f"{token['symbol']} 优质项目（{risk['score']}分）")

            # ⚙️ 推送逻辑（仅中风险以下、流动性达标的代币）
            if risk["score"] <= RISK_THRESHOLD_HIGH:
                msg = self.format_message(token, risk, creator_info)
                self.notifier.send(msg)

            self.checked_tokens.add(token["address"])

        Logger.info("本轮扫描完成。")


# ==============================================================
# 程序主入口
# ==============================================================

async def main():
    monitor = TokenMonitor()

    while True:
        try:
            await monitor.run_once()
        except Exception as e:
            Logger.err(f"运行错误: {e}")

        Logger.info(f"等待 {SCAN_INTERVAL} 秒后继续...")
        await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        Logger.warn("已手动终止运行。")
# ==============================================================
# 运行适配：GitHub Actions / 云端 VPS 自动化支持
# ==============================================================

"""
⚙️ GitHub Actions 中运行说明：

- 运行脚本位于 .github/workflows/monitor.yml
- 会每 5 分钟触发一次：
  schedule:
    - cron: "*/5 * * * *"

- 环境变量由 GitHub Secrets 注入：
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

- 若使用 VPS 运行，可直接执行：
  python3 src/main.py

"""

import signal

class GracefulKiller:
    """安全终止（避免 GitHub Actions 任务被中断后报错）"""
    kill_now = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, *args):
        self.kill_now = True


async def run_monitor_loop():
    monitor = TokenMonitor()
    killer = GracefulKiller()

    while not killer.kill_now:
        try:
            await monitor.run_once()
        except Exception as e:
            Logger.err(f"循环异常: {e}")

        # 在 Actions 中随机延迟，防止 API 封禁
        delay = SCAN_INTERVAL + random.randint(-10, 20)
        Logger.info(f"下轮扫描将在 {delay} 秒后进行")
        await asyncio.sleep(max(30, delay))


# ==============================================================
# 主程序入口（带异常保护）
# ==============================================================

if __name__ == "__main__":
    try:
        Logger.info("🚀 Base Meme Token Monitor 启动中 ...")
        asyncio.run(run_monitor_loop())
    except KeyboardInterrupt:
        Logger.warn("程序手动终止。")
    except Exception as e:
        Logger.err(f"运行终止：{e}")
    finally:
        Logger.info("🛑 已安全退出。")


# ==============================================================
# 开发者调试模式（本地运行）
# ==============================================================

"""
💡 调试建议：
1️⃣ 运行：python3 src/main.py
2️⃣ 检查是否能成功推送 Telegram
3️⃣ 查看输出日志，确认扫描循环正常
4️⃣ 确保 GitHub Secrets 名称为：
     - TELEGRAM_BOT_TOKEN
     - TELEGRAM_CHAT_ID
5️⃣ Actions 可无限使用（公共仓库），自动定时运行。
"""

Logger.info("✅ main.py 已加载完成（所有模块就绪）。")
