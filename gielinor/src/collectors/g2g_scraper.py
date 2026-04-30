"""
G2G 交易平台价格抓取器

技术：Playwright 无头浏览器
目标：抓取 OSRS 金币挂单价格
注意：G2G 有反爬，频率别太高
"""

from pathlib import Path
from datetime import datetime

from playwright.sync_api import sync_playwright
import pandas as pd

from config.settings import G2G_OSRS_URL, DATA_DIR


def scrape_g2g_osrs(max_listings: int = 50) -> list[dict]:
    """抓取 G2G 上 OSRS 金币的挂单列表"""
    listings = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        print(f"[{datetime.now():%H:%M:%S}] 打开 G2G...")
        page.goto(G2G_OSRS_URL, timeout=30000)
        page.wait_for_timeout(5000)

        # 抓取挂单卡片
        items = page.query_selector_all('[class*="listing"]')
        for item in items[:max_listings]:
            try:
                title_el = item.query_selector('[class*="title"]')
                price_el = item.query_selector('[class*="price"]')

                title = title_el.inner_text() if title_el else ""
                price = price_el.inner_text() if price_el else ""

                if title or price:
                    listings.append({
                        "platform": "g2g",
                        "title": title.strip(),
                        "price": price.strip(),
                        "timestamp": datetime.now().isoformat(),
                    })
            except Exception:
                continue

        browser.close()

    print(f"  抓取到 {len(listings)} 条挂单")
    return listings


def save_listings(listings: list[dict]):
    if not listings:
        return

    path = Path(DATA_DIR) / "g2g_listings.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(listings)
    header = not path.exists()
    df.to_csv(path, mode="a", header=header, index=False)
    print(f"  已保存 → {path}")


def run_once():
    print(f"[{datetime.now():%H:%M:%S}] 开始抓取 G2G...")
    try:
        listings = scrape_g2g_osrs()
        save_listings(listings)
    except Exception as e:
        print(f"❌ G2G 抓取失败: {e}")


if __name__ == "__main__":
    run_once()
