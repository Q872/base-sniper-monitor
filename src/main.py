#!/usr/bin/env python3
"""
Base链智能狙击监控系统 - 主程序
五级风控增强版 - 集成DexScreener API和价格追踪
"""

import asyncio
import aiohttp
import yaml
import os
import json
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
                "initial_liquidity": liquidity
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
            "highest_return": round(((token_data["highest_price"] - initial_price) / initial_price) * 100, 2),
            "current_liquidity": token_data["price_history"][-1]["liquidity"] if token_data["price_history"] else 0
        }
    
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
                    data = await response.json()
                    return data.get("pairs", [])
        except Exception as e:
            print(f"DexScreener API 搜索失败: {e}")
            return []

# 初始化数据管理器
data_manager = TokenDataManager()

def load_config():
    """加载配置文件"""
    try:
        with open('config.yaml', 'r', encoding='utf-8') as file:
            config = yaml.safe_load(file)
            print("✅ 配置文件加载成功")
            return config
    except Exception as e:
        print(f"❌ 配置文件加载失败: {e}")
        return {}

def load_risk_addresses():
    """加载风险地址数据库"""
    try:
        with open('data/risk_addresses.txt', 'r') as f:
            addresses = set(line.strip().lower() for line in f if line.strip())
            print(f"✅ 风险地址数据库加载成功: {len(addresses)} 个地址")
            return addresses
    except FileNotFoundError:
        print("⚠️ 风险地址数据库未找到，将使用空数据库")
        return set()

async def analyze_deployer_interactions(deployer_address):
    """分析部署者交互历史"""
    print(f"🔍 分析部署者交互: {deployer_address}")
    await asyncio.sleep(0.5)  # 模拟API调用
    
    # 这里可以集成实际的链上分析API
    return {
        "risk_interactions": 0, 
        "details": [],
        "deployer_risk_score": 30
    }

async def analyze_top_holders(token_address):
    """分析前10大户风险"""
    print(f"👥 分析大户风险: {token_address}")
    await asyncio.sleep(0.5)  # 模拟API调用
    
    return {
        "risk_holders": 0, 
        "details": [],
        "holder_concentration": 25
    }

async def calculate_score(token_data, deployer_analysis, holder_analysis):
    """计算综合评分"""
    print("📊 计算综合评分...")
    
    score = 50  # 基础分
    
    # 1. 流动性评分 (25分)
    liquidity = token_data.get('liquidity', {}).get('usd', 0)
    if liquidity > 20000:
        score += 25
    elif liquidity > 10000:
        score += 20
    elif liquidity > 5000:
        score += 15
    elif liquidity > 1000:
        score += 5
    
    # 2. 交易量评分 (15分)
    volume = token_data.get('volume', {}).get('h24', 0)
    if volume > 50000:
        score += 15
    elif volume > 20000:
        score += 10
    elif volume > 5000:
        score += 5
    
    # 3. 部署者风险评分 (20分)
    deployer_score = deployer_analysis.get('deployer_risk_score', 50)
    score += (deployer_score - 50) * 0.4  # 转换为20分制
    
    # 4. 大户集中度评分 (15分)
    holder_score = 50 - holder_analysis.get('holder_concentration', 0)
    score += holder_score * 0.3  # 转换为15分制
    
    # 5. 代币年龄加分 (10分)
    age_minutes = token_data.get('age_minutes', 1440)  # 默认1天
    if age_minutes <= 30:  # 30分钟内的新币
        score += 10
    elif age_minutes <= 120:  # 2小时内的币
        score += 5
    
    # 6. 价格稳定性 (10分)
    price_change = abs(token_data.get('priceChange', {}).get('h24', 0))
    if price_change < 50:  # 24小时涨跌幅小于50%
        score += 10
    elif price_change < 100:
        score += 5
    
    return min(max(score, 0), 100)

async def send_telegram_alert(token_data, score):
    """发送Telegram警报 - 增强版，包含收益率信息"""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    if not bot_token or not chat_id:
        print("⚠️ Telegram配置缺失，跳过发送")
        return
    
    # 添加收益率信息
    returns_info = ""
    if token_data.get("returns"):
        returns = token_data["returns"]
        returns_info = f"📈 当前收益率: {returns.get('total_return', 0):.2f}%\n"
    
    message = f"""🚨 *BASE链优质代币警报* 🚨

💰 *{token_data['name']} ({token_data['symbol']})*
🏆 综合评分: {score}/100
{returns_info}💧 流动性: ${token_data['liquidity']:,.0f}
📊 24h交易量: ${token_data['volume']:,.0f}
⏰ 代币年龄: {token_data['age_minutes']}分钟
🔺 24h涨跌: {token_data.get('price_change_24h', 0):.1f}%

📋 合约地址: `{token_data['address']}`
🔗 [DexScreener分析]({token_data['url']})

⚠️ 投资有风险，请自行研究！"""
    
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
                json=payload
            ) as response:
                if response.status == 200:
                    print(f"✅ Telegram警报发送成功: {token_data['symbol']}")
                else:
                    print(f"❌ Telegram发送失败: {await response.text()}")
    except Exception as e:
        print(f"❌ Telegram发送错误: {e}")

async def send_performance_report(top_performers: List, recent_tokens: List):
    """发送性能报告到Telegram"""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    if not bot_token or not chat_id:
        return
    
    message = "📊 *Base链代币表现报告*\n\n"
    
    message += "🏆 *顶级表现者:*\n"
    for i, token in enumerate(top_performers, 1):
        message += f"{i}. {token['symbol']}: {token['total_return']}%\n"
    
    message += f"\n🆕 *24小时新币 ({len(recent_tokens)}个)*\n"
    for token in recent_tokens[:3]:
        return_text = f"{token.get('total_return', 'N/A')}%" if token.get('total_return') else "新币"
        message += f"• {token['symbol']}: {return_text}\n"
    
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
                json=payload
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
    
    print(f"🏆 顶级表现者 (前5):")
    for i, token in enumerate(top_performers, 1):
        print(f"   {i}. {token['symbol']}: {token['total_return']}%")
    
    print(f"\n🆕 最近24小时发现的代币 ({len(recent_tokens)}个):")
    for token in recent_tokens[:5]:  # 只显示前5个
        print(f"   • {token['symbol']}: {token.get('total_return', 'N/A')}%")
    
    # 发送Telegram报告
    await send_performance_report(top_performers, recent_tokens)

async def analyze_token(token_data):
    """分析单个代币"""
    print(f"\n🪙 分析代币: {token_data['symbol']} - {token_data['name']}")
    print(f"   💧 流动性: ${token_data['liquidity']:,}")
    print(f"   📈 24h交易量: ${token_data['volume']:,}")
    print(f"   ⏰ 代币年龄: {token_data['age_minutes']}分钟")
    
    # 记录代币价格
    # 注意：这里需要获取实际价格，DexScreener API返回的价格字段可能是priceUsd
    price = token_data.get('priceUsd', 0) or 0
    data_manager.record_token_price(
        token_data["address"],
        token_data["symbol"],
        price,
        token_data["liquidity"]
    )
    
    # 计算收益率
    returns = data_manager.calculate_returns(token_data["address"])
    if returns:
        print(f"   📊 当前收益率: {returns.get('total_return', 0):.2f}%")
    
    # 原有的分析逻辑保持不变
    deployer_task = analyze_deployer_interactions(token_data["deployer"])
    holder_task = analyze_top_holders(token_data["address"])
    
    deployer_analysis, holder_analysis = await asyncio.gather(deployer_task, holder_task)
    
    # 计算综合评分
    score = await calculate_score(token_data, deployer_analysis, holder_analysis)
    
    print(f"   ✅ 分析完成 - 评分: {score}/100")
    
    # 根据评分决定是否推送
    config = load_config()
    min_score = config.get('risk_thresholds', {}).get('min_score', 60)
    good_score = config.get('risk_thresholds', {}).get('good_score', 75)
    
    quality_tokens = 0
    
    if score >= 50:
        print("   🟢 优质项目 - 发送警报")
        # 在警报中添加收益率信息
        if returns:
            token_data["returns"] = returns
        await send_telegram_alert(token_data, score)
        quality_tokens = 1
    elif score >= min_score:
        print("   🟡 中等风险 - 需要人工审核")
    else:
        print("   🔴 高风险 - 静默丢弃")
    
    return {"quality_tokens": quality_tokens}

async def monitor_new_tokens():
    """监控新币种 - 完整功能版，分析所有获取到的代币"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"🚀 [{current_time}] 开始监控Base链新币种...")
    
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
    
    # 分析所有Base链代币（不限制数量）
    analysis_tasks = []
    for pair in base_pairs:  # 没有数量限制，分析所有代币
        token_data = {
            "address": pair.get('baseToken', {}).get('address'),
            "name": pair.get('baseToken', {}).get('name', 'Unknown'),
            "symbol": pair.get('baseToken', {}).get('symbol', 'Unknown'),
            "deployer": pair.get('baseToken', {}).get('address'),
            "liquidity": pair.get('liquidity', {}).get('usd', 0),
            "volume": pair.get('volume', {}).get('h24', 0),
            "priceChange": pair.get('priceChange', {}),
            "price_change_24h": pair.get('priceChange', {}).get('h24', 0),
            "priceUsd": pair.get('priceUsd', 0),
            "pairAddress": pair.get('pairAddress'),
            "url": pair.get('url', ''),
            "age_minutes": int((datetime.now().timestamp() * 1000 - pair.get('pairCreatedAt', 0)) / 60000)
        }
        
        if not token_data["address"]:
            continue
            
        # 创建分析任务
        task = analyze_token(token_data)
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
    
    print(f"🎯 本次监控发现 {found_quality_tokens} 个优质项目")
    return True

async def main():
    """主函数"""
    print("=" * 50)
    print("=== Base链智能狙击监控系统启动 ===")
    print("=== 完整功能版 + 价格追踪 ===")
    print("=" * 50)

        print("=" * 50)
    print("=== Base链智能狙击监控系统启动 ===")
    print("=== 完整功能版 + 价格追踪 ===")
    print("=" * 50)

    # 🔧 添加在这里 - 强制测试 Telegram 连接
    print("🔧 强制测试 Telegram 连接...")
    test_token_data = {
        "name": "测试代币",
        "symbol": "TEST",
        "liquidity": 10000,
        "volume": 50000,
        "age_minutes": 5,
        "price_change_24h": 10.5,
        "address": "0xTEST123456789",
        "url": "https://dexscreener.com/base/0xTEST"
    }
    await send_telegram_alert(test_token_data, 80)
    print("✅ 强制测试消息已发送")

    start_time = datetime.now()
    
    # 加载配置和风险数据库
    risk_addresses = load_risk_addresses()
    config = load_config()
    
    print(f"📁 配置加载: {len(risk_addresses)} 个风险地址")
    
    # 执行监控
    try:
        await monitor_new_tokens()
        print("✅ 监控任务执行完成")
        
        # 生成性能报告
        await generate_performance_report()
        
    except Exception as e:
        print(f"❌ 监控任务出错: {e}")
    
    duration = (datetime.now() - start_time).total_seconds()
    print(f"⏱️ 总执行时间: {duration:.1f}秒")
    
    print("=" * 50)
    print("=== 系统运行完成，等待下次触发 ===")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(main())
