"""Research discovery catalog. IDs are internal; vendor contract mappings are explicit."""

SOURCES = {
    "CFFEX": "https://www.cffex.com.cn/cn/yshq.html",
    "CME": "https://www.cmegroup.com/markets/products.html",
    "CBOT": "https://www.cmegroup.com/markets/products.html",
    "NYMEX": "https://www.cmegroup.com/markets/products.html",
    "COMEX": "https://www.cmegroup.com/markets/products.html",
    "EUREX": "https://www.eurex.com/ex-en/markets/idx",
    "HKEX": "https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-Index-%28HSI%29/Hang-Seng-Index-Futures?sc_lang=en",
    "OSE": "https://www.jpx.co.jp/english/derivatives/products/list/index.html",
    "NSE": "https://www.nseindia.com/static/products-services/equity-derivatives-nifty50",
    "ICE_EU": "https://www.ice.com/products",
    "ICE_US": "https://www.ice.com/products",
}

# No multiplier/tick/margin defaults: historical rule versions belong to each
# contract, not a timeless global product entry. These are planning identifiers.
PRODUCTS = [
    ("CFFEX", "IF", "CSI 300", "equity_index", "CNY"),
    ("CFFEX", "IH", "SSE 50", "equity_index", "CNY"),
    ("CFFEX", "IC", "CSI 500", "equity_index", "CNY"),
    ("CFFEX", "IM", "CSI 1000", "equity_index", "CNY"),
    ("CME", "ES", "E-mini S&P 500", "equity_index", "USD"),
    ("CME", "NQ", "E-mini Nasdaq 100", "equity_index", "USD"),
    ("CME", "RTY", "E-mini Russell 2000", "equity_index", "USD"),
    ("CBOT", "YM", "E-mini Dow", "equity_index", "USD"),
    ("EUREX", "FESX", "EURO STOXX 50", "equity_index", "EUR"),
    ("EUREX", "FDAX", "DAX", "equity_index", "EUR"),
    ("ICE_EU", "FTSE100", "FTSE 100", "equity_index", "GBP"),
    ("HKEX", "HSI", "Hang Seng", "equity_index", "HKD"),
    ("OSE", "NIKKEI225", "Nikkei 225 large", "equity_index", "JPY"),
    ("OSE", "TOPIX", "TOPIX", "equity_index", "JPY"),
    ("NSE", "NIFTY", "NIFTY 50", "equity_index", "INR"),
    ("NYMEX", "CL", "WTI crude oil", "energy", "USD"),
    ("NYMEX", "NG", "Henry Hub natural gas", "energy", "USD"),
    ("NYMEX", "RB", "RBOB gasoline", "energy", "USD"),
    ("NYMEX", "HO", "NY Harbor ULSD", "energy", "USD"),
    ("ICE_EU", "BRENT", "Brent crude oil", "energy", "USD"),
    ("COMEX", "GC", "Gold", "metals", "USD"),
    ("COMEX", "SI", "Silver", "metals", "USD"),
    ("COMEX", "HG", "Copper", "metals", "USD"),
    ("NYMEX", "PL", "Platinum", "metals", "USD"),
    ("NYMEX", "PA", "Palladium", "metals", "USD"),
    ("CBOT", "ZC", "Corn", "agriculture", "USD"),
    ("CBOT", "ZW", "Chicago wheat", "agriculture", "USD"),
    ("CBOT", "ZS", "Soybeans", "agriculture", "USD"),
    ("CBOT", "ZM", "Soybean meal", "agriculture", "USD"),
    ("CBOT", "ZL", "Soybean oil", "agriculture", "USD"),
    ("ICE_US", "SUGAR11", "Sugar No. 11", "agriculture", "USD"),
    ("ICE_US", "COFFEE_C", "Coffee C", "agriculture", "USD"),
    ("ICE_US", "COTTON2", "Cotton No. 2", "agriculture", "USD"),
    ("ICE_US", "COCOA", "Cocoa", "agriculture", "USD"),
]


def universe():
    return {
        "schema": "qfs.research-universe/v1",
        "reviewed_on": "2026-10-11",
        "products": [
            {
                "id": venue + ":" + product,
                "venue": venue,
                "product": product,
                "name": name,
                "sector": sector,
                "currency": currency,
                "reference": SOURCES[venue],
                "research_enabled": True,
                "history_status": "not_assessed",
                "live_enabled": False,
            }
            for venue, product, name, sector, currency in PRODUCTS
        ],
        "limits": "Discovery universe only. Internal product IDs are not vendor symbols. Supply explicit raw contract mappings, historical rules, session calendars and licensed observations; audit coverage separately. CFFEX research eligibility does not alter legacy trade/tradeF switches.",
    }
