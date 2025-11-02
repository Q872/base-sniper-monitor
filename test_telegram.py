#!/usr/bin/env python3
"""
Telegram 配置测试脚本
"""

import os
import requests
import sys

def test_telegram_connection():
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    print("🔧 Telegram 配置测试开始")
    print(f"Bot Token: {'已设置' if bot_token else '未设置'}")
    print(f"Chat ID: {'已设置' if chat_id else '未设置'}")
    
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN 未设置")
        return False
        
    if not chat_id:
        print("❌ TELEGRAM_CHAT_ID 未设置")
        return False
    
    # 测试消息
    message = "🔧 GitHub Actions Telegram 测试\n\n✅ 如果收到此消息，说明配置正确！"
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    
    try:
        print("🔄 正在发送测试消息到 Telegram...")
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            print("✅ Telegram 连接测试成功！")
            return True
        else:
            print(f"❌ Telegram API 返回错误: {response.status_code}")
            print(f"错误详情: {response.text}")
            return False
    except Exception as e:
        print(f"❌ 连接错误: {e}")
        return False

if __name__ == "__main__":
    success = test_telegram_connection()
    sys.exit(0 if success else 1)
