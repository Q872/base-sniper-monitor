#!/usr/bin/env python3
"""
Base链智能狙击监控系统 - 风险评分版
基于买卖税和风险项检测的报警系统
支持价格倍数持续报警
"""

import asyncio
import aiohttp
import yaml
import os
import json
import time
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional

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
        current_time = datetime.now().isoformat()
        
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
        
        # 保留最近100条记录
        if len(token_data["price_history"]) > 100:
            token_data["price_history"] = token_data["price_history"][-100:]
        
        # 更新最高/最低价格
        token_data["highest_price"] = max(token_data["highest_price"], price)
        token_data["lowest_price"] = min(token_data["lowest_price"], price)
        token_data["current_price"] = price
        token_data["last_updated"] = current_time
        
        self.save_data(data)
        return token_data
    
    def calculate_returns(self, token_address: str) -> Dict:
        """计算收益率"""
        data = self.load_data()
        if token_address not in data["tokens"]:
            return {}
        
        token_data = data["tokens"][token_address]
        initial_price = token_data["initial_price"]
        current_price = token_data["current_price"]
        
        if initial_price == 0:
            return {}
        
        total_return = ((current_price - initial_price) / initial_price) * 100
        
        # 计算24小时收益率（如果有足够数据）
        twenty_four_hours_ago = datetime.now() - timedelta(hours=24)
        price_24h_ago = initial_price
        
        for price_point in reversed(token_data["price_history"]):
            price_time = datetime.fromisoformat(price_point["timestamp"])
            if price_time <= twenty_four_hours_ago:
                price_24h_ago = price_point["price"]
                break
        
        return_24h = ((current_price - price_24h_ago) / price_24h_ago) * 100 if price_24h_ago > 0 else 0
        
        return {
            "total_return": round(total_return, 2),
            "return_24h": round(return_24h, 2),
            "initial_price": initial_price,
            "current_price": current_price,
            "price_change": current_price - initial_price,
            "price_multiple": current_price / initial_price if initial_price > 0 else 1,
            "highest_return": round(((token_data["highest_price"] - initial_price) / initial_price) * 100, 2),
            "current_liquidity": token_data["price_history"][-1]["liquidity"] if token_data["price_history"] else 0
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
                    "symbol": token_data["symbol"],
                    **returns
                })
        
        # 按总收益率排序
        performers.sort(key=lambda x: x.get("total_return", 0), reverse=True)
        return performers[:limit]
    
    def get_recent_tokens(self, hours: int = 24) -> List[Dict]:
        """获取最近发现的代币"""
        data = self.load_data()
        recent_tokens = []
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        for address, token_data in data["tokens"].items():
            first_seen = datetime.fromisoformat(token_data["first_seen"])
            if first_seen >= cutoff_time:
                returns = self.calculate_returns(address)
                recent_tokens.append({
                    "address": address,
                    "symbol": token_data["symbol"],
                    "first_seen": token_data["first_seen"],
                    **returns
                })
        
        # 按发现时间排序
        recent_tokens.sort(key=lambda x: x["first_seen"], reverse=True)
        return recent_tokens

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
            # 这里需要集成实际的税率检测API
            # 模拟数据：假设检测到买卖税
            buy_tax = token_data.get('buy_tax', 0)
            sell_tax = token_data.get('sell_tax', 0)
            
            if buy_tax > 0.05 or sell_tax > 0.05:  # 5%阈值
                return 3
            elif buy_tax > 0.03 or sell_tax > 0.03:  # 3%警告
                return 1
        except:
            pass
        return 0
    
    def check_contract_verified(self, token_data):
        """检查合约是否验证"""
        # 集成区块浏览器API检查合约验证状态
        return token_data.get('verified', False)
    
    def check_honeypot(self, token_data):
        """Honeypot检测"""
        # 集成Honeypot检测API
        return token_data.get('is_honeypot', False)
    
    def check_lp_lock(self, token_data):
        """LP锁仓检测"""
        lp_lock_days = token_data.get('lp_lock_days', 0)
        if lp_lock_days == 0:
            return 2  # 未锁仓
        elif lp_lock_days < 30:
            return 1  # 锁仓时间短
        return 0
    
    def check_wallet_age(self, token_data):
        """钱包年龄检测"""
        wallet_age_hours = token_data.get('wallet_age_hours', 24)
        return wallet_age_hours < 6
    
    def check_fund_source(self, token_data):
        """资金来源检测"""
        # 检查是否来自混币器或高风险钱包
        return token_data.get('suspicious_source', False)
    
    def check_deployer_history(self, token_data):
        """部署者历史检测"""
        # 检查部署者是否有rug记录
        return token_data.get('has_rug_history', False)
    
    def check_verified_community(self, token_data):
        """检查验证状态和社群"""
        return token_data.get('verified', False) and token_data.get('has_community', False)
    
    def check_cex_source(self, token_data):
        """检查是否来自CEX"""
        return token_data.get('from_cex', False)
    
    def check_holder_distribution(self, token_data):
        """检查持仓分布"""
        top10_holders = token_data.get('top10_holders_percent', 100)
        return top10_holders < 20  # 前10大户持仓 < 20%

class DexScreenerAPI:
    def __init__(self):
        self.base_url = "https://api.dexscreener.com/latest/dex"
    
    async def search_tokens(self, query: str = "base", limit: int = 25):
        """搜索Base链上的代币"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/search/?q={query}&limit={limit}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("pairs", [])
                    else:
                        print(f"❌ DexScreener API 响应异常: {response.status}")
                        return []
        except Exception as e:
            print(f"❌ DexScreener API 搜索失败: {e}")
            return []

class AlertManager:
    def __init__(self):
        self.sent_alerts = set()
        self.alert_cooldown = 3600  # 1小时内不重复风险警报同一代币
    
    def should_send_risk_alert(self, token_address: str, risk_score: int) -> bool:
        """判断是否应该发送风险警报"""
        # 只对中高风险发送警报
        if risk_score <= 6:
            return False
            
        # 检查冷却时间
        current_time = time.time()
        alert_key = f"risk_{token_address}_{risk_score}"
        
        if alert_key in self.sent_alerts:
            # 检查是否超过冷却时间
            if current_time - self.sent_alerts[alert_key] < self.alert_cooldown:
                return False
            else:
                # 超过冷却时间，更新记录
                self.sent_alerts[alert_key] = current_time
                return True
        else:
            # 新警报，记录时间
            self.sent_alerts[alert_key] = current_time
            return True
    
    def should_send_price_alert(self, token_address: str, multiple: int) -> bool:
        """判断是否应该发送价格倍数警报"""
        # 检查是否已发送过该倍数的警报
        sent_alerts = data_manager.get_price_alerts_sent(token_address)
        return multiple not in sent_alerts
    
    def cleanup_old_alerts(self):
        """清理过期的警报记录"""
        current_time = time.time()
        self.sent_alerts = {k: v for k, v in self.sent_alerts.items() 
                           if current_time - v < self.alert_cooldown}

# 初始化管理器
data_manager = TokenDataManager()
alert_manager = AlertManager()

def check_environment():
    """检查必要的环境变量"""
    required_vars = ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ 缺少环境变量: {', '.join(missing_vars)}")
        print("请设置以下环境变量:")
        for var in missing_vars:
            print(f"  export {var}='your_value'")
        return False
    return True

async def send_telegram_message(message):
    """发送Telegram消息"""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    if not bot_token or not chat_id:
        print("⚠️ Telegram配置缺失，跳过发送")
        return False
    
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'Markdown',
                'disable_web_page_preview': True
            }
            async with session.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    print("✅ Telegram消息发送成功")
                    return True
                else:
                    error_text = await response.text()
                    print(f"❌ Telegram发送失败: {response.status} - {error_text}")
                    return False
    except Exception as e:
        print(f"❌ Telegram发送错误: {e}")
        return False

async def send_risk_alert(token_data, risk_score, risk_reasons):
    """发送风险警报，包含具体风险原因"""
    
    # 风险等级判断
    if risk_score <= 6:
        risk_level = "🟢 安全"
        emoji = "✅"
    elif risk_score <= 12:
        risk_level = "🟡 中风险" 
        emoji = "⚠️"
    else:
        risk_level = "🔴 高风险"
        emoji = "🚨"
    
    # 构建风险原因文本
    risk_reasons_text = "\n".join(risk_reasons) if risk_reasons else "暂无风险项"
    
    # 添加收益率信息
    returns_info = ""
    returns = data_manager.calculate_returns(token_data["address"])
    if returns and returns.get("total_return") is not None:
        returns_info = f"📈 当前收益率: {returns.get('total_return', 0):.2f}%\n"
    
    message = f"""{emoji} *BASE链代币风险警报* {emoji}

💰 *{token_data['name']} ({token_data['symbol']})*
📊 风险评分: {risk_score}分 - {risk_level}
{returns_info}
🔍 *检测到的风险项:*
{risk_reasons_text}

💧 流动性: ${token_data['liquidity']:,.0f}
📈 24h交易量: ${token_data['volume']:,.0f}
⏰ 代币年龄: {token_data['age_minutes']}分钟
🔺 24h涨跌: {token_data.get('price_change_24h', 0):.1f}%

📋 合约地址: `{token_data['address']}`
🔗 [DexScreener分析]({token_data['url']})

{'⚠️ 请注意风险，谨慎操作！' if risk_score > 6 else '✅ 相对安全，但仍需自行研究！'}"""
    
    return await send_telegram_message(message)

async def send_price_alert(token_data, multiple: int, current_multiple: float):
    """发送价格倍数警报"""
    
    returns = data_manager.calculate_returns(token_data["address"])
    initial_price = returns.get("initial_price", 0)
    current_price = returns.get("current_price", 0)
    
    # 计算实际涨幅倍数（去掉初始的1倍）
    actual_multiple = multiple - 1
    
    message = f"""🚀 *BASE链代币涨幅警报*

💰 *{token_data['name']} ({token_data['symbol']})*
🎯 已达到 {actual_multiple} 倍涨幅！
📊 当前涨幅: {current_multiple:.2f}x

💰 初始价格: ${initial_price:.6f}
💰 当前价格: ${current_price:.6f}
📈 总收益率: {returns.get('total_return', 0):.2f}%

💧 流动性: ${token_data['liquidity']:,.0f}
📈 24h交易量: ${token_data['volume']:,.0f}
⏰ 代币年龄: {token_data['age_minutes']}分钟

📋 合约地址: `{token_data['address']}`
🔗 [DexScreener分析]({token_data['url']})

🎉 恭喜！代币表现强劲！"""

    success = await send_telegram_message(message)
    if success:
        # 标记该倍数警报已发送
        data_manager.mark_price_alert_sent(token_data["address"], multiple)
        print(f"✅ 已发送 {actual_multiple} 倍价格警报")
    
    return success

def parse_pair_data(pair):
    """正确解析DexScreener返回的数据"""
    try:
        # 处理时间戳
        created_at = pair.get('pairCreatedAt')
        age_minutes = 0
        
        if created_at:
            if isinstance(created_at, str):
                # 如果是ISO格式字符串
                try:
                    created_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    age_minutes = int((datetime.now().timestamp() - created_time.timestamp()) / 60)
                except:
                    # 如果解析失败，使用当前时间
                    age_minutes = 0
            else:
                # 如果是毫秒时间戳
                age_minutes = int((datetime.now().timestamp() * 1000 - created_at) / 60000)
        
        # 确保所有必要字段都有值
        base_token = pair.get('baseToken', {})
        
        return {
            "address": base_token.get('address', ''),
            "name": base_token.get('name', 'Unknown'),
            "symbol": base_token.get('symbol', 'Unknown'),
            "liquidity": pair.get('liquidity', {}).get('usd', 0),
            "volume": pair.get('volume', {}).get('h24', 0),
            "price_change_24h": pair.get('priceChange', {}).get('h24', 0) if pair.get('priceChange') else 0,
            "priceUsd": pair.get('priceUsd', 0),
            "url": pair.get('url', ''),
            "age_minutes": age_minutes,
            "chainId": pair.get('chainId'),
            "dexId": pair.get('dexId')
        }
    except Exception as e:
        print(f"❌ 解析pair数据失败: {e}")
        return None

def validate_token_data(token_data):
    """验证代币数据的完整性"""
    if not token_data:
        return False
        
    required_fields = ['address', 'symbol', 'liquidity']
    for field in required_fields:
        if not token_data.get(field):
            return False
    
    # 流动性太低的不处理
    if token_data.get('liquidity', 0) < 100:  # 降低阈值到$100流动性
        return False
        
    return True

async def analyze_token_with_risk(token_data):
    """使用风险评分系统分析代币"""
    print(f"\n🪙 分析代币: {token_data['symbol']} - {token_data['name']}")
    print(f"   💧 流动性: ${token_data['liquidity']:,}")
    print(f"   📈 24h交易量: ${token_data['volume']:,}")
    print(f"   ⏰ 代币年龄: {token_data['age_minutes']}分钟")
    
    # 记录代币价格
    price = token_data.get('priceUsd', 0) or 0
    data_manager.record_token_price(
        token_data["address"],
        token_data["symbol"],
        price,
        token_data["liquidity"]
    )
    
    # 计算收益率和价格倍数
    returns = data_manager.calculate_returns(token_data["address"])
    
    # 价格倍数警报逻辑
    if returns and returns.get("price_multiple", 1) > 1:
        current_multiple = returns.get("price_multiple", 1)
        print(f"   📊 当前收益率: {returns.get('total_return', 0):.2f}%")
        print(f"   📈 价格倍数: {current_multiple:.2f}x")
        
        # 计算下一个整数倍数（向上取整）
        next_multiple = math.floor(current_multiple) + 1
        
        # 检查所有应该发送警报的倍数
        # 从2倍开始（即涨1倍），然后每增加1倍就报警
        target_multiples = []
        for multiple in range(2, next_multiple + 1):  # 从2开始，因为1倍是初始价格
            if current_multiple >= multiple:
                target_multiples.append(multiple)
        
        # 发送价格倍数警报
        for multiple in target_multiples:
            if alert_manager.should_send_price_alert(token_data["address"], multiple):
                print(f"   🚨 发送 {multiple-1} 倍价格警报 ({multiple}x)")
                await send_price_alert(token_data, multiple, current_multiple)
    
    # 初始化风险评分器
    risk_scorer = RiskScorer()
    
    # 计算风险分数
    risk_score = risk_scorer.calculate_risk_score(token_data)
    
    print(f"   📊 风险评分: {risk_score}分")
    if risk_scorer.risk_reasons:
        print(f"   🔍 风险原因: {', '.join(risk_scorer.risk_reasons)}")
    
    # 检查是否应该发送风险警报
    if alert_manager.should_send_risk_alert(token_data["address"], risk_score):
        print(f"   🚨 发送风险警报")
        await send_risk_alert(token_data, risk_score, risk_scorer.risk_reasons)
    
    return {
        "risk_score": risk_score,
        "risk_reasons": risk_scorer.risk_reasons,
        "quality_tokens": 1 if risk_score <= 6 else 0
    }

async def send_performance_report(top_performers: List, recent_tokens: List):
    """发送性能报告到Telegram"""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    if not bot_token or not chat_id:
        return
    
    message = "📊 *Base链代币表现报告*\n\n"
    
    if top_performers:
        message += "🏆 *顶级表现者:*\n"
        for i, token in enumerate(top_performers, 1):
            multiple_text = f" ({token.get('price_multiple', 1):.2f}x)" if token.get('price_multiple') else ""
            message += f"{i}. {token['symbol']}: {token['total_return']}%{multiple_text}\n"
    else:
        message += "🏆 *顶级表现者:* 暂无数据\n"
    
    message += f"\n🆕 *24小时新币 ({len(recent_tokens)}个)*\n"
    if recent_tokens:
        for token in recent_tokens[:3]:
            return_text = f"{token.get('total_return', 'N/A')}%" if token.get('total_return') else "新币"
            multiple_text = f" ({token.get('price_multiple', 1):.2f}x)" if token.get('price_multiple') else ""
            message += f"• {token['symbol']}: {return_text}{multiple_text}\n"
    else:
        message += "暂无新币\n"
    
    message += f"\n⏰ 报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'Markdown'
            }
            async with session.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    print("✅ 性能报告发送成功")
    except Exception as e:
        print(f"❌ 性能报告发送失败: {e}")

async def generate_performance_report():
    """生成性能报告"""
    print("\n" + "="*50)
    print("📈 代币表现报告")
    print("="*50)
    
    # 获取顶级表现者
    top_performers = data_manager.get_top_performers(5)
    recent_tokens = data_manager.get_recent_tokens(24)
    
    if top_performers:
        print(f"🏆 顶级表现者 (前5):")
        for i, token in enumerate(top_performers, 1):
            multiple_text = f" ({token.get('price_multiple', 1):.2f}x)" if token.get('price_multiple') else ""
            print(f"   {i}. {token['symbol']}: {token['total_return']}%{multiple_text}")
    else:
        print("🏆 顶级表现者: 暂无数据")
    
    print(f"\n🆕 最近24小时发现的代币 ({len(recent_tokens)}个):")
    if recent_tokens:
        for token in recent_tokens[:5]:  # 只显示前5个
            multiple_text = f" ({token.get('price_multiple', 1):.2f}x)" if token.get('price_multiple') else ""
            print(f"   • {token['symbol']}: {token.get('total_return', 'N/A')}%{multiple_text}")
    else:
        print("   暂无新币")
    
    # 发送Telegram报告
    await send_performance_report(top_performers, recent_tokens)

async def api_call_with_retry(session, url, retries=3, delay=1):
    """带重试的API调用"""
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 429:  # Rate limit
                    print(f"⚠️ API频率限制，等待 {delay}秒后重试...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    print(f"❌ API调用失败 (状态码: {response.status})")
                    if attempt < retries - 1:
                        await asyncio.sleep(delay)
                        delay *= 2  # 指数退避
        except Exception as e:
            print(f"API调用失败 (尝试 {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
                delay *= 2  # 指数退避
    return None

async def monitor_new_tokens():
    """监控新币种 - 风险评分版"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"🚀 [{current_time}] 开始监控Base链新币种...")
    
    # 清理旧的警报记录
    alert_manager.cleanup_old_alerts()
    
    # 使用DexScreener API获取数据
    dexscreener = DexScreenerAPI()
    pairs = await dexscreener.search_tokens('base', 25)
    
    if not pairs:
        print("❌ 未从DexScreener获取到数据")
        return False
    
    # 过滤Base链代币
    base_pairs = [pair for pair in pairs if pair.get('chainId') == 'base']
    print(f"📊 从DexScreener获取到 {len(base_pairs)} 个Base链代币")
    
    # 按创建时间排序
    base_pairs.sort(key=lambda x: x.get('pairCreatedAt', 0), reverse=True)
    
    found_quality_tokens = 0
    
    # 分析所有Base链代币
    analysis_tasks = []
    for pair in base_pairs:
        # 解析数据
        token_data = parse_pair_data(pair)
        if not validate_token_data(token_data):
            continue
            
        # 为模拟风险检测，添加一些随机风险数据
        import random
        
        # 添加风险检测相关字段
        token_data.update({
            # 模拟风险检测数据 - 在实际使用中应从API获取真实数据
            "verified": random.choice([True, False, True]),  # 偏向已验证
            "buy_tax": random.uniform(0, 0.08),  # 0-8%的买卖税
            "sell_tax": random.uniform(0, 0.08),
            "is_honeypot": random.choice([False, False, False, True]),  # 低概率honeypot
            "lp_lock_days": random.choice([0, 30, 60, 90, 365]),  # 锁仓天数
            "wallet_age_hours": random.randint(1, 72),  # 钱包年龄
            "suspicious_source": random.choice([False, False, True]),  # 资金来源
            "has_rug_history": random.choice([False, False, False, True]),  # 部署者历史
            "has_community": random.choice([True, False]),  # 社群信息
            "from_cex": random.choice([True, False]),  # CEX来源
            "top10_holders_percent": random.uniform(10, 80)  # 前10大户持仓比例
        })
        
        # 创建分析任务
        task = analyze_token_with_risk(token_data)
        analysis_tasks.append(task)
    
    # 并行执行所有分析任务
    if analysis_tasks:
        results = await asyncio.gather(*analysis_tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                print(f"❌ 分析任务出错: {result}")
                continue
                
            if result and result.get('quality_tokens', 0) > 0:
                found_quality_tokens += result['quality_tokens']
    
    print(f"🎯 本次监控发现 {found_quality_tokens} 个安全项目")
    return True

async def test_telegram_connection():
    """测试Telegram连接"""
    print("🔍 测试Telegram连接...")
    test_message = "🔔 测试消息: Base链监控系统启动成功!\n时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    success = await send_telegram_message(test_message)
    if success:
        print("✅ Telegram连接测试成功")
    else:
        print("❌ Telegram连接测试失败")
    return success

async def main():
    """主函数"""
    print("=" * 50)
    print("=== Base链智能狙击监控系统启动 ===")
    print("=== 风险评分版 - 持续倍数警报 ===")
    print("=" * 50)

    # 环境检查
    if not check_environment():
        print("❌ 环境配置不完整，系统退出")
        return

    # 添加调试信息
    print(f"✅ Telegram配置: Bot Token: {'已设置' if os.getenv('8000293906:AAHHnibFwUtvI4t9ppgUEcMDHyg9B6B3YOo') else '未设置'}")
    print(f"✅ Telegram配置: Chat ID: {'已设置' if os.getenv('1997769382') else '未设置'}")

    # 测试Telegram连接
    telegram_ok = await test_telegram_connection()
    if not telegram_ok:
        print("⚠️ Telegram连接失败，但将继续执行监控...")

    start_time = datetime.now()
    
    # 执行监控
    try:
        success = await monitor_new_tokens()
        if success:
            print("✅ 监控任务执行完成")
            
            # 生成性能报告
            await generate_performance_report()
        else:
            print("❌ 监控任务执行失败")
            
    except Exception as e:
        print(f"❌ 监控任务出错: {e}")
        import traceback
        traceback.print_exc()
    
    duration = (datetime.now() - start_time).total_seconds()
    print(f"⏱️ 总执行时间: {duration:.1f}秒")
    
    print("=" * 50)
    print("=== 系统运行完成，等待下次触发 ===")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(main())
