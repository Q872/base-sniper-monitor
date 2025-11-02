import json
import os
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
