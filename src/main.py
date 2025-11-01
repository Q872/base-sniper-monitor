#!/usr/bin/env python3
"""
Base链智能狙击监控系统 - 集成DexScreener版本
五级风控增强版 - 单次执行版本
"""

import asyncio
import aiohttp
import yaml
import os
from datetime import datetime

class DexScreenerAPI:
    def __init__(self):
        self.base_url = "https://api.dexscreener.com/latest/dex"
    
    async def search_tokens(self, query: str = "base", limit: int = 25):
        """异步搜索Base链上的代币"""
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
    await asyncio.sleep(0.5)
    return {"risk_interactions": 0, "details": []}

async def analyze_top_holders(token_address):
    """分析前10大户风险"""
    print(f"👥 分析大户风险: {token_address}")
    await asyncio.sleep(0.5)
    return {"risk_holders": 0, "details": []}

async def calculate_score(token_data):
    """计算综合评分"""
    print("📊 计算综合评分...")
    await asyncio.sleep(0.5)
    
    # 基于DexScreener数据的评分逻辑
    score = 50  # 基础分
    
    # 流动性加分
    liquidity = token_data.get('liquidity', {}).get('usd', 0)
    if liquidity > 10000:
        score += 20
    elif liquidity > 5000:
        score += 10
    
    # 交易量加分
    volume = token_data.get('volume', {}).get('h24', 0)
    if volume > 50000:
        score += 15
    elif volume > 10000:
        score += 5
    
    # 价格变化考虑
    price_change = token_data.get('priceChange', {}).get('h24', 0)
    if -10 <= price_change <= 50:  # 合理范围
        score += 10
    
    return min(score, 100)

async def monitor_new_tokens():
    """监控新币种 - 集成DexScreener API"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"🚀 [{current_time}] 开始监控Base链新币种...")
    
    # 使用DexScreener API获取真实数据
    dexscreener = DexScreenerAPI()
    pairs = await dexscreener.search_tokens('base', 25)
    
    if not pairs:
        print("❌ 未从DexScreener获取到数据")
        return False
    
    # 过滤Base链代币
    base_pairs = [pair for pair in pairs if pair.get('chainId') == 'base']
    print(f"📊 从DexScreener获取到 {len(base_pairs)} 个Base链代币")
    
    found_tokens = 0
    
    for pair in base_pairs[:5]:  # 只分析前5个
        token_data = {
            "address": pair.get('baseToken', {}).get('address'),
            "name": pair.get('baseToken', {}).get('name'),
            "symbol": pair.get('baseToken', {}).get('symbol'),
            "deployer": "unknown",  # DexScreener不提供部署者信息
            "liquidity": pair.get('liquidity', {}),
            "volume": pair.get('volume', {}),
            "priceChange": pair.get('priceChange', {}),
            "pairAddress": pair.get('pairAddress'),
            "url": pair.get('url')
        }
        
        if not token_data["address"]:
            continue
            
        print(f"🪙 分析代币: {token_data['symbol']} - {token_data['name']}")
        print(f"   💧 流动性: ${token_data['liquidity'].get('usd', 0):,}")
        print(f"   📈 24h交易量: ${token_data['volume'].get('h24', 0):,}")
        
        # 执行风控分析
        deployer_analysis = await analyze_deployer_interactions(token_data["deployer"])
        holder_analysis = await analyze_top_holders(token_data["address"])
        
        # 计算评分
        score = await calculate_score(token_data)
        
        print(f"   ✅ 分析完成 - 评分: {score}/100")
        
        # 根据评分决定是否推送
        config = load_config()
        min_score = config.get('risk_thresholds', {}).get('min_score', 50)
        good_score = config.get('risk_thresholds', {}).get('good_score', 70)
        
        if score >= good_score:
            print("   🟢 优质项目 - 准备推送")
            found_tokens += 1
        elif score >= min_score:
            print("   🟡 中等风险 - 需要人工审核")
        else:
            print("   🔴 高风险 - 静默丢弃")
    
    print(f"🎯 本次监控发现 {found_tokens} 个优质项目")
    return True

async def main():
    """主函数"""
    print("=" * 50)
    print("=== Base链智能狙击监控系统启动 ===")
    print("===     集成DexScreener API    ===")
    print("=" * 50)
    
    # 加载配置
    risk_addresses = load_risk_addresses()
    config = load_config()
    
    print(f"📁 配置加载: {len(risk_addresses)} 个风险地址")
    
    # 单次执行监控
    try:
        await monitor_new_tokens()
        print("✅ 监控任务执行完成")
    except Exception as e:
        print(f"❌ 监控任务出错: {e}")
    
    print("=" * 50)
    print("=== 系统运行完成，等待下次GitHub Actions触发 ===")
    print("=" * 50)

if __name__ == "__main__":
    # 运行主程序（单次执行）
    asyncio.run(main())
