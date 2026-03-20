from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any
import akshare as ak
import pandas as pd
import datetime
from datetime import timezone, timedelta
import os
import sqlite3
import json
import re
import copy
import time
import threading
from collections import OrderedDict
from utils.stock import fetch_all_markets  # 引入股票行情抓取

# ===================== 宏定义与超参数 =====================
MIN_STOCK_RATIO_THRESHOLD = 60.0    # 股票总持仓占比必须大于此值(%)，才启用自算估值
MIN_HEAVY_HOLDING_RATIO = 30.0      # 前十大重仓股累计权重必须大于此值(%)
MAX_ML_HISTORY_DAYS = 60            # 机器学习基建：每个基金最多保留的交易日历史记录数

# ===================== L0 级全局静态底座 (系统启动时异步加载) =====================
GLOBAL_STATIC_DICT = {}
L0_LOCK = threading.Lock()

def init_l0_cache():
    """后台静默拉取全市场基金基础信息，用于极端断网降级兜底"""
    with L0_LOCK:
        if not GLOBAL_STATIC_DICT:
            try:
                print(">>> [系统初始化] 正在拉取全量基金基础信息(L0底座)...")
                df = ak.fund_name_em()
                for _, row in df.iterrows():
                    GLOBAL_STATIC_DICT[str(row['基金代码'])] = {
                        "基金名称": str(row['基金简称']),
                        "基金类型": str(row['基金类型'])
                    }
                print(f">>> [系统初始化] L0底座加载完成，共 {len(GLOBAL_STATIC_DICT)} 只基金。")
            except Exception as e:
                print(f"!!! [系统初始化] L0底座加载失败: {e}")

# 启动后台线程，不阻塞 FastAPI 主程序启动
threading.Thread(target=init_l0_cache, daemon=True).start()

# ===================== 全局内存缓存区 (第一级缓存) =====================
GLOBAL_CACHE = {
    "estimation_df": None,
    "last_update_time": 0
}
CACHE_LOCK = threading.Lock()

# ================ 全局内存缓存区 (第二级缓存：低频静态) =====================
# 使用 OrderedDict 代替普通字典，它能记住元素的添加和访问顺序
FUND_DETAIL_CACHE = OrderedDict()  
DETAIL_LOCK = threading.Lock()
DETAIL_TTL = 86400     # 保质期：24 小时 (24 * 60 * 60 秒)
MAX_CACHE_SIZE = 1000   # 最大容量防御：内存里最多只存 1000 只基金的详情

def get_realtime_estimation_df(ttl: int):
    """获取实时估值表的智能函数：带防击穿锁的双重检查机制"""
    beijing_now = datetime.datetime.now(timezone(timedelta(hours=8)))
    hour, minute = beijing_now.hour, beijing_now.minute
    current_time = time.time()
    
    # 获取缓存上一次更新的北京时间
    last_time = GLOBAL_CACHE["last_update_time"]
    last_dt = datetime.datetime.fromtimestamp(last_time, timezone(timedelta(hours=8))) if last_time else datetime.datetime.min.replace(tzinfo=timezone(timedelta(hours=8)))
    
    # ================= 修复：状态感知的智能时段锁定 =================
    # 1. 盘后锁定 (15:05 至次日 09:00)
    is_afternoon_close = (hour == 15 and minute >= 5) or hour > 15 or hour < 9
    if is_afternoon_close:
        # 确定当前这轮"盘后"的收盘基准线 (如果是凌晨，基准线是昨天的15:00)
        close_benchmark = beijing_now.replace(hour=15, minute=0, second=0, microsecond=0)
        if hour < 9:
            close_benchmark -= datetime.timedelta(days=1)
            
        if last_dt < close_benchmark:
            # 缓存是在收盘前(比如11点)拉取的，现在虽然是盘后，但必须强行失效，去拿一次最终定稿的收盘数据！
            ttl = 0 
        else:
            # 缓存已经是收盘后的最终数据了，安心锁死三天
            ttl = 1800  

    # 2. 午休锁定 (11:30 至 13:00)
    elif (hour == 11 and minute >= 30) or (hour == 12):
        lunch_benchmark = beijing_now.replace(hour=11, minute=30, second=0, microsecond=0)
        if last_dt < lunch_benchmark:
            ttl = 0  # 缓存是11:30前拉的，强刷一次拿午盘定格数据
        else:
            ttl = 1800  # 已经拿到了午休期间的数据，锁死
            
    # ==============================================================

    cache_age = current_time - GLOBAL_CACHE["last_update_time"]
    
    # 【第一重检查】：没过期，大家直接拿走，不需要排队（最高效）
    if GLOBAL_CACHE["estimation_df"] is not None and (cache_age < ttl):
        print(f">>> [缓存命中] 数据年龄 {int(cache_age)}秒 < {ttl}秒，直接返回。")
        return GLOBAL_CACHE["estimation_df"]
    
    # 走到这里说明缓存过期了，开始“排队抢锁”
    with CACHE_LOCK:
        # 【第二重检查】
        current_time = time.time()
        cache_age = current_time - GLOBAL_CACHE["last_update_time"]
        if GLOBAL_CACHE["estimation_df"] is not None and (cache_age < ttl):
            print(f"--- [锁内命中] 前面的兄弟已经把数据更新了，直接白嫖返回。")
            return GLOBAL_CACHE["estimation_df"]

        # 如果第二重检查依然是过期的，说明我是第一个进门的，乖乖去打水
        print(f">>> [缓存未命中] 获取锁成功，发起网络拉取全量估值数据...")
        try:
            df = ak.fund_value_estimation_em(symbol="全部")
            # 【核心修复】：防止上游接口抽风返回空数据污染全局缓存
            if df is None or df.empty:
                raise Exception("东方财富接口返回了空数据")
            
            GLOBAL_CACHE["estimation_df"] = df
            GLOBAL_CACHE["last_update_time"] = time.time()
            return df
        except Exception as e:
            print(f"!!! [拉取失败] 网络错误或异常: {e}")
            if GLOBAL_CACHE["estimation_df"] is not None:
                print(">>> [降级容错] 强制使用旧的缓存数据")
                return GLOBAL_CACHE["estimation_df"]
            raise e

# ===================== SQLite 数据库初始化 =====================
DB_FILE = os.path.join(os.path.dirname(__file__), "funds.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_funds
                 (sync_code TEXT PRIMARY KEY, fund_codes TEXT)''')
    try: c.execute("ALTER TABLE user_funds ADD COLUMN password TEXT DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE user_funds ADD COLUMN security_q TEXT DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE user_funds ADD COLUMN security_a TEXT DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE user_funds ADD COLUMN settings TEXT DEFAULT '{}'")
    except: pass
    try: c.execute("ALTER TABLE user_funds ADD COLUMN last_login TEXT DEFAULT ''")
    except: pass

    # 【新增】机器学习特征与标签表
    c.execute('''CREATE TABLE IF NOT EXISTS fund_ml_data
                 (fund_code TEXT,
                  trade_date TEXT,
                  fund_actual_rate REAL DEFAULT NULL,
                  stock_ratio_reported REAL,
                  holdings_snapshot TEXT,
                  PRIMARY KEY (fund_code, trade_date))''')

    conn.commit()
    conn.close()

init_db()

# ===================== 持久化键值对缓存 (独立于用户数据库，绝对安全物理隔离) =====================
CACHE_DB_FILE = os.path.join(os.path.dirname(__file__), "cache.db")

def init_cache_db():
    conn = sqlite3.connect(CACHE_DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS kv_cache
                 (key TEXT PRIMARY KEY, value TEXT, expire_time REAL)''')
    # 每次启动时顺便清理一下已过期的缓存，防止该文件无限增大
    c.execute("DELETE FROM kv_cache WHERE expire_time > 0 AND expire_time < ?", (time.time(),))
    conn.commit()
    conn.close()

init_cache_db()

def get_cache(key: str):
    """从独立缓存库读取数据"""
    try:
        conn = sqlite3.connect(CACHE_DB_FILE)
        c = conn.cursor()
        c.execute("SELECT value, expire_time FROM kv_cache WHERE key=?", (key,))
        row = c.fetchone()
        conn.close()
        if row:
            if row[1] == 0 or time.time() < row[1]:
                return json.loads(row[0])
    except Exception: pass
    return None

def set_cache(key: str, value: Any, ttl: float = 0):
    """向独立缓存库写入数据，ttl为存活秒数"""
    try:
        expire_time = time.time() + ttl if ttl > 0 else 0
        conn = sqlite3.connect(CACHE_DB_FILE)
        c = conn.cursor()
        c.execute("REPLACE INTO kv_cache (key, value, expire_time) VALUES (?, ?, ?)", 
                  (key, json.dumps(value), expire_time))
        conn.commit()
        conn.close()
    except Exception: pass


def is_valid_sync_code(code: str) -> bool:
    if not code or len(code) != 5: return False
    pattern = r'^[A-Z]{2}\d{2}[红橙黄绿青蓝紫黑白灰粉褐]$'
    return bool(re.match(pattern, code))

# ===================== 股票内存中心与后台调度引擎 =====================
GLOBAL_STOCK_CACHE = {"A": {}, "HK": {}, "US": {}}
STOCK_LOCK = threading.Lock()

def parse_stock_time(time_val, market):
    """时间格式化工具"""
    if pd.isna(time_val) or not str(time_val).strip(): return ""
    val = str(time_val).strip()
    now = datetime.datetime.now(timezone(timedelta(hours=8)))
    try:
        if market == "A": # A股格式: 09:32:01
            parts = val.split(":")
            if len(parts) >= 2: return f"{parts[0]}点{parts[1]}分"
        elif market == "HK": # 港股格式: 2026/03/20 09:15:45
            dt = datetime.datetime.strptime(val, "%Y/%m/%d %H:%M:%S")
            time_str = f"{dt.hour:02d}点{dt.minute:02d}分"
            if dt.date() == now.date(): return time_str
            elif dt.date() == (now - datetime.timedelta(days=1)).date(): return f"昨日 {time_str}"
            else: return f"{dt.day}日 {time_str}"
    except: pass
    return ""

def stock_market_hub():
    last_ahk_update = 0
    last_us_update = 0
    last_snapshot_date = ""

    try:
        out_dir = "stock"
        if os.path.exists(out_dir):
            for market, prefix in [("A", "A股"), ("HK", "港股"), ("US", "美股")]:
                # 找出该市场所有的 xlsx 文件，按文件名（日期）排序，取最新的一个
                files = [f for f in os.listdir(out_dir) if f.endswith(f"_{prefix}.xlsx")]
                if files:
                    latest_file = sorted(files)[-1] 
                    _reload_stock_cache_to_memory(market, out_dir, latest_file)
                    print(f">>> [行情中心] 冷启动成功装载 {market} 市场本地数据: {latest_file}")
    except Exception as e:
        print(f"!!! [行情中心] 冷启动读取本地缓存失败: {e}")

    while True:
        try:
            now = datetime.datetime.now(timezone(timedelta(hours=8)))
            is_weekend = now.weekday() >= 5
            today_str = now.strftime("%Y-%m-%d")

            # 1. 周末只在周六早晨9点更新一次兜底
            if is_weekend:
                if now.weekday() == 5 and now.hour == 9 and 0 <= now.minute < 5:
                    fetch_all_markets(fetch_a=True, fetch_hk=True, fetch_us=True)
                time.sleep(300)
                continue

            # 2. A股/港股更新逻辑 (每15分钟)
            is_ahk_time = (9 <= now.hour <= 12) or (13 <= now.hour <= 16)
            if is_ahk_time and (time.time() - last_ahk_update > 15 * 60):
                res = fetch_all_markets(fetch_a=True, fetch_hk=True, fetch_us=False)
                _reload_stock_cache_to_memory("A", "stock", f"{now.strftime('%Y_%m_%d')}_A股.xlsx")
                _reload_stock_cache_to_memory("HK", "stock", f"{now.strftime('%Y_%m_%d')}_港股.xlsx")
                last_ahk_update = time.time()

            # 3. 美股更新逻辑 (晚21:00-次日05:00, 每60分钟)
            is_us_time = (now.hour >= 21) or (now.hour < 5)
            if is_us_time and (time.time() - last_us_update > 60 * 60):
                res = fetch_all_markets(fetch_a=False, fetch_hk=False, fetch_us=True)
                _reload_stock_cache_to_memory("US", "stock", f"{now.strftime('%Y_%m_%d')}_美股.xlsx")
                last_us_update = time.time()

            # 4. 机器学习基建：每日15:30存特征快照
            if now.hour == 15 and now.minute >= 30 and last_snapshot_date != today_str:
                _take_ml_snapshot(today_str)
                last_snapshot_date = today_str

        except Exception as e:
            print(f"!!! 行情引擎异常: {e}")
        time.sleep(60)

def _reload_stock_cache_to_memory(market, out_dir, filename):
    file_path = os.path.join(out_dir, filename)
    if not os.path.exists(file_path): return
    try:
        df = pd.read_excel(file_path, engine='openpyxl')
        new_dict = {}
        for _, row in df.iterrows():
            if market == "A":
                code = str(row.get('代码', ''))
                pure_code = code[2:] if code[:2].isalpha() else code # 剥离 sh/sz/bj
                rate = row.get('涨跌幅', 0.0)
                new_dict[pure_code] = {"rate": float(rate) if pd.notna(rate) else 0.0, "time": parse_stock_time(row.get('时间戳'), "A")}
            elif market == "HK":
                pure_code = str(row.get('代码', '')).lstrip('0')
                rate = row.get('涨跌幅', 0.0)
                new_dict[pure_code] = {"rate": float(rate) if pd.notna(rate) else 0.0, "time": parse_stock_time(row.get('日期时间'), "HK")}
            elif market == "US":
                pure_code = str(row.get('symbol', ''))
                rate = row.get('chg', 0.0)
                new_dict[pure_code] = {"rate": float(rate) if pd.notna(rate) else 0.0, "time": ""}
        with STOCK_LOCK:
            GLOBAL_STOCK_CACHE[market] = new_dict
    except: pass

def _take_ml_snapshot(today_str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT fund_codes FROM user_funds")
    all_codes = set()
    for row in c.fetchall():
        if row[0]:
            try: all_codes.update(json.loads(row[0]))
            except: pass
    
    for code in all_codes:
        long_term_key = f"fund_long_{code}"
        data = get_cache(long_term_key)
        if not data: continue
        
        alloc = data.get("basic_info", {}).get("asset_allocation", [])
        stock_ratio = sum(item["value"] for item in alloc if "股票" in item["name"]) if alloc else 0.0
        
        snapshot = []
        with STOCK_LOCK:
            for holding in data.get("holdings_data", []):
                if holding[4] == "股票":
                    h_code = str(holding[0])
                    rate = 0.0
                    for m in ["A", "HK", "US"]:
                        matched = next((GLOBAL_STOCK_CACHE[m][k]["rate"] for k in GLOBAL_STOCK_CACHE[m] if h_code.endswith(k) or k.endswith(h_code)), None)
                        if matched is not None:
                            rate = matched
                            break
                    snapshot.append({"code": h_code, "weight": holding[2], "rate": rate})
        
        if snapshot:
            c.execute("INSERT OR IGNORE INTO fund_ml_data (fund_code, trade_date, stock_ratio_reported, holdings_snapshot) VALUES (?, ?, ?, ?)",
                      (code, today_str, stock_ratio, json.dumps(snapshot)))
    conn.commit()
    conn.close()

threading.Thread(target=stock_market_hub, daemon=True).start()

# ===================== 核心基金查询函数 =====================
def get_fund_comprehensive_info(fund_code: str, estimation_df: pd.DataFrame = None, is_pro: bool = False):
    # 【修复1】在最外层暴露出 code，让前端能精准匹配，防止错位！
    result = {"success": False, "code": fund_code, "data": {"basic_info": {}, "estimation_info": {}, "holdings_data": [], "detailed_info": None}, "error": ""}
    
    # 【新增】处理缺失数据的辅助函数
    def safe_str(val):
        s = str(val)
        return s if s not in ('nan', 'None', '<NA>') else '--'

    try:
        current_time = time.time()
        static_data = None
        
        # ---------------- 步骤 1：获取静态数据（带有 LRU 防御的二级缓存） ----------------
        with DETAIL_LOCK:
            cached_info = FUND_DETAIL_CACHE.get(fund_code)
            if cached_info and (current_time - cached_info["timestamp"] < DETAIL_TTL):
                FUND_DETAIL_CACHE.move_to_end(fund_code)
                static_data = cached_info["data"]

        # 如果没命中缓存（过期了，或者被 LRU 踢掉了），就去全网抓取
        if static_data is None:
            print(f">>> [内存LRU未命中] 正在处理 {fund_code} 的静态数据...")
            static_data = {"basic_info": {}, "holdings_data": [], "detailed_info": None}
            
            # --- 【修复1】尝试从持久化 KV 数据库加载长期数据 (基础信息 + 持仓) ---
            long_term_key = f"fund_long_{fund_code}"
            long_term_data = get_cache(long_term_key)
            
            if long_term_data:
                print(f"--- [KV持久化缓存命中] 成功读取 {fund_code} 长效数据 (基础信息+持仓)。免去网络抓取。")
                static_data["basic_info"] = long_term_data.get("basic_info", {})
                static_data["holdings_data"] = long_term_data.get("holdings_data", [])
            else:
                # ================= 1.1 抓取基本信息 (三级降级防线) =================
                basic_info = {"基金名称": "暂无数据", "运作公司": "暂无数据", "托管银行": "暂无数据", "基金评级": "暂无数据", "最新规模": "暂无数据", "基金类型": "暂无数据"}
                
                try:
                    basic_info_df = ak.fund_individual_basic_info_xq(symbol=fund_code)
                    info_dict = dict(zip(basic_info_df['item'], basic_info_df['value']))
                    basic_info.update({
                        "基金名称": info_dict.get('基金名称', '暂无数据'),
                        "运作公司": info_dict.get('基金公司', '暂无数据'),
                        "托管银行": info_dict.get('托管银行', '暂无数据'),
                        "基金评级": info_dict.get('基金评级', '暂无数据'),
                        "最新规模": info_dict.get('最新规模', '暂无数据'),
                        "基金类型": info_dict.get('基金类型', '暂无数据') 
                    })
                except Exception:
                    try:
                        overview_df = ak.fund_overview_em(symbol=fund_code)
                        if not overview_df.empty:
                            row = overview_df.iloc[0]
                            basic_info.update({
                                "基金名称": str(row.get('基金简称', '暂无数据')),
                                "运作公司": str(row.get('基金管理人', '暂无数据')),
                                "托管银行": str(row.get('基金托管人', '暂无数据')),
                                "最新规模": str(row.get('资产规模', '暂无数据')),
                                "基金类型": str(row.get('基金类型', '暂无数据'))
                            })
                    except Exception:
                        if fund_code in GLOBAL_STATIC_DICT:
                            basic_info.update({
                                "基金名称": GLOBAL_STATIC_DICT[fund_code].get("基金名称", "暂无数据"),
                                "基金类型": GLOBAL_STATIC_DICT[fund_code].get("基金类型", "暂无数据")
                            })

                allocation_data = []
                def get_last_n_quarters(n=4):
                    dates = []
                    now = datetime.datetime.now()
                    y, m = now.year, now.month
                    if m >= 10: q, y_q = 3, y
                    elif m >= 7: q, y_q = 2, y
                    elif m >= 4: q, y_q = 1, y
                    else: q, y_q = 4, y - 1
                    q_ends = {1: '0331', 2: '0630', 3: '0930', 4: '1231'}
                    for _ in range(n):
                        dates.append(f"{y_q}{q_ends[q]}")
                        q -= 1
                        if q == 0:
                            q, y_q = 4, y_q - 1
                    return dates

                for q_date in get_last_n_quarters(4):
                    try:
                        alloc_df = ak.fund_individual_detail_hold_xq(symbol=fund_code, date=q_date)
                        if alloc_df is not None and not alloc_df.empty and '资产类型' in alloc_df.columns:
                            for _, row in alloc_df.iterrows():
                                try: allocation_data.append({"name": str(row['资产类型']), "value": float(row['仓位占比'])})
                                except: pass
                            break
                    except Exception: continue
                
                basic_info["asset_allocation"] = allocation_data
                static_data["basic_info"] = basic_info
                
                # ================= 1.2 抓取持仓信息 =================
                current_year = str(datetime.datetime.now().year)
                last_year = str(datetime.datetime.now().year - 1)
                combined_holdings = []

                holdings_df = None
                try: holdings_df = ak.fund_portfolio_hold_em(symbol=fund_code, date=current_year)
                except Exception: pass
                
                if holdings_df is None or holdings_df.empty:
                    try: holdings_df = ak.fund_portfolio_hold_em(symbol=fund_code, date=last_year)
                    except Exception: pass

                if holdings_df is not None and not holdings_df.empty and '季度' in holdings_df.columns:
                    available_quarters = sorted(holdings_df['季度'].unique(), reverse=True)
                    if available_quarters: holdings_df = holdings_df[holdings_df['季度'] == available_quarters[0]]
                    if '占净值比例' in holdings_df.columns:
                        for _, row in holdings_df.iterrows():
                            try: ratio = float(row.get('占净值比例', 0.0))
                            except: ratio = 0.0
                            combined_holdings.append([str(row.get('股票代码', '--')), str(row.get('股票名称', '--')), ratio, str(row.get('持仓市值', '--')), "股票"])

                bond_df = None
                try: bond_df = ak.fund_portfolio_bond_hold_em(symbol=fund_code, date=current_year)
                except Exception: pass
                    
                if bond_df is None or bond_df.empty:
                    try: bond_df = ak.fund_portfolio_bond_hold_em(symbol=fund_code, date=last_year)
                    except Exception: pass

                if bond_df is not None and not bond_df.empty and '季度' in bond_df.columns:
                    available_quarters = sorted(bond_df['季度'].unique(), reverse=True)
                    if available_quarters: bond_df = bond_df[bond_df['季度'] == available_quarters[0]]
                    if '占净值比例' in bond_df.columns:
                        for _, row in bond_df.iterrows():
                            try: ratio = float(row.get('占净值比例', 0.0))
                            except: ratio = 0.0
                            combined_holdings.append([str(row.get('债券代码', '--')), str(row.get('债券名称', '--')), ratio, str(row.get('持仓市值', '--')), "债券"])

                combined_holdings.sort(key=lambda x: x[2], reverse=True)

                if not combined_holdings:
                    try:
                        ind_df = None
                        try: ind_df = ak.fund_portfolio_industry_allocation_em(symbol=fund_code, date=current_year)
                        except Exception: pass
                        
                        if ind_df is None or ind_df.empty:
                            try: ind_df = ak.fund_portfolio_industry_allocation_em(symbol=fund_code, date=last_year)
                            except Exception: pass

                        if ind_df is not None and not ind_df.empty and '截止时间' in ind_df.columns:
                            available_dates = sorted(ind_df['截止时间'].unique(), reverse=True)
                            if available_dates: ind_df = ind_df[ind_df['截止时间'] == available_dates[0]]
                            if '占净值比例' in ind_df.columns:
                                for _, row in ind_df.iterrows():
                                    try: ratio = float(row.get('占净值比例', 0.0))
                                    except: ratio = 0.0
                                    combined_holdings.append(["--", str(row.get('行业类别', '--')), ratio, str(row.get('市值', '--')), "行业"])
                                combined_holdings.sort(key=lambda x: x[2], reverse=True)
                    except Exception: pass
                
                static_data["holdings_data"] = combined_holdings[:]
                
                # --- 【新增】当真正经历网络抓取后，写入持久化 KV 数据库 (7天有效期) ---
                set_cache(long_term_key, {
                    "basic_info": static_data["basic_info"],
                    "holdings_data": static_data["holdings_data"]
                }, ttl=604800)

            # ================= 1.3 抓取详细历史走势 (修复2：删除了下方重复的抓取逻辑) =================
            short_term_key = f"fund_short_{fund_code}"
            short_term_data = get_cache(short_term_key)
            
            if short_term_data:
                print(f"--- [KV持久化缓存命中] 成功读取 {fund_code} 短效图表数据。")
                static_data["detailed_info"] = short_term_data
            else:
                try:
                    hist_df = None
                    try: hist_df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
                    except Exception: pass
                    
                    if hist_df is None or hist_df.empty:
                        try:
                            hist_df = ak.fund_etf_hist_em(symbol=fund_code, period="daily", start_date="20000101", end_date="20500101", adjust="")
                            if hist_df is not None and not hist_df.empty:
                                hist_df = hist_df.rename(columns={"日期": "净值日期", "涨跌幅": "日增长率"})
                        except Exception: pass

                    if hist_df is not None and not hist_df.empty:
                        hist_df['日增长率'] = pd.to_numeric(hist_df['日增长率'], errors='coerce').fillna(0.0)
                        hist_df['净值日期'] = hist_df['净值日期'].astype(str)
                        hist_df = hist_df.tail(150).reset_index(drop=True)
                        dates = hist_df['净值日期'].tolist()
                        rates = hist_df['日增长率'].tolist()

                        def calc_return(k):
                            if len(rates) == 0: return 0.0
                            val = 1.0
                            for r in rates[-k:]: val *= (1 + r / 100.0)
                            return round((val - 1) * 100.0, 2)

                        hist_df['净值日期_dt'] = pd.to_datetime(hist_df['净值日期'])
                        max_date = hist_df['净值日期_dt'].max()

                        def calc_trend_by_calendar(days):
                            if len(rates) == 0: return {"dates": [], "returns": []}
                            cutoff_date = max_date - datetime.timedelta(days=days)
                            sub_df = hist_df[hist_df['净值日期_dt'] >= cutoff_date]
                            sub_dates = sub_df['净值日期'].tolist()
                            sub_rates = sub_df['日增长率'].tolist()
                            cum, val = [0.0], 1.0
                            for x in sub_rates[1:]:
                                val *= (1 + x / 100.0)
                                cum.append(round((val - 1) * 100.0, 2))
                            return {"dates": sub_dates, "returns": cum}

                        hist_df['month'] = hist_df['净值日期'].str[:7]
                        months = list(dict.fromkeys(hist_df['month'].tolist()))[-6:] 
                        df_6mo = hist_df[hist_df['month'].isin(months)].copy()
                        rates_6mo = df_6mo['日增长率'].tolist()
                        cum_6mo, val_6mo = [0.0], 1.0
                        for x in rates_6mo[1:]:
                            val_6mo *= (1 + x / 100.0)
                            cum_6mo.append(round((val_6mo - 1) * 100.0, 2))
                        df_6mo['cum_return'] = cum_6mo

                        trend_6mo = []
                        for m in months:
                            m_cums = df_6mo[df_6mo['month'] == m]['cum_return'].tolist()
                            mean_val = round(sum(m_cums)/len(m_cums), 2) if m_cums else 0.0
                            trend_6mo.append({"month": str(m), "rates": m_cums, "mean": mean_val})

                        static_data["detailed_info"] = {
                            "return_5d": calc_return(5), "return_20d": calc_return(20), "return_60d": calc_return(60),
                            "trend_30d": calc_trend_by_calendar(30), 
                            "trend_90d": calc_trend_by_calendar(90), 
                            "trend_6mo": trend_6mo
                        }
                        
                        # --- 【修改为北京时间凌晨 5:00 定时销毁】 ---
                        beijing_now = datetime.datetime.now(timezone(timedelta(hours=8)))
                        expire_dt = beijing_now.replace(hour=5, minute=0, second=0, microsecond=0)
                        if beijing_now > expire_dt:
                            expire_dt += datetime.timedelta(days=1)
                        ttl_seconds = (expire_dt - beijing_now).total_seconds()
                        set_cache(short_term_key, static_data["detailed_info"], ttl=ttl_seconds)
                        
                except Exception as e:
                    print(f"历史数据处理报错: {e}")
                    pass

        # ---------------- 数据抓取完毕，准备存入缓存 ----------------
        with DETAIL_LOCK:
            # 【核心修复】必须先判断是否是“新晋”基金，只有新增且满载时才踢人
            if fund_code not in FUND_DETAIL_CACHE and len(FUND_DETAIL_CACHE) >= MAX_CACHE_SIZE:
                evicted_fund, _ = FUND_DETAIL_CACHE.popitem(last=False)
                print(f"!!! [LRU 触发清理] 内存已满，踢出冷门基金数据: {evicted_fund}")
            FUND_DETAIL_CACHE[fund_code] = {"data": static_data, "timestamp": time.time()}
            FUND_DETAIL_CACHE.move_to_end(fund_code) # 【核心修复】确保刚刚更新过的活跃基金排在防淘汰队列的最末端
    
        # ---------------- 步骤 2：装载静态数据 ----------------
        result["data"]["basic_info"] = static_data["basic_info"]
        # 【核心修复】必须深拷贝！防止后续的 holding.extend() 污染 L2 全局内存字典
        result["data"]["holdings_data"] = copy.deepcopy(static_data["holdings_data"])
        result["data"]["detailed_info"] = static_data["detailed_info"]

        # ---------------- 步骤 3：拼装动态估值（实时数据） ----------------
        if estimation_df is not None:
            target_fund = estimation_df[estimation_df['基金代码'] == fund_code]
            if not target_fund.empty:
                info = target_fund.iloc[0]
                cols = target_fund.columns
                est_val_col = next((c for c in cols if '估算数据-估算值' in c), None)
                est_rate_col = next((c for c in cols if '估算数据-估算增长率' in c), None)
                bias_col = next((c for c in cols if '估算偏差' in c), None)
                result["data"]["estimation_info"] = {
                    "实时估算净值": safe_str(info[est_val_col]) if est_val_col else '暂无',
                    "估算日增长率": safe_str(info[est_rate_col]) if est_rate_col else '暂无',
                    "估算偏差": safe_str(info[bias_col]) if bias_col else '暂无',
                    "提示": ""
                }
            else:
                result["data"]["estimation_info"] = {"实时估算净值": "--", "估算日增长率": "未找到", "估算偏差": "--", "提示": "未在当日列表中找到"}
        else:       
            result["data"]["estimation_info"] = {"实时估算净值": "--", "估算日增长率": "未找到", "估算偏差": "--", "提示": "缺失估值底表"}

        # ---------------- 步骤 4：股票行情穿透补全与兜底自算估值 ----------------
        stock_ratio = 0.0
        alloc = result["data"]["basic_info"].get("asset_allocation", [])
        if alloc:
            stock_ratio = sum(item["value"] for item in alloc if "股票" in item["name"])
            
        matched_weight_sum = 0.0
        estimated_contribution = 0.0
        
        with STOCK_LOCK:
            for holding in result["data"]["holdings_data"]:
                if holding[4] == "股票":
                    h_code = str(holding[0])
                    h_weight = float(holding[2]) if holding[2] else 0.0
                    matched_rate, matched_time = None, ""
                    
                    if is_pro: # 【核心修改】仅在 PRO 模式下，才去遍历匹配底层股票行情
                        for m in ["A", "HK", "US"]:
                            cache_key = next((k for k in GLOBAL_STOCK_CACHE[m] if h_code.endswith(k) or k.endswith(h_code)), None)
                            if cache_key:
                                matched_rate = GLOBAL_STOCK_CACHE[m][cache_key]["rate"]
                                matched_time = GLOBAL_STOCK_CACHE[m][cache_key]["time"]
                                break
                            
                    if matched_rate is not None:
                        holding.extend([round(matched_rate, 2), matched_time]) # append 到 r[5], r[6]
                        matched_weight_sum += h_weight
                        estimated_contribution += (h_weight / 100.0) * matched_rate
                    else:
                        holding.extend(["--", ""])
                else:
                    holding.extend(["--", ""])

        # 官方估值缺失时，触发自算估值引擎
        est_rate = result["data"]["estimation_info"].get("估算日增长率")
        # 【核心修改】增加 is_pro 判断。只有 PRO 模式开启，才允许触发兜底反向估算
        if is_pro and (est_rate in ["未找到", "暂无", "--"]) and (stock_ratio > MIN_STOCK_RATIO_THRESHOLD) and (matched_weight_sum > MIN_HEAVY_HOLDING_RATIO):
            # 归一化推算：(重仓股实际贡献 / 重仓股总占比) * 股票大类总占比
            fallback_rate = (estimated_contribution / (matched_weight_sum / 100.0)) * (stock_ratio / 100.0)
            result["data"]["estimation_info"].update({
                "估算日增长率": f"{fallback_rate:.2f}%",
                "提示": f"依据 {matched_weight_sum:.1f}% 重仓股反向估算"
            })

        result["success"] = True
    except Exception as e:
        result["error"] = f"系统异常: {str(e)}"
    return result

# ===================== 【新增】凌晨 5 点定时预热任务 =====================
def daily_prewarm_task():
    while True:
        # 1. 计算距离下一个北京时间凌晨 5:00:05 的秒数 (加5秒缓冲，确保昨日L3缓存彻底物理过期)
        now = datetime.datetime.now(timezone(timedelta(hours=8)))
        target = now.replace(hour=5, minute=0, second=5, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        sleep_seconds = (target - now).total_seconds()
        
        # 2. 线程休眠，直到 5 点醒来
        time.sleep(sleep_seconds)
        
        print(">>> [定时任务] 开始凌晨 5 点强制刷新预热用户基金池...")
        
        # ----------------- 凌晨机器学习标签回填 (补Y) -----------------
        try:
            conn_ml = sqlite3.connect(DB_FILE)
            c_ml = conn_ml.cursor()
            null_records = c_ml.execute("SELECT fund_code, trade_date FROM fund_ml_data WHERE fund_actual_rate IS NULL").fetchall()
            for r_code, t_date in null_records:
                try:
                    # 抓取历史净值进行精准回填
                    hist_df = ak.fund_open_fund_info_em(symbol=r_code, indicator="单位净值走势")
                    hist_df['净值日期'] = hist_df['净值日期'].astype(str)
                    target_row = hist_df[hist_df['净值日期'] == t_date]
                    if not target_row.empty:
                        actual_rate = float(target_row.iloc[0]['日增长率'])
                        c_ml.execute("UPDATE fund_ml_data SET fund_actual_rate=? WHERE fund_code=? AND trade_date=?", (actual_rate, r_code, t_date))
                except Exception: pass
            
            # 淘汰超过保留天数的陈旧数据
            distinct_codes = c_ml.execute("SELECT DISTINCT fund_code FROM fund_ml_data").fetchall()
            for (dc,) in distinct_codes:
                c_ml.execute(f"""
                    DELETE FROM fund_ml_data 
                    WHERE fund_code = ? AND rowid NOT IN (
                        SELECT rowid FROM fund_ml_data 
                        WHERE fund_code = ? 
                        ORDER BY trade_date DESC LIMIT {MAX_ML_HISTORY_DAYS}
                    )
                """, (dc, dc))
            conn_ml.commit()
            conn_ml.close()
            print(">>> [定时任务] 机器学习特征标签回填与清理完成...")
        except Exception as ml_e:
            print(f"!!! [定时任务] 机器学习基建异常: {ml_e}")

        try:
            # 【核心修复】：必须先强行清空 L2 内存缓存！
            with DETAIL_LOCK:
                FUND_DETAIL_CACHE.clear()
                print(">>> [定时任务] 已清空 L2 内存缓存...")

            # 【新增】清理 L3 持久化缓存中积累的过期垃圾数据，防止服务器长期运行导致 SQLite 膨胀
            try:
                conn_cache = sqlite3.connect(CACHE_DB_FILE)
                c_cache = conn_cache.cursor()
                c_cache.execute("DELETE FROM kv_cache WHERE expire_time > 0 AND expire_time < ?", (time.time(),))
                conn_cache.commit()
                conn_cache.close()
                print(">>> [定时任务] 已清理 L3 物理数据库中的过期垃圾碎片...")
            except Exception as db_e:
                print(f"!!! [定时任务] L3 清理失败: {db_e}")

            # 3. 从数据库提取所有用户保存的基金代码
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT fund_codes FROM user_funds")
            rows = c.fetchall()
            conn.close()
            
            # 去重处理，避免重复拉取
            all_codes = set()
            for row in rows:
                if row[0]:
                    try:
                        codes = json.loads(row[0])
                        all_codes.update(codes)
                    except: pass
            
            # 4. 遍历拉取全量数据存入缓存
            for code in all_codes:
                # 此时 L2 已空，L3 的图表缓存也刚好在 5:00:00 过期，这里会真正触发网络爬虫获取新的一天的数据
                get_fund_comprehensive_info(code)
                time.sleep(0.5) # 保护上游接口
            print(f">>> [定时任务] 强制刷新预热完成，共更新 {len(all_codes)} 只基金。")
        except Exception as e:
            print(f"!!! [定时任务] 预热失败: {e}")

# 启动独立守护线程
threading.Thread(target=daily_prewarm_task, daemon=True).start()

# ===================== FastAPI 服务与路由 =====================
app = FastAPI(title="基金查询接口", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"success": False, "error": f"服务器内部错误: {str(exc)}"})

@app.get("/", response_class=HTMLResponse)
async def serve_html():
    html_path = os.path.join(os.path.dirname(__file__), "基金查询.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f: return f.read()
    except: return "<h1>找不到 基金查询.html 文件</h1>"


# ----------------- 云端账号与密码机制接口 -----------------
class AuthCheckResponse(BaseModel):
    success: bool
    has_password: bool
    security_q: str = ""
    error: str = ""

@app.get("/api/fund/check_auth", response_model=AuthCheckResponse)
def check_auth(sync_code: str):
    if not is_valid_sync_code(sync_code): return {"success": False, "has_password": False, "error": "同步码格式错误"}
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password, security_q FROM user_funds WHERE sync_code=?", (sync_code,))
    row = c.fetchone()
    conn.close()
    if row:
        has_pwd = bool(row[0])
        return {"success": True, "has_password": has_pwd, "security_q": row[1] if row[1] else ""}
    return {"success": True, "has_password": False, "security_q": ""}

@app.get("/api/fund/sync_pool")
async def get_sync_pool(sync_code: str, password: str = ""):
    if not is_valid_sync_code(sync_code): return {"success": False, "error": "同步码格式错误"}
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT fund_codes, password, settings FROM user_funds WHERE sync_code=?", (sync_code,))
    row = c.fetchone()
    
    if row:
        if row[1] and row[1] != password: 
            conn.close()
            return {"success": False, "error": "密码错误"}
        
        beijing_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE user_funds SET last_login=? WHERE sync_code=?", (beijing_now, sync_code))
        conn.commit()
        
        settings_data = json.loads(row[2]) if len(row) > 2 and row[2] else {}
        fund_data = json.loads(row[0])
        conn.close()
        return {"success": True, "data": fund_data, "settings": settings_data}
        
    conn.close()
    return {"success": True, "data": []}

class SyncPoolRequest(BaseModel):
    sync_code: str
    password: str = ""
    codes: List[str]
    settings: Dict[str, Any] = {}  # 【新增】接收前端传来的设置

@app.post("/api/fund/sync_pool")
async def save_sync_pool(request: SyncPoolRequest):
    if not is_valid_sync_code(request.sync_code): return {"success": False, "error": "同步码格式错误"}
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password FROM user_funds WHERE sync_code=?", (request.sync_code,))
    row = c.fetchone()
    if row and row[0] and row[0] != request.password:
        conn.close()
        return {"success": False, "error": "密码校验失败，无法保存"}

    # 【新增】准备当前北京时间
    beijing_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    
    if row:
        # 【修改】更新操作加入 last_login
        c.execute("UPDATE user_funds SET fund_codes=?, settings=?, last_login=? WHERE sync_code=?", 
                  (json.dumps(request.codes), json.dumps(request.settings), beijing_now, request.sync_code))
    else:
        # 【修改】插入操作加入 last_login
        c.execute("INSERT INTO user_funds (sync_code, fund_codes, password, security_q, security_a, settings, last_login) VALUES (?, ?, '', '', '', ?, ?)", 
                  (request.sync_code, json.dumps(request.codes), json.dumps(request.settings), beijing_now))
    conn.commit()
    conn.close()
    return {"success": True}

class SetPasswordRequest(BaseModel):
    sync_code: str
    old_password: str = ""
    new_password: str
    security_q: str
    security_a: str

@app.post("/api/fund/set_password")
def set_password(req: SetPasswordRequest):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password FROM user_funds WHERE sync_code=?", (req.sync_code,))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO user_funds (sync_code, fund_codes, password, security_q, security_a) VALUES (?, '[]', ?, ?, ?)",
                  (req.sync_code, req.new_password, req.security_q, req.security_a))
    else:
        if row[0] and row[0] != req.old_password:
            conn.close()
            return {"success": False, "error": "原密码错误"}
        c.execute("UPDATE user_funds SET password=?, security_q=?, security_a=? WHERE sync_code=?", 
                  (req.new_password, req.security_q, req.security_a, req.sync_code))
    conn.commit()
    conn.close()
    return {"success": True}

class ResetPasswordRequest(BaseModel):
    sync_code: str
    security_a: str
    new_password: str

@app.post("/api/fund/reset_password")
def reset_password(req: ResetPasswordRequest):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT security_a FROM user_funds WHERE sync_code=?", (req.sync_code,))
    row = c.fetchone()
    if not row or row[0] != req.security_a:
        conn.close()
        return {"success": False, "error": "密保答案不正确"}
    c.execute("UPDATE user_funds SET password=? WHERE sync_code=?", (req.new_password, req.sync_code))
    conn.commit()
    conn.close()
    return {"success": True}

class DeleteAccountRequest(BaseModel):
    sync_code: str
    password: str = ""

@app.post("/api/fund/delete_account")
def delete_account(req: DeleteAccountRequest):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password FROM user_funds WHERE sync_code=?", (req.sync_code,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {"success": True}
    if row[0] and row[0] != req.password:
        conn.close()
        return {"success": False, "error": "密码错误，拒绝注销"}
    c.execute("DELETE FROM user_funds WHERE sync_code=?", (req.sync_code,))
    conn.commit()
    conn.close()
    return {"success": True}

# ----------------- 数据查询类接口 -----------------
@app.get("/api/fund/query")
# 【核心修复】：接收 str 类型，防止框架将 URL 参数 "?is_pro=false" 误判为 True
async def query_fund(code: str, is_pro: str = "false"):  
    if not code or len(code) != 6 or not code.isdigit(): return {"success": False, "error": "格式错误"}
    try:
        estimation_df = get_realtime_estimation_df(ttl=60)
    except Exception as e:
        estimation_df = None
        
    # 绝对安全的字符串到布尔值转换
    is_pro_flag = str(is_pro).lower() in ["true", "1", "yes"]
    
    return get_fund_comprehensive_info(code, estimation_df, is_pro_flag)

class BatchQueryRequest(BaseModel): 
    codes: List[str]
    ttl: int = 60
    is_pro: bool = False  # 【核心修改】接收前端的 PRO 模式状态

@app.post("/api/fund/batch_query")
async def batch_query_fund(request: BatchQueryRequest):
    try: 
        estimation_df = get_realtime_estimation_df(request.ttl)
    except Exception as e: 
        print(f"!!! [降级容错] 获取全量估值表彻底失败，启用无估值模式: {e}")
        estimation_df = None
        
    # 【核心修改】将 is_pro 传递给底层函数
    results = [get_fund_comprehensive_info(code, estimation_df, request.is_pro) for code in request.codes]
    return {"success": True, "data": results}

@app.post("/api/fund/batch_estimation_rate")
async def batch_estimation_rate(request: BatchQueryRequest):
    try: 
        estimation_df = get_realtime_estimation_df(request.ttl)
    except Exception as e: 
        return {"success": False, "error": str(e)}
    
    results = []
    
    def safe_str(val):
        s = str(val)
        return s if s not in ('nan', 'None', '<NA>') else '--'

    for code in request.codes:
        est_rate = "未找到"
        est_val = "--"
        est_bias = "--"
        tip = ""

        # 1. 优先提取官方实时估值
        if estimation_df is not None:
            target_fund = estimation_df[estimation_df['基金代码'] == code]
            if not target_fund.empty:
                info = target_fund.iloc[0]
                cols = info.index
                est_val_col = next((c for c in cols if '估算数据-估算值' in c), None)
                est_rate_col = next((c for c in cols if '估算数据-估算增长率' in c), None)
                bias_col = next((c for c in cols if '估算偏差' in c), None)
                
                est_rate = safe_str(info[est_rate_col]) if est_rate_col else "未找到"
                est_val = safe_str(info[est_val_col]) if est_val_col else "--"
                est_bias = safe_str(info[bias_col]) if bias_col else "--"

        # 2. 核心隔离逻辑：仅在请求明确携带 is_pro=True 且官方无估值时，才触发反向推算。
        # 推算过程只读内存字典，绝不回写全局变量，保证非 PRO 用户绝对无法窃取该数值。
        if request.is_pro and (est_rate in ["未找到", "暂无", "--"]):
            static_data = None
            with DETAIL_LOCK:
                cached_info = FUND_DETAIL_CACHE.get(code)
                if cached_info:
                    static_data = cached_info["data"]
            
            if static_data:
                alloc = static_data.get("basic_info", {}).get("asset_allocation", [])
                stock_ratio = sum(item["value"] for item in alloc if "股票" in item["name"]) if alloc else 0.0
                
                if stock_ratio > MIN_STOCK_RATIO_THRESHOLD:
                    matched_weight_sum = 0.0
                    estimated_contribution = 0.0
                    with STOCK_LOCK:
                        for holding in static_data.get("holdings_data", []):
                            if len(holding) >= 5 and holding[4] == "股票":
                                h_code = str(holding[0])
                                try: h_weight = float(holding[2]) if holding[2] else 0.0
                                except: h_weight = 0.0
                                
                                for m in ["A", "HK", "US"]:
                                    cache_key = next((k for k in GLOBAL_STOCK_CACHE[m] if h_code.endswith(k) or k.endswith(h_code)), None)
                                    if cache_key:
                                        matched_rate = GLOBAL_STOCK_CACHE[m][cache_key]["rate"]
                                        matched_weight_sum += h_weight
                                        estimated_contribution += (h_weight / 100.0) * matched_rate
                                        break
                                        
                    if matched_weight_sum > MIN_HEAVY_HOLDING_RATIO:
                        fallback_rate = (estimated_contribution / (matched_weight_sum / 100.0)) * (stock_ratio / 100.0)
                        est_rate = f"{fallback_rate:.2f}%"
                        tip = f"依据 {matched_weight_sum:.1f}% 重仓股反向估算"

        # 无论是否 PRO，都返回标准格式（非 PRO 时 tip 必定为空）
        results.append({
            "code": code, 
            "estimation_rate": est_rate,
            "estimation_value": est_val,
            "estimation_bias": est_bias,
            "tip": tip  # 新增独立字段，由前端决定渲染
        })
        
    return {"success": True, "data": results}

@app.get("/output.css")
async def serve_css():
    css_path = os.path.join(os.path.dirname(__file__), "output.css")
    if os.path.exists(css_path):
        return FileResponse(css_path)
    return JSONResponse(status_code=404, content={"error": "CSS file not found"})

app.mount("/assets", StaticFiles(directory="assets"), name="assets")