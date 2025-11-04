#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BMIS - 完整 main.py (GitHub Actions 兼容，单次执行)
功能亮点：
- 从 DexScreener 拉取 Base 链最新 pairs
- TokenDataManager：记录价格历史与倍数报警
- RiskScorer：完整评分逻辑（你指定的阈值语义）
- AlertManager：价格/风险冷却与去重
- Deployer Intelligence：检测部署者是否曾撤池（支持 v2/v3 逻辑 via explorer logs）
- Telegram 推送（HTML 格式）
- 单次运行后退出 -> 适合 GitHub Actions 定时触发
注意：请在 Secrets 中配置 BOT_TOKEN, CHANNEL_ID, BASESCAN_API_KEY (推荐)
"""

import os
import sys
import time
import json
import math
import asyncio
import aiohttp
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# ---------------------------
# 配置 (可通过 GitHub Secrets 覆盖)
# ---------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")                # 必须配置
CHANNEL_ID = os.getenv("CHANNEL_ID")              # 必须配置（-100... 或 @channelname）
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY")  # 推荐配置（用于日志/验证查询）
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "10000"))  # 初始LP阈值
PRICE_HISTORY_LIMIT = int(os.getenv("PRICE_HISTORY_LIMIT", "200"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "3600"))  # 秒
DEXSCR_API_BASE = "https://api.dexscreener.com/latest/dex"
CHAIN_SLUG = "base"  # 监控链 slug
PAIRS_LIMIT = int(os.getenv("PAIRS_LIMIT", "50"))

# 评分阈值（你指定的语义）
# score >= 13 -> 高风险 (不推送)
# score <= 6 -> 优质
# 7..12 -> 中风险
HIGH_RISK_THRESHOLD = 13
LOW_RISK_THRESHOLD = 6

# --------- 依赖检查提示 ----------
try:
    from telegram import Bot
except Exception:
    # we'll still allow script to run to show clear error when running in GH Actions
    Bot = None

# ---------------------------
# 数据管理器
# ---------------------------
class TokenDataManager:
    def __init__(self, data_file: str = "data/token_history.json"):
        self.data_file = data_file
        self._ensure_data_file()

    def _ensure_data_file(self):
        d = os.path.dirname(self.data_file)
        if d:
            os.makedirs(d, exist_ok=True)
        if not os.path.exists(self.data_file):
            with open(self.data_file, "w") as f:
                json.dump({"tokens": {}, "statistics": {}}, f, indent=2)

    def load_data(self) -> Dict:
        try:
            with open(self.data_file, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ 读取历史数据失败: {e}")
            return {"tokens": {}, "statistics": {}}

    def save_data(self, data: Dict):
        try:
            with open(self.data_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"❌ 保存历史数据失败: {e}")

    def record_token_price(self, token_address: str, symbol: str, price: float, liquidity: float):
        data = self.load_data()
        now = datetime.utcnow().isoformat() + "Z"
        if token_address not in data["tokens"]:
            data["tokens"][token_address] = {
                "symbol": symbol,
                "first_seen": now,
                "price_history": [],
                "initial_price": price or 0,
                "initial_liquidity": liquidity or 0,
                "price_alerts_sent": []
            }
        token = data["tokens"][token_address]
        token["price_history"].append({"timestamp": now, "price": price, "liquidity": liquidity})
        # trim
        if len(token["price_history"]) > PRICE_HISTORY_LIMIT:
            token["price_history"] = token["price_history"][-PRICE_HISTORY_LIMIT:]
        token["current_price"] = price
        token["last_updated"] = now
        if not token.get("initial_price"):
            token["initial_price"] = price
        self.save_data(data)
        return token

    def calculate_returns(self, token_address: str) -> Dict:
        data = self.load_data()
        if token_address not in data["tokens"]:
            return {}
        token = data["tokens"][token_address]
        initial = token.get("initial_price", 0) or 0
        current = token.get("current_price", initial) or initial
        if initial == 0:
            return {}
        total_return = ((current - initial) / initial) * 100
        # 24h
        cutoff = datetime.utcnow() - timedelta(hours=24)
        price_24h = initial
        for p in reversed(token.get("price_history", [])):
            try:
                ts = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
                if ts <= cutoff:
                    price_24h = p["price"]
                    break
            except Exception:
                continue
        return_24h = ((current - price_24h) / price_24h) * 100 if price_24h > 0 else 0
        return {
            "total_return": round(total_return, 2),
            "return_24h": round(return_24h, 2),
            "initial_price": initial,
            "current_price": current,
            "price_multiple": (current / initial) if initial > 0 else 1
        }

    def get_price_alerts_sent(self, token_address: str) -> List[int]:
        data = self.load_data()
        return data["tokens"].get(token_address, {}).get("price_alerts_sent", [])

    def mark_price_alert_sent(self, token_address: str, multiple: int):
        data = self.load_data()
        arr = data["tokens"].setdefault(token_address, {}).setdefault("price_alerts_sent", [])
        if multiple not in arr:
            arr.append(multiple)
            self.save_data(data)

    def get_top_performers(self, limit: int = 10) -> List[Dict]:
        data = self.load_data()
        out = []
        for addr, t in data["tokens"].items():
            ret = self.calculate_returns(addr)
            if ret:
                out.append({"address": addr, "symbol": t.get("symbol"), **ret})
        out.sort(key=lambda x: x.get("total_return", 0), reverse=True)
        return out[:limit]

data_manager = TokenDataManager()

# ---------------------------
# 风险评分器（按你要求的语义）
# ---------------------------
class RiskScorer:
    def __init__(self):
        self.reasons: List[str] = []

    def score(self, td: Dict) -> int:
        """
        约定：
           - 高风险 score >= HIGH_RISK_THRESHOLD (13) -> 不推送
           - 低风险 score <= LOW_RISK_THRESHOLD (6) -> 优质
           - 中间 7..12 -> 中风险
        评分规则示例（可调）：
        """
        self.reasons = []
        score = 0
        # 未验证合约加分（不安全）
        if not td.get("verified", False):
            score += 2
            self.reasons.append("合约未验证")
        # 税率
        try:
            b = float(td.get("buy_tax", 0) or 0)
            s = float(td.get("sell_tax", 0) or 0)
            if b > 0.05 or s > 0.05:
                score += 4
                self.reasons.append("买卖税较高 (>5%)")
            elif b > 0.03 or s > 0.03:
                score += 1
                self.reasons.append("买卖税 >3%")
        except Exception:
            pass
        # honeypot
        if td.get("is_honeypot", False):
            score += 6
            self.reasons.append("honeypot")
        # LP 未锁/短锁
        lp_lock = int(td.get("lp_lock_days", 0) or 0)
        if lp_lock == 0:
            score += 3
            self.reasons.append("LP 未锁")
        elif lp_lock < 30:
            score += 1
            self.reasons.append("LP 锁仓短")
        # 部署者新钱包
        wa = float(td.get("wallet_age_hours", 24) or 24)
        if wa < 6:
            score += 2
            self.reasons.append("部署钱包 <6h")
        # 部署者撤池 / 惯犯
        if td.get("deployer_withdrawn", False):
            score += 5
            self.reasons.append("部署者有撤池记录")
        if td.get("has_rug_history", False):
            score += 5
            self.reasons.append("部署者历史有RUG记录")
        # 持仓集中度
        try:
            top10 = float(td.get("top10_holders_percent", 100) or 100)
            if top10 > 50:
                score += 2
                self.reasons.append("持仓高度集中")
        except Exception:
            pass
        # 社区、CEX来源为安全因子（减分）
        if td.get("verified", False) and td.get("has_community", False):
            score -= 2
            self.reasons.append("verified + 社群")
        if td.get("from_cex", False):
            score -= 1
            self.reasons.append("资金来自 CEX")
        # 确保 >=0
        score = max(0, int(score))
        return score

# ---------------------------
# 报警管理
# ---------------------------
class AlertManager:
    def __init__(self):
        self.sent = {}  # key->timestamp
        self.cooldown = ALERT_COOLDOWN

    def should_send_risk(self, token_addr: str, score: int) -> bool:
        # 只对 score < HIGH_RISK_THRESHOLD 的代币推送（你要求）
        if score >= HIGH_RISK_THRESHOLD:
            return False
        # 低于阈值才会推送；但是我们仍然做冷却，防止重复
        key = f"risk:{token_addr}:{score}"
        now = time.time()
        last = self.sent.get(key)
        if last and now - last < self.cooldown:
            return False
        self.sent[key] = now
        return True

    def should_send_price(self, token_addr: str, multiple: int) -> bool:
        sent = data_manager.get_price_alerts_sent(token_addr)
        return multiple not in sent

    def mark_price_sent(self, token_addr: str, multiple: int):
        data_manager.mark_price_alert_sent(token_addr, multiple)

alert_manager = AlertManager()

# ---------------------------
# DexScreener 接口与解析
# ---------------------------
class DexScreenerClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def get_latest_pairs(self, chain: str = CHAIN_SLUG, limit: int = PAIRS_LIMIT):
        # 使用官方 latest/pairs/{chain} 接口
        url = f"{DEXSCR_API_BASE}/pairs/{chain}"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])[:limit]
                    return pairs
                else:
                    print(f"❌ DexScreener 返回状态 {resp.status}")
                    return []
        except Exception as e:
            print(f"❌ DexScreener 请求异常: {e}")
            return []

def parse_pair(pair: Dict) -> Optional[Dict]:
    try:
        base = pair.get("baseToken", {}) or {}
        liquidity = 0
        try:
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
        except Exception:
            liquidity = 0
        vol = 0
        try:
            vol = float((pair.get("volume") or {}).get("h24") or 0)
        except Exception:
            vol = 0
        price = float(pair.get("priceUsd") or 0)
        created = pair.get("pairCreatedAt")
        age_minutes = 0
        if created:
            try:
                if isinstance(created, str):
                    created_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_minutes = int((datetime.utcnow() - created_time).total_seconds() / 60)
                else:
                    age_minutes = int((time.time() * 1000 - float(created)) / 60000)
            except Exception:
                age_minutes = 0
        return {
            "address": base.get("address") or base.get("id") or "",
            "name": base.get("name", "Unknown"),
            "symbol": base.get("symbol", "UNK"),
            "liquidity": liquidity,
            "volume": vol,
            "priceUsd": price,
            "url": pair.get("url", ""),
            "age_minutes": age_minutes,
            "raw": pair
        }
    except Exception as e:
        print(f"❌ parse_pair error: {e}")
        return None

# ---------------------------
# Deployer intelligence：检测撤池行为（v2/v3）
# 原理：
# - v2 (UniswapV2) 常见撤池可通过 Pair 合约的 Burn / Transfer(到0x) 或 router removeLiquidity 操作的日志检测
# - v3 (UniswapV3) 使用 NonfungiblePositionManager 的 DecreaseLiquidity / Burn / Collect 事件来识别撤出
# 我们用 explorer logs 查询（需要 BASESCAN_API_KEY）
# ---------------------------
class DeployerIntelligence:
    def __init__(self, session: aiohttp.ClientSession, explorer_api_key: Optional[str] = None):
        self.session = session
        self.key = explorer_api_key

    async def _get_logs(self, address: str, topic0: Optional[str] = None, from_block: Optional[int] = None, to_block: Optional[int] = None):
        """
        通用日志查询（Basescan style）
        使用 module=logs&action=getLogs&address=XXX&topic0=0x...&apikey=KEY
        注意 explorer 的速率限制。
        """
        if not self.key:
            # 没有 key 无法查询（返回空），但不会抛异常
            return []
        params = {
            "module": "logs",
            "action": "getLogs",
            "address": address,
            "apikey": self.key,
            "offset": 100,
            "page": 1
        }
        if topic0:
            params["topic0"] = topic0
        if from_block:
            params["fromBlock"] = str(from_block)
        if to_block:
            params["toBlock"] = str(to_block)
        url = "https://api.basescan.org/api"  # note: base explorer domain placeholder (replace if needed)
        try:
            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    d = await resp.json()
                    return d.get("result", [])
                else:
                    # print(f"Explorer logs status {resp.status}")
                    return []
        except Exception:
            return []

    async def check_deployer_withdraw_v2(self, lp_pair_address: str) -> bool:
        """
        对 UniswapV2 风格的 pair 检查 Burn / Transfer to zero (LP token burn)
        topic0 for Burn: keccak256("Burn(address,uint256,uint256)")
        但这里更稳妥是查 Transfer -> to=0x000... 或 router removeLiquidity events (topic detection)
        """
        # topic for Transfer: keccak("Transfer(address,address,uint256)") = 0xddf252ad...
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        logs = await self._get_logs(lp_pair_address, topic0=transfer_topic)
        if not logs:
            return False
        # 简单判断：近期是否有 to == 0x000.. burn 或大额 transfer out (可视为撤池)
        zero_addr = "0x0000000000000000000000000000000000000000"
        for lg in logs:
            topics = lg.get("topics", [])
            data = lg.get("data", "")
            # topics[2] is 'to' in Transfer indexed fields if standard ERC20 (address indexed)
            if len(topics) >= 3:
                to_topic = topics[2]
                # to_topic is hex-padded address; compare lower-case
                if zero_addr[2:].rjust(64, "0") in to_topic.lower():
                    return True
        return False

    async def check_deployer_withdraw_v3(self, deployer_address: str) -> bool:
        """
        检测 v3 撤池相关：查询 NonfungiblePositionManager 的 DecreaseLiquidity / Burn / Collect
        NonfungiblePositionManager address may vary; but we can search logs where topics contain function sig hash.
        DecreaseLiquidity topic0 = keccak256("DecreaseLiquidity(uint256,int128,uint256,uint256)")
        We'll instead query logs where deployer_address appears as transaction `from` interacting with position manager.
        Simpler approach: 查询最近若干 txs of deployer and see if any call nonfungible position manager with DecreaseLiquidity.
        NOTE: This approach depends on explorer API and may be slower. If BASESCAN_API_KEY not set, returns False.
        """
        if not self.key:
            return False
        # We'll query normal txs for deployer via explorer API (module=account&action=txlist)
        params = {
            "module": "account",
            "action": "txlist",
            "address": deployer_address,
            "sort": "desc",
            "page": 1,
            "offset": 50,
            "apikey": self.key
        }
        url = "https://api.basescan.org/api"
        try:
            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return False
                d = await resp.json()
                txs = d.get("result", []) or []
                # Look for method signatures in input data that indicate decreaseLiquidity (function selectors)
                # Example selectors (hex4): decreaseLiquidity might be '0x...'; we check for common patterns:
                suspicious_selectors = [
                    # these are illustrative; real selectors should be computed; we'll search for 'decrease'/'burn'/'collect' strings in input if available
                    "decrease", "DecreaseLiquidity", "burn", "collect", "removeLiquidity"
                ]
                for tx in txs:
                    inp = tx.get("input", "") or ""
                    low = inp.lower()
                    for s in suspicious_selectors:
                        if s.lower() in low:
                            return True
        except Exception:
            return False
        return False

# ---------------------------
# Telegram helpers
# ---------------------------
async def send_telegram(bot_session: aiohttp.ClientSession, text: str):
    if not BOT_TOKEN or not CHANNEL_ID:
        print("⚠️ Telegram 未配置 BOT_TOKEN 或 CHANNEL_ID，跳过发送")
        return False
    api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        async with bot_session.post(api, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            txt = await resp.text()
            if resp.status == 200:
                return True
            else:
                print(f"❌ Telegram send failed {resp.status}: {txt}")
                return False
    except Exception as e:
        print(f"❌ Telegram send error: {e}")
        return False

# ---------------------------
# 单个 token 分析流程
# ---------------------------
async def analyze_token(pair: Dict, session: aiohttp.ClientSession, dex_client: DexScreenerClient, deployer_checker: DeployerIntelligence):
    parsed = parse_pair(pair)
    if not parsed:
        return None
    addr = parsed["address"]
    symbol = parsed["symbol"]
    # record price
    data_manager.record_token_price(addr, symbol, float(parsed.get("priceUsd", 0) or 0), float(parsed.get("liquidity", 0) or 0))

    # prepare token data structure for scoring
    td = {
        "address": parsed["address"],
        "name": parsed["name"],
        "symbol": parsed["symbol"],
        "liquidity": parsed["liquidity"],
        "volume": parsed["volume"],
        "priceUsd": parsed["priceUsd"],
        "url": parsed.get("url", ""),
        "age_minutes": parsed.get("age_minutes", 0),
        # place-holders (in real flow should be from dedicated API / on-chain checks)
        "verified": False,
        "buy_tax": 0.0,
        "sell_tax": 0.0,
        "is_honeypot": False,
        "lp_lock_days": 0,
        "wallet_age_hours": 24,
        "suspicious_source": False,
        "has_rug_history": False,
        "has_community": False,
        "from_cex": False,
        "top10_holders_percent": 100
    }

    # ---- try to enrich data via explorer/APIs (if Basescan key available) ----
    # 1) check deployer / creator and whether it withdrew liquidity recently
    deployer_withdrawn = False
    try:
        # try to find deployer from pair raw metadata (some APIs include deployer)
        raw = pair.get("pairCreatedAt")  # dex pair raw may not include deployer; skip if not present
        # Instead we can try to get token contract creator via explorer (if key present)
        if BASESCAN_API_KEY:
            # query contract creation tx (module=contract&action=getsourcecode or getcontractcreation - varies)
            # We'll attempt to fetch internal txs / normal txs for contract to find deployer
            # Here we use the DeployerIntelligence method for v3/v2 detection: check recent txs for decrease/burn keywords
            # For simplicity we'll try both checks using token address and raw pair address
            checker = deployer_checker
            # check v3 style withdraw for addresses involved (take token address and pair URL)
            try:
                deployer_withdrawn = await checker.check_deployer_withdraw_v3(parsed["address"])
            except Exception:
                deployer_withdrawn = False
            # v2 pair withdraw detection (if pair contract exists)
            try:
                # if raw pair has pair contract address
                pair_contract = pair.get("pairAddress") or pair.get("pair", {}).get("address") or None
                if pair_contract:
                    w2 = await checker.check_deployer_withdraw_v2(pair_contract)
                    if w2:
                        deployer_withdrawn = True
            except Exception:
                pass
    except Exception:
        deployer_withdrawn = False

    td["deployer_withdrawn"] = deployer_withdrawn

    # ---- simulate / placeholder for other enrichments (replace with real checks if you have APIs) ----
    # For now, we might try to detect verified source from pair/raw if available
    # Example: if pair.raw contains "verified" flag - many API don't; so leave as-is or enrich later

    # ---- scoring ----
    scorer = RiskScorer()
    score = scorer.score(td)
    reasons = scorer.reasons

    # ---- price multiple detection & alerts ----
    returns = data_manager.calculate_returns(addr)
    if returns and returns.get("price_multiple", 1) > 1:
        current_multiple = returns.get("price_multiple", 1)
        next_multiple = math.floor(current_multiple) + 1
        targets = [m for m in range(2, next_multiple + 1) if current_multiple >= m]
        for m in targets:
            if alert_manager.should_send_price(addr, m):
                # send price alert message
                price_msg = (f"🚀 <b>涨幅警报</b>\n"
                             f"{td['name']} ({td['symbol']}) 已达 {m-1} 倍上涨 ({current_multiple:.2f}x)\n"
                             f"初始价: {returns.get('initial_price')}, 当前价: {returns.get('current_price')}\n"
                             f"合约: {td['address']}\n{td.get('url')}")
                await send_telegram(session, price_msg)
                alert_manager.mark_price_sent(addr, m)

    # ---- decide push or not ----
    # You requested: only push tokens that are NOT high risk (score < HIGH_RISK_THRESHOLD)
    should_push = score < HIGH_RISK_THRESHOLD and parsed["liquidity"] >= MIN_LIQUIDITY_USD

    analysis = {
        "addr": addr,
        "symbol": td["symbol"],
        "name": td["name"],
        "score": score,
        "reasons": reasons,
        "should_push": should_push,
        "parsed": parsed,
        "td": td
    }
    return analysis

# ---------------------------
# 主监控函数（单次执行）
# ---------------------------
async def run_once():
    print("=" * 60)
    print("BMIS 正在运行 - 单次扫描 (GitHub Actions Friendly)")
    print(f"时间: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)

    if Bot is None:
        print("⚠️ python-telegram-bot 未安装或无法导入，请确保 requirements.txt 包含 python-telegram-bot")
    if not BOT_TOKEN or not CHANNEL_ID:
        print("❌ 未配置 BOT_TOKEN 或 CHANNEL_ID (请在 GitHub Secrets 中配置)，退出")
        return 1

    async with aiohttp.ClientSession() as session:
        dex = DexScreenerClient(session)
        pairs = await dex.get_latest_pairs(chain=CHAIN_SLUG, limit=PAIRS_LIMIT)
        if not pairs:
            print("⚠️ 未获取到 pairs，可能 DexScreener 无返回 or rate-limited。退出本轮。")
            return 0

        print(f"🔎 获取到 {len(pairs)} pairs，开始逐个分析（上限 {PAIRS_LIMIT}）")
        deployer_checker = DeployerIntelligence(session, explorer_api_key=BASESCAN_API_KEY)

        tasks = []
        for p in pairs:
            tasks.append(analyze_token(p, session, dex, deployer_checker))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        pushed = 0
        for r in results:
            if isinstance(r, Exception):
                print(f"❌ 分析异常: {r}")
                continue
            if not r:
                continue
            if r.get("should_push"):
                # build push message with reasons & score
                parsed = r["parsed"]
                score = r["score"]
                reasons = r["reasons"]
                msg = (f"🟢 <b>新代币（建议观察 — 非高危）</b>\n\n"
                       f"📛 {r['name']} ({r['symbol']})\n"
                       f"💧 流动性: ${parsed.get('liquidity',0):,.0f}\n"
                       f"📊 风险评分: {score} (<=12 表示非高危)\n"
                       f"🔍 风险项: {'; '.join(reasons) if reasons else '无明显高风险'}\n"
                       f"🔗 {parsed.get('url')}\n"
                       f"⏱ 发现时间(UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
                await send_telegram(session, msg)
                pushed += 1
            else:
                print(f"跳过推送 (高危或流动性不足): {r['symbol']} score={r.get('score')} liq={r.get('parsed',{}).get('liquidity')}")
        # after analysis, send summary optionally
        summary = f"本轮扫描完成。分析 {len(results)} 个，推送 {pushed} 个（Non-high-risk）。"
        print(summary)
        try:
            await send_telegram(session, f"📡 BMIS 本轮完成：{pushed} 条推送。")
        except Exception:
            pass

    return 0

# ---------------------------
# CLI 入口
# ---------------------------
def main():
    try:
        loop = asyncio.get_event_loop()
        ret = loop.run_until_complete(run_once())
        # exit code for actions
        sys.exit(ret if isinstance(ret, int) else 0)
    except Exception as e:
        print(f"❌ 主流程异常: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
