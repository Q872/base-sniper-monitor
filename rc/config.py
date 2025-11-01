import yaml
import os

def load_config():
    """加载配置文件"""
    try:
        with open('config.yaml', 'r', encoding='utf-8') as file:
            return yaml.safe_load(file)
    except Exception as e:
        print(f"配置文件加载失败: {e}")
        return {}

def get_api_key():
    """从环境变量获取API密钥"""
    return os.getenv('BASESCAN_API_KEY', '')

# 全局配置
CONFIG = load_config()
API_KEY = get_api_key()
