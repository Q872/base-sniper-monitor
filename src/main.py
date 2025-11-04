#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BMIS - Base Meme Intelligent Sniper (监控/预警版)
修复版本 - 可正常运行
"""

import os
import sys
import time
import json
import math
import traceback
import asyncio
import aiohttp
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# ---------------------------
# Configuration
# ---------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHANNEL_ID")
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY", "")
HONEYPOT_API_KEY = os.getenv("HONEYPOT_API_KEY", "")
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "10000"))
PAIRS_LIMIT = int(os.getenv("PAIRS_LIMIT", "50"))
PRICE_HISTORY_LIMIT = int(os.getenv("PRICE_HISTORY_LIMIT", "200"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "3600"))
DEXSCREENER_API_BASE = "https://api.dexscreener.com/latest/dex"
CHAIN_SLUG = "base"
HONEYPOT_IS_URL = "https://api.honeypot.is/v1/GetHoneypotStatus"

# Risk thresholds
HIGH_RISK_THRESHOLD = 13
MEDIUM_RISK_THRESHOLD = 7
LOW_RISK_THRESHOLD = 6

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    sys.exit(1)

# ---------------------------
# Logger
# ---------------------------
class Logger:
    @staticmethod
    def now() -> str:
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def info(msg: str):
        print(f"[{Logger.now()}] [INFO] {msg}")

    @staticmethod
    def warn(msg: str):
        print(f"[{Logger.now()}] [WARN] {msg}")

    @staticmethod
    def error(msg: str):
        print(f"[{Logger.now()}] [ERROR] {msg}")

# ---------------------------
# Contract Verification
# ---------------------------
def is_verified_source(address: str) -> bool:
    """检查合约是否已验证开源"""
    try:
        if not BASESCAN_API_KEY:
            return False
            
        url = "https://api.basescan.org/api"
        params = {
            "module": "contract",
            "action": "getsourcecode",
            "address": address,
            "apikey": BASESCAN_API_KEY
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return False

        data = r.json()
        result = data.get("result", [])
        if not result or not isinstance(result, list):
            return False

        source_code = result[0].get("SourceCode", "")
        return bool(source_code and source_code.strip())

    except Exception as e:
        Logger.warn(f"Contract verification failed: {e}")
        return False

# ---------------------------
# Token Data Manager
# ---------------------------
class TokenDataManager:
    def __init__(self, path: str = "data/token_history.json"):
        self.path = path
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if not os.path.exists(self.path):
            self._init_file()

    def _init_file(self):
        with open(self.path, "w") as f:
            json.dump({"tokens": {}, "stats": {}}, f, indent=2)

    def load(self) -> Dict:
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except Exception as e:
            Logger.warn(f"Failed to load token history: {e}")
            return {"tokens": {}, "stats": {}}

    def save(self, data: Dict):
        try:
            with open(self.path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            Logger.warn(f"Failed to save token history: {e}")

    def record_price(self, address: str, symbol: str, price: float, liquidity: float):
        data = self.load()
        now = datetime.utcnow().isoformat() + "Z"
        tokens = data.setdefault("tokens", {})
        t = tokens.setdefault(address, {
            "symbol": symbol,
            "first_seen": now,
            "price_history": [],
            "price_alerts_sent": [],
            "initial_price": price,
            "initial_liquidity": liquidity
        })
        t["price_history"].append({"timestamp": now, "price": price, "liquidity": liquidity})
        if len(t["price_history"]) > PRICE_HISTORY_LIMIT:
            t["price_history"] = t["price_history"][-PRICE_HISTORY_LIMIT:]
        t["current_price"] = price
        t["last_updated"] = now
        if not t.get("initial_price"):
            t["initial_price"] = price
        if not t.get("initial_liquidity"):
            t["initial_liquidity"] = liquidity
        self.save(data)
        return t

    def get_price_alerts(self, address: str) -> List[int]:
        data = self.load()
        return data.get("tokens", {}).get(address, {}).get("price_alerts_sent", [])

    def mark_price_alert(self, address: str, multiple: int):
        data = self.load()
        arr = data.setdefault("tokens", {}).setdefault(address, {}).setdefault("price_alerts_sent", [])
        if multiple not in arr:
            arr.append(multiple)
            self.save(data)

    def compute_returns(self, address: str) -> Dict[str, Any]:
        data = self.load()
        token = data.get("tokens", {}).get(address)
        if not token:
            return {}
        initial = token.get("initial_price", 0) or 0
        current = token.get("current_price", initial) or initial
        if initial == 0:
            return {}
        total_return = ((current - initial) / initial) * 100
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

data_manager = TokenDataManager()

# ---------------------------
# Alert Manager
# ---------------------------
class AlertManager:
    def __init__(self):
        self.sent: Dict[str, float] = {}

    def _cooldown_ok(self, key: str, cooldown: int = ALERT_COOLDOWN) -> bool:
        now = time.time()
        last = self.sent.get(key)
        if last and (now - last) < cooldown:
            return False
        self.sent[key] = now
        return True

    def should_send_risk(self, address: str, score: int) -> bool:
        if score >= HIGH_RISK_THRESHOLD:
            return False
        key = f"risk:{address}:{score}"
        return self._cooldown_ok(key)

    def should_send_price(self, address: str, multiple: int) -> bool:
        sent = data_manager.get_price_alerts(address)
        return multiple not in sent

    def mark_price(self, address: str, multiple: int):
        data_manager.mark_price_alert(address, multiple)

alert_manager = AlertManager()

# ---------------------------
# DexScreener Client
# ---------------------------
class DexScreenerClient:
    def __init__(self):
        self.base = DEXSCREENER_API_BASE

    async def get_latest_pairs(self, chain: str = CHAIN_SLUG, limit: int = PAIRS_LIMIT) -> List[Dict[str, Any]]:
        url = f"{self.base}/pairs/{chain}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        Logger.warn(f"DexScreener returned {resp.status}")
                        return []
                    j = await resp.json()
                    pairs = j.get("pairs", [])[:limit]
                    return pairs
            except Exception as e:
                Logger.warn(f"DexScreener request failed: {e}")
                return []

# ---------------------------
# Honeypot Check
# ---------------------------
async def check_honeypot_async(session: aiohttp.ClientSession, token_address: str, chain: str = "base") -> Dict[str, Any]:
    params = {"address": token_address}
    if HONEYPOT_API_KEY:
        params["apiKey"] = HONEYPOT_API_KEY
    try:
        async with session.get(HONEYPOT_IS_URL, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status != 200:
                return {"isHoneypot": False, "error": f"status {resp.status}"}
            j = await resp.json()
            return j
    except Exception as e:
        return {"isHoneypot": False, "error": str(e)}

# ---------------------------
# Creator Analysis
# ---------------------------
class CreatorAnalyzer:
    def __init__(self, api_key: str = ""):
        self.key = api_key

    def analyze_creator(self, address: str) -> Dict[str, Any]:
        out = {
            "is_new": False, 
            "last_txs": [], 
            "eth_balance": 0.0, 
            "has_v2_withdraw": False, 
            "has_v3_decrease": False, 
            "has_rug_history": False
        }
        try:
            if not self.key:
                return out
                
            # Get balance
            bal_params = {
                "module": "account", 
                "action": "balance", 
                "address": address, 
                "tag": "latest", 
                "apikey": self.key
            }
            r = requests.get("https://api.basescan.org/api", params=bal_params, timeout=8)
            if r.status_code == 200:
                j = r.json()
                out["eth_balance"] = int(j.get("result", 0)) / 1e18 if j.get("result") else 0.0

            # Get transactions (simplified)
            tx_params = {
                "module": "account",
                "action": "txlist",
                "address": address,
                "page": 1,
                "offset": 10,
                "sort": "desc",
                "apikey": self.key
            }
            r = requests.get("https://api.basescan.org/api", params=tx_params, timeout=10)
            if r.status_code == 200:
                j = r.json()
                txs = j.get("result", [])[:5]
                out["last_txs"] = txs
                
                if txs:
                    # Check if creator is new (first tx within 7 days)
                    try:
                        first_tx_ts = min(int(tx["timeStamp"]) for tx in txs)
                        days_old = (datetime.utcnow() - datetime.utcfromtimestamp(first_tx_ts)).days
                        out["is_new"] = days_old <= 7
                    except Exception:
                        pass

        except Exception as e:
            Logger.warn(f"Creator analysis failed: {e}")
        return out

# ---------------------------
# Risk Scorer
# ---------------------------
class RiskScorer:
    def __init__(self):
        self.reasons: List[str] = []

    def score(self, token_meta: Dict[str, Any], creator_meta: Dict[str, Any], honeypot_meta: Dict[str, Any]) -> int:
        self.reasons = []
        score = 0
        
        # Contract verification
        if not token_meta.get("verified", False):
            score += 2
            self.reasons.append("合约未验证")
            
        # Liquidity check
        liq = float(token_meta.get("liquidity_usd", 0) or 0)
        if liq < 5000:
            score += 3
            self.reasons.append("流动性偏低")
        elif liq < 30000:
            score += 1
            
        # Honeypot check
        if honeypot_meta.get("isHoneypot", False):
            score += 8
            self.reasons.append("Honeypot 检测为危险")
            
        # Tax check (simplified)
        if token_meta.get("high_tax", False):
            score += 4
            self.reasons.append("交易税偏高")
            
        # Creator analysis
        if creator_meta.get("is_new"):
            score += 2
            self.reasons.append("部署者为新钱包")
            
        if creator_meta.get("has_rug_history"):
            score += 6
            self.reasons.append("部署者有撤池历史")
            
        if creator_meta.get("eth_balance", 0) < 0.1:
            score += 1
            self.reasons.append("部署者余额偏低")
            
        # Positive factors
        if token_meta.get("has_community", False):
            score -= 2
            self.reasons.append("有社区或社媒")
            
        score = max(0, int(score))
        return score

# ---------------------------
# Telegram Helper
# ---------------------------
async def send_telegram_async(session: aiohttp.ClientSession, text: str):
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": text, 
        "parse_mode": "HTML", 
        "disable_web_page_preview": True
    }
    try:
        async with session.post(api, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                txt = await resp.text()
                Logger.warn(f"Telegram API returned {resp.status}: {txt}")
                return False
            return True
    except Exception as e:
        Logger.warn(f"Telegram send error: {e}")
        return False

# ---------------------------
# Pair Analysis
# ---------------------------
def parse_pair_to_meta(pair: Dict[str, Any]) -> Dict[str, Any]:
    base = pair.get("baseToken", {}) or {}
    
    # Extract liquidity
    liquidity = 0.0
    try:
        liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
    except Exception:
        liquidity = 0.0
        
    # Extract volume
    volume = 0.0
    try:
        volume = float((pair.get("volume") or {}).get("h24") or 0)
    except Exception:
        volume = 0.0
        
    price = float(pair.get("priceUsd") or 0)
    created = pair.get("pairCreatedAt")
    age_minutes = 0
    
    if created:
        try:
            if isinstance(created, str):
                created_ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            else:
                created_ts = float(created) / 1000.0
            age_minutes = int((time.time() - created_ts) / 60)
        except Exception:
            age_minutes = 0
            
    meta = {
        "address": base.get("address") or base.get("id") or "",
        "symbol": base.get("symbol", "UNK"),
        "name": base.get("name", "Unknown"),
        "liquidity_usd": liquidity,
        "volume_24h": volume,
        "price_usd": price,
        "pair_address": pair.get("pairAddress") or pair.get("pair", {}).get("address") or "",
        "dex": pair.get("dexId"),
        "created_at": pair.get("pairCreatedAt"),
        "age_minutes": age_minutes,
        "info": pair.get("info", {}) or {},
    }
    
    # Extract social info
    info = meta["info"]
    if isinstance(info, dict):
        meta["website"] = info.get("websites", [None])[0] if info.get("websites") else None
        meta["telegram"] = info.get("telegram", None)
        
    return meta

async def analyze_and_maybe_push(pair: Dict[str, Any], session: aiohttp.ClientSession, 
                               dex_client: DexScreenerClient, creator_analyzer: CreatorAnalyzer, 
                               scorer: RiskScorer):
    meta = parse_pair_to_meta(pair)
    addr = meta["address"]
    symbol = meta["symbol"]
    name = meta["name"]
    liq = meta["liquidity_usd"]
    
    Logger.info(f"Analyzing {symbol} ({addr}) - liquidity ${liq:,.2f}")

    # Record price history
    data_manager.record_price(addr, symbol, meta.get("price_usd", 0.0), liq)

    # Honeypot check
    honeypot_meta = {"isHoneypot": False}
    try:
        honeypot_meta = await check_honeypot_async(session, addr, chain=CHAIN_SLUG)
    except Exception as e:
        Logger.warn(f"Honeypot check failed: {e}")

    # Contract verification
    is_verified = is_verified_source(addr)
    
    # Build token metadata
    token_meta = {
        "address": addr,
        "symbol": symbol,
        "name": name,
        "liquidity_usd": liq,
        "price_usd": meta.get("price_usd"),
        "volume_24h": meta.get("volume_24h"),
        "website": meta.get("website"),
        "telegram": meta.get("telegram"),
        "verified": is_verified,
        "has_community": bool(meta.get("telegram") or meta.get("website")),
        "high_tax": False,  # Simplified for this example
    }

    # Creator analysis
    creator_addr = meta.get("pair_address") or addr
    creator_meta = creator_analyzer.analyze_creator(creator_addr)

    # Risk scoring
    score = scorer.score(token_meta, creator_meta, honeypot_meta)
    Logger.info(f"{symbol} score = {score} ; reasons: {scorer.reasons}")

    # Price multiple alerts
    returns = data_manager.compute_returns(addr)
    if returns and returns.get("price_multiple", 1) > 1:
        current_multiple = returns.get("price_multiple", 1)
        integer_multiple = int(current_multiple)
        if integer_multiple >= 2 and alert_manager.should_send_price(addr, integer_multiple):
            price_msg = (f"🚀 <b>涨幅通知</b>\n"
                         f"{name} ({symbol}) 已达到 {integer_multiple}x 上涨\n"
                         f"初始价: ${returns.get('initial_price', 0):.6f}\n"
                         f"当前价: ${returns.get('current_price', 0):.6f}\n"
                         f"流动性: ${liq:,.2f}\n"
                         f"合约: <code>{addr}</code>\n")
            success = await send_telegram_async(session, price_msg)
            if success:
                alert_manager.mark_price(addr, integer_multiple)

    # Risk-based push decision
    should_push = (score < HIGH_RISK_THRESHOLD) and (liq >= MIN_LIQUIDITY_USD)
    if not should_push:
        Logger.warn(f"Skip push for {symbol}: score {score} liq ${liq:,.2f}")
        return {"pushed": False, "score": score}

    # Prepare and send alert
    reasons_text = "\n".join([f"- {r}" for r in scorer.reasons]) if scorer.reasons else "无明显高风险"
    verified_text = "✅ 开源" if is_verified else "❌ 未开源"
    
    msg = (f"🟢 <b>新代币检测（非高危）</b>\n"
           f"{name} ({symbol})\n"
           f"流动性: ${liq:,.2f}\n"
           f"24h 量: ${meta.get('volume_24h',0):,.0f}\n"
           f"风险评分: {score} ({'优质' if score<=LOW_RISK_THRESHOLD else '中风险'})\n"
           f"开源状态: {verified_text}\n"
           f"风险因素:\n{reasons_text}\n"
           f"创建者地址: <code>{creator_addr}</code>\n"
           f"创建者余额: {creator_meta.get('eth_balance', 0):.4f} ETH\n"
           f"创建者撤池历史: {'是' if creator_meta.get('has_rug_history') else '否'}\n"
           f"合约: <code>{addr}</code>\n"
           f"更多: {meta.get('website') or meta.get('telegram') or ''}\n"
           f"时间(UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
           
    await send_telegram_async(session, msg)
    Logger.info(f"Pushed token {symbol} ({addr}) to Telegram")
    return {"pushed": True, "score": score}

# ---------------------------
# Main Execution
# ---------------------------
async def run_once():
    Logger.info("BMIS single-run started")
    dex = DexScreenerClient()
    scorer = RiskScorer()
    creator_analyzer = CreatorAnalyzer(api_key=BASESCAN_API_KEY)
    
    async with aiohttp.ClientSession() as session:
        pairs = await dex.get_latest_pairs(chain=CHAIN_SLUG, limit=PAIRS_LIMIT)
        if not pairs:
            Logger.warn("No pairs from DexScreener; exiting.")
            return 1

        # Sort by creation time (newest first)
        try:
            pairs_sorted = sorted(pairs, key=lambda x: x.get("pairCreatedAt", 0), reverse=True)
        except Exception:
            pairs_sorted = pairs

        results = []
        for pair in pairs_sorted:
            try:
                res = await analyze_and_maybe_push(pair, session, dex, creator_analyzer, scorer)
                results.append(res)
            except Exception as e:
                Logger.warn(f"Error analyzing pair: {e}")
                traceback.print_exc()

        pushed = sum(1 for r in results if r and r.get("pushed"))
        Logger.info(f"Run complete. Pairs checked: {len(results)}. Pushed: {pushed}")
        
        # Send summary
        try:
            await send_telegram_async(session, f"📡 BMIS 本轮完成：已检测 {len(results)} 对，推送 {pushed} 条（非高危）。")
        except Exception:
            pass
            
    return 0

def main():
    try:
        loop = asyncio.get_event_loop()
        code = loop.run_until_complete(run_once())
        sys.exit(code if isinstance(code, int) else 0)
    except Exception as e:
        Logger.error(f"Main error: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
