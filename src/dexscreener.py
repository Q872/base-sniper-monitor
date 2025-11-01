import requests
import json
from typing import Dict, List, Optional

class DexScreenerAPI:
    def __init__(self):
        self.base_url = "https://api.dexscreener.com/latest/dex"
    
    def search_tokens(self, query: str = "base", limit: int = 25) -> List[Dict]:
        """搜索Base链上的代币"""
        try:
            response = requests.get(
                f"{self.base_url}/search/?q={query}&limit={limit}",
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return data.get("pairs", [])
        except Exception as e:
            print(f"DexScreener API 搜索失败: {e}")
            return []
