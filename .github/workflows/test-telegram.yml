#!/usr/bin/env python3
"""
Telegram 配置测试脚本
用于验证 GitHub Secrets 中的 Telegram 配置是否正确
"""

import os
import requests

def test_telegram_connection():
    # 从环境变量获取配置（在GitHub Actions中会自动从Secrets注入）
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    print("🔧 Telegram 配置测试")
    print(f"Bot Token: {'已设置' if bot_token else '未设置'}")
    print(f"Chat ID: {'已设置' if chat_id else '未设置'}")
    
    if not bot_token or not chat_id:
        print("❌ 配置不完整，请检查 GitHub Secrets")
        return False
    
    message = "🔧 GitHub Secrets 配置测试\n\n✅ Telegram 连接成功！\n\n如果收到此消息，说明：\n- Bot Token 正确\n- Chat ID 正确\n- 网络连接正常"
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    
    try:
        print("🔄 正在发送测试消息...")
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            print("✅ Telegram 连接测试成功！")
            print("📱 您应该收到一条测试消息")
            return True
        else:
            print(f"❌ Telegram API 返回错误: {response.status_code}")
            print(f"错误详情: {response.text}")
            return False
    except Exception as e:
        print(f"❌ 连接错误: {e}")
        return False

if __name__ == "__main__":
    test_telegram_connection()
