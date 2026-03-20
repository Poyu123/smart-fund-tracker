import os
import time
import pandas as pd
import akshare as ak
import yfinance as yf
from datetime import datetime
from typing import Optional
from time import sleep
from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch_and_save_a_stocks(
    output_dir: str = "stock",
    filename_template: str = "{date}_A股.xlsx",
    delay_seconds: int = 3
) -> int:
    """
    获取新浪财经沪深京 A 股实时行情数据，并保存为 Excel 文件。
    
    成功返回 1，失败或数据为空返回 0。
    不打印任何信息。
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        today_str = datetime.now().strftime("%Y_%m_%d")
        filename = filename_template.format(date=today_str)
        file_path = os.path.join(output_dir, filename)

        time.sleep(delay_seconds)

        # 尝试新版接口
        try:
            df = ak.stock_zh_a_stock()
        except AttributeError:
            # 回退到旧版接口
            df = ak.stock_zh_a_spot()

        if df.empty:
            return 0

        df.to_excel(file_path, index=False, engine='openpyxl')
        return 1

    except Exception:
        return 0

def fetch_and_save_hk_stocks(
    output_dir: str = "stock",
    filename_template: str = "{date}_港股.xlsx",
    delay_seconds: int = 3
) -> int:
    """
    获取新浪财经港股实时行情数据，并保存为 Excel 文件。
    
    成功返回 1，失败或数据为空返回 0。
    不打印任何信息。
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        today_str = datetime.now().strftime("%Y_%m_%d")
        filename = filename_template.format(date=today_str)
        file_path = os.path.join(output_dir, filename)

        time.sleep(delay_seconds)

        df = ak.stock_hk_spot()

        if df.empty:
            return 0

        df.to_excel(file_path, index=False, engine='openpyxl')
        return 1

    except Exception:
        return 0

def fetch_and_save_us_stocks(
    output_dir: str = "stock",
    filename_template: str = "{date}_美股.xlsx",
    delay_seconds: int = 3
) -> int:
    """
    获取新浪/雅虎财经美股实时行情数据，并保存为 Excel 文件。
    
    成功返回 1，失败或数据为空返回 0。
    不打印任何信息。
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        today_str = datetime.now().strftime("%Y_%m_%d")
        filename = filename_template.format(date=today_str)
        file_path = os.path.join(output_dir, filename)

        time.sleep(delay_seconds)

        df = ak.stock_us_spot()

        if df.empty:
            return 0

        df.to_excel(file_path, index=False, engine='openpyxl')
        return 1

    except Exception:
        return 0

def _run_with_retry(task_name, func, max_retries, delay_seconds):
    """单个市场的重试逻辑，供线程调用"""
    status = 0
    final_timestamp = ""

    for attempt in range(max_retries):
        try:
            res = func()
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if res == 1:
                return {
                    "market": task_name,
                    "result": {"status": 1, "timestamp": current_time}
                }
        except Exception:
            pass  # 静默忽略异常

        # 记录本次失败时间
        final_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 如果不是最后一次尝试，等待后重试
        if attempt < max_retries - 1:
            sleep(delay_seconds)

    # 所有重试失败
    return {
        "market": task_name,
        "result": {"status": 0, "timestamp": final_timestamp}
    }

def fetch_all_markets(
    output_dir: str = "stock",
    delay_seconds: int = 3,
    max_retries: int = 3,
    fetch_a: bool = True,      # 新增：是否获取 A 股
    fetch_hk: bool = True,     # 新增：是否获取 港股
    fetch_us: bool = True      # 新增：是否获取 美股
) -> dict:
    """
    并发获取指定市场的实时行情数据（A股/港股/美股），每个市场最多重试 max_retries 次。
    
    参数：
        fetch_a (bool): 是否获取 A 股，默认 True
        fetch_hk (bool): 是否获取 港股，默认 True
        fetch_us (bool): 是否获取 美股，默认 True
    
    返回格式（仅包含启用的市场）：
    {
        "a_stock": {"status": 1, "timestamp": "2026-03-20 09:40:12"},
        "hk_stock": {"status": 0, "timestamp": "2026-03-20 09:40:18"},
        ...
    }

    保存文件及其列名含义
        1. 美股
        保存文件格式为 {date}_美股.xlsx，列名及其含义如下：
        name: 股票名称（英文）
        cname: 股票名称（中文）
        category: 股票类别
        symbol: 股票代码
        price: 最新价
        diff: 涨跌额
        chg: 涨跌幅
        preclose: 昨收价
        open: 今开盘价
        high: 最高价
        low: 最低价
        amplitude: 振幅
        volume: 成交量
        mkcap: 市值
        pe: 市盈率
        market: 上市市场
        category_id: 类别 ID
        2. 港股
        保存文件格式为 {date}_港股.xlsx，列名及其含义如下：
        日期时间: 数据日期时间
        代码: 股票代码
        中文名称: 股票名称（中文）
        英文名称: 股票名称（英文）
        交易类型: 交易类型
        最新价: 最新价
        涨跌额: 涨跌额
        涨跌幅: 涨跌幅
        昨收: 昨收价
        今开: 今开盘价
        最高: 最高价
        最低: 最低价
        成交量: 成交量
        成交额: 成交额
        买一: 买一价
        卖一: 卖一价
        3. A股
        保存文件格式为 {date}_A股.xlsx，列名及其含义如下：
        代码: 股票代码
        名称: 股票名称
        最新价: 最新价
        涨跌额: 涨跌额
        涨跌幅: 涨跌幅
        买入: 买入价
        卖出: 卖出价
        昨收: 昨收价
        今开: 今开盘价
        最高: 最高价
        最低: 最低价
        成交量: 成交量
        成交额: 成交额
        时间戳: 数据时间戳

    """
    tasks = []

    if fetch_a:
        tasks.append(("a_stock", lambda: fetch_and_save_a_stocks(output_dir, "{date}_A股.xlsx", delay_seconds)))
    if fetch_hk:
        tasks.append(("hk_stock", lambda: fetch_and_save_hk_stocks(output_dir, "{date}_港股.xlsx", delay_seconds)))
    if fetch_us:
        tasks.append(("us_stock", lambda: fetch_and_save_us_stocks(output_dir, "{date}_美股.xlsx", delay_seconds)))

    # 如果没有启用任何市场，直接返回空字典
    if not tasks:
        return {}

    def _run_with_retry(task_name, func, max_retries, delay_seconds):
        status = 0
        final_timestamp = ""
        for attempt in range(max_retries):
            try:
                res = func()
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if res == 1:
                    return {"market": task_name, "result": {"status": 1, "timestamp": current_time}}
            except Exception:
                pass
            final_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if attempt < max_retries - 1:
                sleep(delay_seconds)
        return {"market": task_name, "result": {"status": 0, "timestamp": final_timestamp}}

    results = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_to_market = {
            executor.submit(_run_with_retry, name, func, max_retries, delay_seconds): name
            for name, func in tasks
        }
        for future in as_completed(future_to_market):
            market = future_to_market[future]
            try:
                outcome = future.result()
                results[market] = outcome["result"]
            except Exception:
                results[market] = {
                    "status": 0,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

    # 可选：保持固定顺序（仅对启用的市场）
    ordered_keys = ["a_stock", "hk_stock", "us_stock"]
    ordered_results = {k: results[k] for k in ordered_keys if k in results}
    return ordered_results

