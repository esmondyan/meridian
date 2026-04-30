# 采集频率（秒）
import os

COLLECT_INTERVAL = 3600  # 1小时一次

# OSRS API
OSRS_GE_API = "https://prices.runescape.wiki/api/v1/osrs/latest"
OSRS_MAPPING_API = "https://prices.runescape.wiki/api/v1/osrs/mapping"
OSRS_5M_API = "https://prices.runescape.wiki/api/v1/osrs/5m"
OSRS_USER_AGENT = "game-price-monitor/1.0"

# G2G
G2G_OSRS_URL = "https://www.g2g.com/old-school-runescape-12583"

# 关注的物品 ID（OSRS 物品 ID）
# 0 = 所有物品，也可以指定关注列表
WATCHED_ITEMS = []  # 空 = 全部

# 数据存储
DATA_DIR = "data"
DB_PATH = "data/prices.db"
APP_DB_PATH = "data/app.db"

# JWT / Auth
# ⚠️ 生产环境请通过环境变量设置，不要硬编码！
JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_EXPIRE_MINUTES = 60
JWT_REFRESH_EXPIRE_DAYS = 30

# FastAPI server
API_HOST = "0.0.0.0"
API_PORT = 8898
