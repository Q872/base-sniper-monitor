#!/usr/bin/env python3
"""
完整版 main.py
- 包含：TokenDataManager, RiskScorer, DexScreener 接口, AlertManager
- 修复点：DexScreener 调用、环境变量读取、流动性阈值由环境变量控制
"""

import asyncio
import aiohttp
import yaml
import os
import json
import time
import math
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# ========== 配置 ==========
# 可通过环境变量覆盖
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "10000"))  # 初始LP阈值，低于则不推送（你可以在 Github Secrets 里设置）
PRICE_HISTORY_LIMIT = int(os.getenv("PRICE_HISTORY_LIMIT", "100"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "3600"))  # 1小时：价格/风险重复报警冷却（秒）

# Telegram env keys
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ========== 数据管理 ==========
class TokenDataManager:
    def __init__(self, data_file: str = "data/token_history.json"):
        self.data_file = data_file
        self._ensure_data_file()
    
    def _ensure_data_file(self):
        """确保数据文件存在"""
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        if not os.path.exists(self.data_file):
            with open(self.data_file, 'w') as f:
                json.dump({"tokens": {}, "statistics": {}}, f, indent=2)
    
    def load_data(self) -> Dict:
        """加载历史数据"""
        try:
            with open(self.data_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ 加载数据失败: {e}")
            return {"tokens": {}, "statistics": {}}
    
    def save_data(self, data: Dict):
        """保存数据"""
        try:
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"❌ 保存数据失败: {e}")
    
    def record_token_price(self, token_address: str, symbol: str, price: float, liquidity: float):
        """记录代币价格"""
        data = self.load_data()
        current_time = datetime.utcnow().isoformat() + "Z"
        
        if token_address not in data["tokens"]:
            data["tokens"][token_address] = {
                "symbol": symbol,
                "first_seen": current_time,
                "price_history": [],
                "highest_price": price,
                "lowest_price": price,
                "initial_price": price,
                "initial_liquidity": liquidity,
                "price_alerts_sent": []  # 记录已发送的价格警报倍数
            }
        
        token_data = data["tokens"][token_address]
        token_data["price_history"].append({
            "timestamp": current_time,
            "price": price,
            "liquidity": liquidity
        })
        
        # 保留最近 PRICE_HISTORY_LIMIT 条记录
        if len(token_data["price_history"]) > PRICE_HISTORY_LIMIT:
            token_data["price_history"] = token_data["price_history"][-PRICE_HISTORY_LIMIT:]
        
        # 更新最高/最低价格
        token_data["highest_price"] = max(token_data.get("highest_price", price), price)
        token_data["lowest_price"] = min(token_data.get("lowest_price", price), price)
        token_data["current_price"] = price
        token_data["last_updated"] = current_time
        
        # 若无 initial_price 则设置
        if not token_data.get("initial_price"):
            token_data["initial_price"] = price
        
        self.save_data(data)
        return token_data
    
    def calculate_returns(self, token_address: str) -> Dict:
        """计算收益率"""
        data = self.load_data()
        if token_address not in data["tokens"]:
            return {}
        
        token_data = data["tokens"][token_address]
        initial_price = token_data.get("initial_price", 0)
        current_price = token_data.get("current_price", initial_price)
        
        if initial_price == 0:
            return {}
        
        total_return = ((current_price - initial_price) / initial_price) * 100
        
        # 计算24小时收益率（如果有足够数据）
        twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
        price_24h_ago = initial_price
        
        for price_point in reversed(token_data.get("price_history", [])):
            try:
                price_time = datetime.fromisoformat(price_point["timestamp"].replace("Z", "+00:00"))
                if price_time <= twenty_four_hours_ago:
                    price_24h_ago = price_point["price"]
                    break
            except Exception:
                continue
        
        return_24h = ((current_price - price_24h_ago) / price_24h_ago) * 100 if price_24h_ago > 0 else 0
        
        return {
            "total_return": round(total_return, 2),
            "return_24h": round(return_24h, 2),
            "initial_price": initial_price,
            "current_price": current_price,
            "price_change": current_price - initial_price,
            "price_multiple": current_price / initial_price if initial_price > 0 else 1,
            "highest_return": round(((token_data.get("highest_price", current_price) - initial_price) / initial_price) * 100, 2),
            "current_liquidity": token_data.get("price_history", [])[-1].get("liquidity", 0) if token_data.get("price_history") else 0
        }
    
    def get_price_alerts_sent(self, token_address: str) -> List[int]:
        """获取已发送的价格警报倍数"""
        data = self.load_data()
        if token_address in data["tokens"]:
            return data["tokens"][token_address].get("price_alerts_sent", [])
        return []
    
    def mark_price_alert_sent(self, token_address: str, multiple: int):
        """标记价格警报已发送"""
        data = self.load_data()
        if token_address in data["tokens"]:
            if multiple not in data["tokens"][token_address].get("price_alerts_sent", []):
                data["tokens"][token_address].setdefault("price_alerts_sent", []).append(multiple)
                self.save_data(data)
    
    def get_top_performers(self, limit: int = 10) -> List[Dict]:
        """获取表现最好的代币"""
        data = self.load_data()
        performers = []
        
        for address, token_data in data["tokens"].items():
            returns = self.calculate_returns(address)
            if returns and returns.get("total_return") is not None:
                performers.append({
                    "address": address,
                    "symbol": token_data.get("symbol", ""),
                    **returns
                })
        
        # 按总收益率排序
        performers.sort(key=lambda x: x.get("total_return", 0), reverse=True)
        return performers[:limit]
    
    def get_recent_tokens(self, hours: int = 24) -> List[Dict]:
        """获取最近发现的代币"""
        data = self.load_data()
        recent_tokens = []
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        for address, token_data in data["tokens"].items():
            try:
                first_seen = datetime.fromisoformat(token_data.get("first_seen").replace("Z", "+00:00"))
            except Exception:
                first_seen = datetime.utcnow() - timedelta(days=365)
            if first_seen >= cutoff_time:
                returns = self.calculate_returns(address)
                recent_tokens.append({
                    "address": address,
                    "symbol": token_data.get("symbol", ""),
                    "first_seen": token_data.get("first_seen"),
                    **returns
                })
        
        # 按发现时间排序
        recent_tokens.sort(key=lambda x: x.get("first_seen", ""), reverse=True)
        return recent_tokens

# 初始化数据管理器（单例）
data_manager = TokenDataManager()

# ========== 风险评分器 ==========
class RiskScorer:
    def __init__(self):
        self.risk_reasons = []  # 存储所有风险原因
        
    def calculate_risk_score(self, token_data):
        """计算风险分数并收集风险原因"""
        risk_score = 0
        self.risk_reasons = []
        
        # 1. 合约验证状态检测
        if not self.check_contract_verified(token_data):
            risk_score += 2
            self.risk_reasons.append("❌ 合约未验证")
        
        # 2. 买卖税率检测（重点！）
        tax_risk = self.check_tax_rate(token_data)
        if tax_risk > 0:
            risk_score += tax_risk
            if tax_risk == 3:
                self.risk_reasons.append("⚠️ 买卖税 > 5%")
            elif tax_risk == 1:
                self.risk_reasons.append("⚠️ 买卖税 > 3%")
        
        # 3. Honeypot检测
        if self.check_honeypot(token_data):
            risk_score += 5
            self.risk_reasons.append("🚫 Honeypot检测失败")
        
        # 4. LP锁仓检测
        lp_risk = self.check_lp_lock(token_data)
        if lp_risk > 0:
            risk_score += lp_risk
            self.risk_reasons.append("🔓 LP锁仓时间短或无锁仓")
        
        # 5. 钱包年龄检测
        if self.check_wallet_age(token_data):
            risk_score += 2
            self.risk_reasons.append("🆕 部署钱包 < 6小时")
        
        # 6. 资金来源检测
        if self.check_fund_source(token_data):
            risk_score += 3
            self.risk_reasons.append("💸 资金来源可疑")
        
        # 7. 部署者历史检测
        if self.check_deployer_history(token_data):
            risk_score += 4
            self.risk_reasons.append("👤 部署者有不良记录")
        
        # 安全减分项
        if self.check_verified_community(token_data):
            risk_score -= 2
            self.risk_reasons.append("✅ 合约已验证且有社群")
        
        if self.check_cex_source(token_data):
            risk_score -= 1
            self.risk_reasons.append("🏦 资金来自CEX")
        
        if self.check_holder_distribution(token_data):
            risk_score -= 1
            self.risk_reasons.append("📊 持仓分布良好")
        
        return max(risk_score, 0)  # 确保分数不为负
    
    def check_tax_rate(self, token_data):
        """检测买卖税率 - 重点检测项"""
        try:
            buy_tax = float(token_data.get('buy_tax', 0) or 0)
            sell_tax = float(token_data.get('sell_tax', 0) or 0)
            
            if buy_tax > 0.05 or sell_tax > 0.05:  # 5%阈值
                return 3
            elif buy_tax > 0.03 or sell_tax > 0.03:  # 3%警告
                return 1
        except Exception:
            pass
        return 0
    
    def check_contract_verified(self, token_data):
        """检查合约是否验证"""
        return bool(token_data.get('verified', False))
    
    def check_honeypot(self, token_data):
        """Honeypot检测"""
        return bool(token_data.get('is_honeypot', False))
    
    def check_lp_lock(self, token_data):
        """LP锁仓检测"""
        try:
            lp_lock_days = int(token_data.get('lp_lock_days', 0) or 0)
        except Exception:
            lp_lock_days = 0
        if lp_lock_days == 0:
            return 2  # 未锁仓
        elif lp_lock_days < 30:
            return 1  # 锁仓时间短
        return 0
    
    def check_wallet_age(self, token_data):
        """钱包年龄检测"""
        try:
            wallet_age_hours = float(token_data.get('wallet_age_hours', 24) or 24)
        except Exception:
            wallet_age_hours = 24
        return wallet_age_hours < 6
    
    def check_fund_source(self, token_data):
        """资金来源检测"""
        return bool(token_data.get('suspicious_source', False))
    
    def check_deployer_history(self, token_data):
        """部署者历史检测"""
        return bool(token_data.get('has_rug_history', False))
    
    def check_verified_community(self, token_data):
        """检查验证状态和社群"""
        return bool(token_data.get('verified', False) and token_data.get('has_community', False))
    
    def check_cex_source(self, token_data):
        """检查是否来自CEX"""
        return bool(token_data.get('from_cex', False))
    
    def check_holder_distribution(self, token_data):
        """检查持仓分布"""
        try:
            top10_holders = float(token_data.get('top10_holders_percent', 100) or 100)
        except Exception:
            top10_holders = 100
        return top10_holders < 20  # 前10大户持仓 < 20%

# ========== 报警管理 ==========
class AlertManager:
    def __init__(self):
        self.sent_alerts = {}  # key -> timestamp
        self.alert_cooldown = ALERT_COOLDOWN  # seconds
    
    def should_send_risk_alert(self, token_address: str, risk_score: int) -> bool:
        """判断是否应该发送风险警报"""
        if risk_score <= 6:
            return False
        
        current_time = time.time()
        alert_key = f"risk_{token_address}_{risk_score}"
        
        last = self.sent_alerts.get(alert_key)
        if last and (current_time - last) < self.alert_cooldown:
            return False
        
        self.sent_alerts[alert_key] = current_time
        return True
    
    def should_send_price_alert(self, token_address: str, multiple: int) -> bool:
        """判断是否应该发送价格倍数警报"""
        sent = data_manager.get_price_alerts_sent(token_address)
        return multiple not in sent
    
    def cleanup_old_alerts(self):
        """清理过期的警报记录"""
        now = time.time()
        keys = list(self.sent_alerts.keys())
        for k in keys:
            if now - self.sent_alerts[k] > self.alert_cooldown:
                del self.sent_alerts[k]

alert_manager = AlertManager()

# ========== DexScreener API ==========
class DexScreenerAPI:
    def __init__(self):
        self.base_url = "https://api.dexscreener.com/latest/dex"
    
    async def get_latest_base_pairs(self, limit: int = 25):
        """获取 Base 链最新代币对（官方 latest/pairs/base）"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/pairs/base", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        print(f"✅ DexScreener 返回 pairs 数量: {len(pairs)}")
                        return pairs[:limit]
                    else:
                        print(f"❌ DexScreener status: {resp.status}")
                        return []
        except Exception as e:
            print(f"❌ DexScreener 请求异常: {e}")
            return []

dexscreener = DexScreenerAPI()

# ========== 工具函数 ==========
def parse_pair_data(pair):
    """解析 DexScreener pair 数据成统一结构"""
    try:
        created_at = pair.get('pairCreatedAt')
        age_minutes = 0
        if created_at:
            # pairCreatedAt 有时是字符串 ISO 或者时间戳（毫秒）
            if isinstance(created_at, str):
                try:
                    created_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    age_minutes = int((datetime.utcnow() - created_time).total_seconds() / 60)
                except Exception:
                    age_minutes = 0
            else:
                # 认为是 ms timestamp
                try:
                    age_minutes = int((time.time() * 1000 - float(created_at)) / 60000)
                except Exception:
                    age_minutes = 0
        
        base_token = pair.get('baseToken', {}) or {}
        liquidity = 0
        try:
            liquidity = float((pair.get('liquidity') or {}).get('usd') or 0)
        except Exception:
            liquidity = 0
        
        volume_24h = 0
        try:
            volume_24h = float((pair.get('volume') or {}).get('h24') or 0)
        except Exception:
            volume_24h = 0
        
        price_change_24h = 0
        if pair.get('priceChange'):
            try:
                price_change_24h = float(pair.get('priceChange').get('h24') or 0)
            except Exception:
                price_change_24h = 0
        
        return {
            "address": base_token.get('address', '') or base_token.get('id', ''),
            "name": base_token.get('name', 'Unknown'),
            "symbol": base_token.get('symbol', 'Unknown'),
            "liquidity": liquidity,
            "volume": volume_24h,
            "price_change_24h": price_change_24h,
            "priceUsd": pair.get('priceUsd', 0),
            "url": pair.get('url', ''),
            "age_minutes": age_minutes,
            "chainId": pair.get('chainId'),
            "dexId": pair.get('dexId')
        }
    except Exception as e:
        print(f"❌ parse_pair_data error: {e}")
        return None

def validate_token_data(token_data):
    """验证 token 数据完整性与阈值"""
    if not token_data:
        return False
    required_fields = ['address', 'symbol', 'liquidity']
    for f in required_fields:
        if not token_data.get(f):
            return False
    # 使用全局阈值 MIN_LIQUIDITY_USD
    if float(token_data.get('liquidity', 0) or 0) < float(MIN_LIQUIDITY_USD):
        return False
    return True

# ========== Telegram 发送 ==========
async def send_telegram_message(message):
    """发送 Telegram 消息（异步）"""
    bot_token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if not bot_token or not chat_id:
        print("⚠️ Telegram 配置缺失（未设置 TELEGAM_BOT_TOKEN/CHAT_ID），跳过发送")
        return False
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'Markdown',
                'disable_web_page_preview': True
            }
            async with session.post(f'https://api.telegram.org/bot{bot_token}/sendMessage', json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    print("✅ Telegram 消息发送成功")
                    return True
                else:
                    txt = await resp.text()
                    print(f"❌ Telegram 发送失败 {resp.status}: {txt}")
                    return False
    except Exception as e:
        print(f"❌ Telegram 发送异常: {e}")
        return False

# ========== 分析与报警逻辑 ==========
async def send_risk_alert(token_data, risk_score, risk_reasons):
    """构建并发送风险告警到 Telegram"""
    if risk_score <= 6:
        risk_level = "🟢 安全"
        emoji = "✅"
    elif risk_score <= 12:
        risk_level = "🟡 中风险"
        emoji = "⚠️"
    else:
        risk_level = "🔴 高风险"
        emoji = "🚨"
    
    reason_text = "\n".join(risk_reasons) if risk_reasons else "暂无风险项"
    returns = data_manager.calculate_returns(token_data["address"])
    returns_info = f"📈 当前收益率: {returns.get('total_return', 0):.2f}%\n" if returns else ""
    
    message = f"""{emoji} *BASE 链代币风险警报* {emoji}

💰 *{token_data.get('name')} ({token_data.get('symbol')})*
📊 风险评分: {risk_score} 分 - {risk_level}
{returns_info}
🔍 *检测到的风险项:*
{reason_text}

💧 流动性: ${token_data.get('liquidity', 0):,.0f}
📈 24h 交易量: ${token_data.get('volume', 0):,.0f}
⏰ 代币年龄: {token_data.get('age_minutes')} 分钟

📋 合约地址: `{token_data.get('address')}`
🔗 链接: {token_data.get('url')}

{'⚠️ 请注意风险，谨慎操作！' if risk_score > 6 else '✅ 相对安全，但仍需自行研究！'}"""
    await send_telegram_message(message)

async def send_price_alert(token_data, multiple: int, current_multiple: float):
    """发送价格倍数警报"""
    returns = data_manager.calculate_returns(token_data["address"])
    initial_price = returns.get("initial_price", 0)
    current_price = returns.get("current_price", 0)
    actual_multiple = multiple - 1  # 表示涨了多少倍（2 表示 1 倍上涨 etc）
    message = f"""🚀 *BASE链代币涨幅警报*

💰 *{token_data.get('name')} ({token_data.get('symbol')})*
🎯 已达到 {actual_multiple} 倍涨幅！
📊 当前涨幅: {current_multiple:.2f}x

💰 初始价格: ${initial_price:.6f}
💰 当前价格: ${current_price:.6f}
📈 总收益率: {returns.get('total_return', 0):.2f}%

💧 流动性: ${token_data.get('liquidity', 0):,.0f}
⏰ 代币年龄: {token_data.get('age_minutes')} 分钟

📋 合约地址: `{token_data.get('address')}`
🔗 链接: {token_data.get('url')}
"""
    ok = await send_telegram_message(message)
    if ok:
        data_manager.mark_price_alert_sent(token_data["address"], multiple)

async def analyze_token_with_risk(token_data):
    """对单个代币执行风险评分与价格倍数检测"""
    print(f"\n🪙 分析代币: {token_data.get('symbol')} - {token_data.get('name')}")
    print(f"   💧 流动性: ${token_data.get('liquidity',0):,.0f}")
    print(f"   📈 24h 交易量: ${token_data.get('volume',0):,.0f}")
    print(f"   ⏰ 代币年龄: {token_data.get('age_minutes')} 分钟")
    
    # 记录价格（若 priceUsd 可用）
    price = token_data.get('priceUsd') or 0
    data_manager.record_token_price(token_data.get('address'), token_data.get('symbol'), float(price or 0), float(token_data.get('liquidity') or 0))
    
    returns = data_manager.calculate_returns(token_data.get('address'))
    # 价格倍数警报
    if returns and returns.get("price_multiple", 1) > 1:
        current_multiple = returns.get("price_multiple", 1)
        next_multiple = math.floor(current_multiple) + 1
        target_multiples = [m for m in range(2, next_multiple + 1) if current_multiple >= m]
        for m in target_multiples:
            if alert_manager.should_send_price_alert(token_data.get('address'), m):
                print(f"   🚨 发送 {m-1} 倍价格警报")
                await send_price_alert(token_data, m, current_multiple)
    
    # 风险评分
    scorer = RiskScorer()
    risk_score = scorer.calculate_risk_score(token_data)
    print(f"   📊 风险评分: {risk_score} 分")
    if scorer.risk_reasons:
        print(f"   🔍 风险原因: {', '.join(scorer.risk_reasons)}")
    
    # 发风险告警（若需要）
    if alert_manager.should_send_risk_alert(token_data.get('address'), risk_score):
        print("   🚨 发送风险告警")
        await send_risk_alert(token_data, risk_score, scorer.risk_reasons)
    
    return {"risk_score": risk_score, "risk_reasons": scorer.risk_reasons, "quality_tokens": 1 if risk_score <= 6 else 0}

# ========== 监控循环 ==========
async def monitor_new_tokens():
    """核心监控：获取最新 Base tokens 并分析"""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n🚀 [{now}] 开始监控 Base 链新代币...")
    alert_manager.cleanup_old_alerts()
    
    pairs = await dexscreener.get_latest_base_pairs(25)
    if not pairs:
        print("⚠️ 未获取到 pairs，结束本次监控")
        return False
    
    # 调试输出前两条
    try:
        print("🔎 DexScreener 前两条示例：")
        print(json.dumps(pairs[:2], indent=2, ensure_ascii=False))
    except Exception:
        pass
    
    # 过滤 Base 链
    base_pairs = [p for p in pairs if p.get('chainId') == 'base']
    print(f"📊 过滤后 {len(base_pairs)} 个 Base 链 pairs")
    
    tasks = []
    for pair in base_pairs:
        td = parse_pair_data(pair)
        if not td:
            continue
        if not validate_token_data(td):
            print(f"⚠️ 跳过 {td.get('symbol')}（流动性/数据不足）")
            continue
        
        # 为测试目的暂时注入模拟风险字段（部署时请替换为真实检测）
        import random
        td.update({
            "verified": random.choice([True, False, True]),
            "buy_tax": random.uniform(0, 0.08),
            "sell_tax": random.uniform(0, 0.08),
            "is_honeypot": random.choice([False, False, False, True]),
            "lp_lock_days": random.choice([0, 30, 60, 90, 365]),
            "wallet_age_hours": random.randint(1, 72),
            "suspicious_source": random.choice([False, False, True]),
            "has_rug_history": random.choice([False, False, False, True]),
            "has_community": random.choice([True, False]),
            "from_cex": random.choice([True, False]),
            "top10_holders_percent": random.uniform(10, 80)
        })
        
        tasks.append(analyze_token_with_risk(td))
    
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        found = 0
        for r in results:
            if isinstance(r, Exception):
                print(f"❌ 任务异常: {r}")
            elif r and r.get("quality_tokens", 0) > 0:
                found += r.get("quality_tokens", 0)
        print(f"🎯 本轮发现 {found} 个符合条件的低风险代币")
    else:
        print("ℹ️ 本轮没有需要分析的代币")
    return True

# ========== 性能报告 ==========
async def send_performance_report(top_performers: List, recent_tokens: List):
    bot_token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if not bot_token or not chat_id:
        return
    message = "📊 *Base链代币表现报告*\n\n"
    if top_performers:
        message += "🏆 *顶级表现者:*\n"
        for i, token in enumerate(top_performers, 1):
            multiple_text = f" ({token.get('price_multiple',1):.2f}x)" if token.get('price_multiple') else ""
            message += f"{i}. {token.get('symbol')}: {token.get('total_return')}%{multiple_text}\n"
    else:
        message += "🏆 *顶级表现者:* 暂无数据\n"
    message += f"\n🆕 *24小时新币 ({len(recent_tokens)}个)*\n"
    if recent_tokens:
        for token in recent_tokens[:3]:
            return_text = f"{token.get('total_return','N/A')}%" if token.get('total_return') else "新币"
            multiple_text = f" ({token.get('price_multiple',1):.2f}x)" if token.get('price_multiple') else ""
            message += f"• {token.get('symbol')}: {return_text}{multiple_text}\n"
    else:
        message += "暂无新币\n"
    message += f"\n⏰ 报告时间: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
    await send_telegram_message(message)

async def generate_performance_report():
    print("\n" + "="*40)
    print("📈 生成代币表现报告")
    print("="*40)
    top_performers = data_manager.get_top_performers(5)
    recent_tokens = data_manager.get_recent_tokens(24)
    if top_performers:
        print("🏆 顶级表现者：")
        for i, t in enumerate(top_performers, 1):
            print(f"  {i}. {t.get('symbol')} - {t.get('total_return')}%")
    else:
        print("🏆 顶级表现者: 无")
    print(f"\n🆕 最近24小时代币数: {len(recent_tokens)}")
    await send_performance_report(top_performers, recent_tokens)

# ========== 环境检查 ==========
def check_environment():
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append('TELEGRAM_BOT_TOKEN')
    if not TELEGRAM_CHAT_ID:
        missing.append('TELEGRAM_CHAT_ID')
    if missing:
        print(f"❌ 缺少环境变量: {', '.join(missing)}")
        print("请在运行环境中设置这些 Secrets（GitHub Actions -> Settings -> Secrets）")
        return False
    return True

# ========== 主流程 ==========
async def test_telegram_connection():
    print("🔍 测试 Telegram 连接...")
    txt = "🔔 BMIS: 测试消息 - 监控已启动（测试）\n时间: " + datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    ok = await send_telegram_message(txt)
    if ok:
        print("✅ Telegram 连接测试成功")
    else:
        print("❌ Telegram 连接测试失败")
    return ok

async def main():
    print("="*60)
    print("=== Base 链智能狙击监控系统（完整版）启动 ===")
    print("="*60)
    # 检查环境
    env_ok = check_environment()
    if not env_ok:
        print("❌ 环境未配置完整，退出")
        return
    # 测试 Telegram
    await test_telegram_connection()
    # 主循环
    try:
        while True:
            start = time.time()
            try:
                ok = await monitor_new_tokens()
                if ok:
                    await generate_performance_report()
            except Exception as e:
                print(f"❌ 监控主流程异常: {e}")
                traceback.print_exc()
            duration = time.time() - start
            print(f"⏱ 本轮耗时: {duration:.1f}s - 下次 5 分钟后执行")
            await asyncio.sleep(300)
    except KeyboardInterrupt:
        print("🛑 手动停止")
    except Exception as e:
        print(f"❌ 未处理异常退出: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
