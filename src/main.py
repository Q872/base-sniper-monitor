# -*- coding: utf-8 -*-
# ============================================================
# main.py (Base链MEME监控机器人)
# 作者: Olaf & GPT-5
# 功能: 实时监控DexScreener新代币，自动筛选/评分/推送
# ============================================================
import os
import time
import asyncio
import aiohttp
import requests
from datetime import datetime
from typing import Dict, Any, List

# ------------------------------------------------------------
# 🧩 全局配置
# ------------------------------------------------------------
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/pairs/base"
HONEYPOT_API = "https://api.honeypot.is/v2/IsHoneypot"
BASESCAN_API = "https://api.basescan.org/api"
MIN_LP_USD = 5000.0  # 💰 流动性小于此值则忽略
POLL_INTERVAL = 45  # 秒级扫描频率
SCORE_HIGH = 13
SCORE_LOW = 6

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 追踪已推送池避免重复
notified_pairs = set()

# ------------------------------------------------------------
# 🧠 工具函数：Telegram消息推送
# ------------------------------------------------------------
async def send_telegram_message(session: aiohttp.ClientSession, text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ 未设置 Telegram 环境变量，跳过推送。")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        async with session.post(url, data=payload) as resp:
            if resp.status == 200:
                print(f"✅ 推送成功: {text[:40]}...")
            else:
                print(f"❌ 推送失败: {resp.status}")
    except Exception as e:
        print(f"❌ Telegram 错误: {e}")

# ------------------------------------------------------------
# 🧩 DexScreener拉取函数（实时Base链）
# ------------------------------------------------------------
async def fetch_latest_pairs(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    try:
        async with session.get(DEXSCREENER_API, timeout=10) as resp:
            data = await resp.json()
            if "pairs" not in data:
                print("⚠️ DexScreener无pairs字段")
                return []
            return data["pairs"]
    except Exception as e:
        print(f"⚠️ 拉取DexScreener失败: {e}")
        return []

# ------------------------------------------------------------
# 🧮 LP过滤与初步筛选
# ------------------------------------------------------------
def filter_pairs(pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤流动性过低或重复推送的代币"""
    valid = []
    for p in pairs:
        try:
            pair_address = p.get("pairAddress")
            liquidity_usd = float(p.get("liquidity", {}).get("usd", 0))
            if pair_address in notified_pairs:
                continue
            if liquidity_usd < MIN_LP_USD:
                continue
            valid.append(p)
        except Exception:
            continue
    return valid

# ------------------------------------------------------------
# 🧩 转换为标准代币信息
# ------------------------------------------------------------
def parse_pair_to_meta(p: Dict[str, Any]) -> Dict[str, Any]:
    """统一代币数据格式"""
    try:
        base_token = p.get("baseToken", {})
        quote_token = p.get("quoteToken", {})
        info = {
            "pairAddress": p.get("pairAddress"),
            "dexId": p.get("dexId"),
            "baseName": base_token.get("name"),
            "baseSymbol": base_token.get("symbol"),
            "baseAddress": base_token.get("address"),
            "quoteSymbol": quote_token.get("symbol"),
            "priceUsd": p.get("priceUsd"),
            "liquidityUsd": p.get("liquidity", {}).get("usd", 0),
            "fdv": p.get("fdv"),
            "pairCreatedAt": datetime.utcfromtimestamp(int(p.get("pairCreatedAt", 0)) / 1000).strftime("%Y-%m-%d %H:%M:%S"),
            "url": f"https://dexscreener.com/base/{p.get('pairAddress')}",
        }
        return info
    except Exception as e:
        print(f"⚠️ parse_pair_to_meta失败: {e}")
        return {}
# ------------------------------------------------------------
# 🐝 Honeypot检测模块（防貔貅逻辑）
# ------------------------------------------------------------
async def check_honeypot(session: aiohttp.ClientSession, token_address: str) -> Dict[str, Any]:
    """调用 Honeypot API 检查是否为陷阱代币"""
    try:
        url = f"{HONEYPOT_API}?address={token_address}&chain=base"
        async with session.get(url, timeout=10) as resp:
            data = await resp.json()
            result = {
                "is_honeypot": data.get("honeypotResult", {}).get("isHoneypot", False),
                "buy_tax": data.get("simulationResult", {}).get("buyTax", 0),
                "sell_tax": data.get("simulationResult", {}).get("sellTax", 0),
                "transfer_tax": data.get("simulationResult", {}).get("transferTax", 0),
            }
            return result
    except Exception as e:
        print(f"⚠️ Honeypot检测错误: {e}")
        return {"is_honeypot": False, "buy_tax": 0, "sell_tax": 0, "transfer_tax": 0}

# ------------------------------------------------------------
# 🔍 BaseScan 合约开源检测
# ------------------------------------------------------------
async def check_contract_verified(session: aiohttp.ClientSession, contract_address: str) -> bool:
    """检查合约是否已在BaseScan上开源"""
    try:
        params = {
            "module": "contract",
            "action": "getsourcecode",
            "address": contract_address,
        }
        async with session.get(BASESCAN_API, params=params, timeout=10) as resp:
            data = await resp.json()
            if data.get("status") == "1" and data.get("result"):
                result = data["result"][0]
                return result.get("SourceCode") != ""
    except Exception as e:
        print(f"⚠️ 合约开源检测错误: {e}")
    return False

# ------------------------------------------------------------
# 🧮 风险评分系统
# ------------------------------------------------------------
def calculate_score(meta: Dict[str, Any], honeypot: Dict[str, Any], verified: bool) -> int:
    """根据多维度计算风险得分"""
    score = 0

    # 流动性越高分数越低（安全）
    try:
        lp = float(meta.get("liquidityUsd", 0))
        if lp < 10000:
            score += 4
        elif lp < 30000:
            score += 2
        else:
            score += 0
    except:
        score += 2

    # 新建池子增加风险
    try:
        created_at = datetime.strptime(meta.get("pairCreatedAt"), "%Y-%m-%d %H:%M:%S")
        delta_minutes = (datetime.utcnow() - created_at).total_seconds() / 60
        if delta_minutes < 30:
            score += 5
        elif delta_minutes < 120:
            score += 3
    except:
        score += 2

    # Honeypot检测加分
    if honeypot.get("is_honeypot"):
        score += 8
    if honeypot.get("buy_tax", 0) > 5 or honeypot.get("sell_tax", 0) > 5:
        score += 2

    # 未开源加风险
    if not verified:
        score += 3

    # FDV异常
    fdv = meta.get("fdv", 0)
    if fdv and isinstance(fdv, (int, float)):
        if fdv < 100000:
            score += 2
        elif fdv > 50000000:
            score += 2

    # DEX类型（部分DEX风险较高）
    dex = (meta.get("dexId") or "").lower()
    if any(x in dex for x in ["unknown", "unverified", "sushiswap", "shadow"]):
        score += 3

    return score

# ------------------------------------------------------------
# 🧩 风险等级标签
# ------------------------------------------------------------
def classify_project(score: int) -> str:
    if score >= SCORE_HIGH:
        return "❌ 高风险"
    elif score >= SCORE_LOW:
        return "⚠️ 中风险"
    else:
        return "✅ 优质"

# ------------------------------------------------------------
# 🤖 检测是否被其他机器人推荐
# ------------------------------------------------------------
async def check_other_bot_recommendation(session: aiohttp.ClientSession, token_address: str) -> bool:
    """
    检查代币是否被已知狙击机器人推荐：
    这里通过访问第三方DEX追踪源/推特Bot列表（模拟）
    """
    try:
        bot_sources = [
            "https://api.dexscreener.com/latest/dex/tokens/",
            "https://api.dexscreener.com/latest/dex/pairs/",
        ]
        for base in bot_sources:
            async with session.get(base + token_address, timeout=5) as resp:
                data = await resp.json()
                # 如果返回包含推荐字段
                if "pair" in data or "pairs" in data:
                    if "bot" in str(data).lower() or "recommend" in str(data).lower():
                        return True
    except Exception:
        return False
    return False

# ------------------------------------------------------------
# 🌍 检测社交媒体信息（官网 / Telegram / X）
# ------------------------------------------------------------
async def fetch_social_links(session: aiohttp.ClientSession, token_address: str) -> Dict[str, str]:
    """
    获取代币的社交媒体链接
    DexScreener部分pairs数据中含有socials
    """
    socials = {"website": "-", "telegram": "-", "twitter": "-"}
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        async with session.get(url, timeout=8) as resp:
            data = await resp.json()
            if "pairs" in data:
                first = data["pairs"][0]
                if "info" in first and "socials" in first["info"]:
                    for s in first["info"]["socials"]:
                        name = s.get("type", "").lower()
                        link = s.get("url", "")
                        if "tg" in name or "telegram" in name:
                            socials["telegram"] = link
                        elif "x" in name or "twitter" in name:
                            socials["twitter"] = link
                        elif "site" in name or "web" in name:
                            socials["website"] = link
    except Exception as e:
        print(f"⚠️ fetch_social_links错误: {e}")
    return socials

# ------------------------------------------------------------
# 🧩 汇总分析并生成推送文本
# ------------------------------------------------------------
def build_notification(meta: Dict[str, Any],
                       score: int,
                       risk_label: str,
                       honeypot: Dict[str, Any],
                       verified: bool,
                       socials: Dict[str, str],
                       bot_recommend: bool) -> str:
    """
    生成最终推送消息文本（HTML格式）
    """
    base_name = meta.get("baseName", "未知代币")
    symbol = meta.get("baseSymbol", "")
    lp = meta.get("liquidityUsd", 0)
    price = meta.get("priceUsd", 0)
    fdv = meta.get("fdv", 0)
    pair_url = meta.get("url", "")
    pair_time = meta.get("pairCreatedAt", "")

    verified_tag = "✅ 开源" if verified else "❌ 未开源"
    honeypot_tag = "⚠️ 可疑" if honeypot.get("is_honeypot") else "✅ 安全"
    bot_tag = "🤖 Bot推荐" if bot_recommend else "🧭 未见推荐"

    socials_str = f"🌐 <a href='{socials.get('website', '-')}'>{socials.get('website','-')}</a>\n" \
                  f"🐦 <a href='{socials.get('twitter', '-')}'>{socials.get('twitter','-')}</a>\n" \
                  f"💬 <a href='{socials.get('telegram', '-')}'>{socials.get('telegram','-')}</a>"

    msg = f"""
🚀 <b>{base_name} ({symbol})</b>
💰 价格: ${price:.6f}
📊 FDV: ${fdv:,}
💦 流动性: ${lp:,.0f}
📅 创建时间: {pair_time}

🔍 状态: {risk_label}
🧮 评分: {score}
{verified_tag} | {honeypot_tag} | {bot_tag}

{socials_str}

🔗 <a href='{pair_url}'>查看DexScreener</a>
    """
    return msg

# ------------------------------------------------------------
# 🚀 主逻辑：监控与推送
# ------------------------------------------------------------
async def process_pairs(session: aiohttp.ClientSession, pairs: List[Dict[str, Any]]):
    global notified_pairs

    for pair in pairs:
        meta = parse_pair_to_meta(pair)
        token_address = meta.get("baseAddress")
        if not token_address or meta.get("pairAddress") in notified_pairs:
            continue

        try:
            # 🐝 1. Honeypot检测
            honeypot_result = await check_honeypot(session, token_address)

            # 🔍 2. 合约是否开源
            verified = await check_contract_verified(session, token_address)

            # 🤖 3. 是否被其他机器人推荐
            bot_recommend = await check_other_bot_recommendation(session, token_address)

            # 🌐 4. 社交信息
            socials = await fetch_social_links(session, token_address)

            # 🧮 5. 风险评分
            score = calculate_score(meta, honeypot_result, verified)
            risk_label = classify_project(score)

            # ❌ 高风险不推送
            if score >= SCORE_HIGH:
                print(f"🚫 {meta.get('baseSymbol')} 高风险（得分{score}），跳过")
                notified_pairs.add(meta.get("pairAddress"))
                continue

            # ✅ 构建推送消息
            message = build_notification(meta, score, risk_label, honeypot_result, verified, socials, bot_recommend)
            await send_telegram_message(session, message)

            notified_pairs.add(meta.get("pairAddress"))

            # 推送后稍作间隔
            await asyncio.sleep(2)

        except Exception as e:
            print(f"⚠️ process_pairs错误: {e}")
            continue


# ------------------------------------------------------------
# 🌀 主循环任务
# ------------------------------------------------------------
async def monitor_loop():
    print("🚀 开始监控 Base 链 MEME 项目 ...")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                pairs = await fetch_latest_pairs(session)
                valid_pairs = filter_pairs(pairs)
                if valid_pairs:
                    print(f"📡 本次检测到 {len(valid_pairs)} 个新池")
                    await process_pairs(session, valid_pairs)
                else:
                    print("⏳ 无新池或不满足条件")
            except Exception as e:
                print(f"⚠️ monitor_loop异常: {e}")

            # 每轮间隔
            await asyncio.sleep(POLL_INTERVAL)


# ------------------------------------------------------------
# 🧰 启动入口（兼容 GitHub Actions）
# ------------------------------------------------------------
def main():
    try:
        asyncio.run(monitor_loop())
    except KeyboardInterrupt:
        print("🛑 手动终止监控")
    except Exception as e:
        print(f"⚠️ 程序异常退出: {e}")
        time.sleep(5)
        main()  # 自动重启机制


if __name__ == "__main__":
    main()

# =============================================================
# ✅ 版本说明：
# - 实时拉取DexScreener Base新币
# - 过滤LP<5000项目
# - Honeypot检测 + BaseScan开源验证
# - 综合评分（<6优质 / 6~12中风险 / ≥13高危不推送）
# - 机器人推荐检测 + 社交链接获取
# - 支持Telegram推送（环境变量）
# =============================================================
