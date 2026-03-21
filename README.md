# FundVu (smart-fund-tracker)

<p align="center">
  <strong>A high-performance, cross-device personal fund monitoring workspace.</strong><br>
  <strong>一个高性能、跨设备的个人基金实时监控工作站。</strong>
</p>

<p align="center">
  🚀 <strong>Live Demo / 在线体验: <a href="http://132.232.207.65:8000/">http://132.232.207.65:8000/</a></strong> 🚀
</p>

---

## Introduction / 项目简介

**FundVu** is a full-stack personal fund tracking dashboard designed to solve the pain points of delayed official fund estimations. By penetrating the underlying top 10 heavy-weight stocks and fetching real-time market data (A-shares, HK, US), FundVu's "PRO Engine" reverse-calculates real-time net values when official data is missing. It features a unique "Sync Code" mechanism for seamless cross-device roaming without traditional registration, backed by a robust 3-level caching system.

**FundVu** 是一个旨在解决官方基金估值延迟/缺失痛点的全栈追踪看板。当官方估值缺失时，它的 "PRO 引擎" 能够穿透并提取基金的前十大重仓股，结合实时抓取的 A股/港股/美股行情，反向加权推算实时净值。系统采用创新的“同步码”机制实现多设备无缝漫游（无需繁琐注册），并在后端构建了强悍的三级缓存防线以应对高并发。

## Key Features / 核心特性

- **No-Reg Sync:** Use a unique 4-character + 1-color "Sync Code" to roam across devices. Includes color-wheel password protection and security questions.
- **PRO Smart Estimation:** Reverse-calculates fund net values in real-time based on underlying stock market data when official estimations drop out.
- **Multi-dimensional Visualization:** Built-in ECharts for historical trends (line charts, boxplots) and multi-color indicator dots for 5/20/60-day cumulative performance.
- **3-Level Cache Defense:** Global memory lock for real-time data + LRU cache for active funds + SQLite persistent KV cache for offline fallbacks.
- **Modern UI & Dark Mode:** Tailwind CSS powered styling with smooth View Transitions API animations and draggable sorting.

## Tech Stack / 技术栈

**Frontend (前端):** - HTML5 + Vanilla JS
- Tailwind CSS
- ECharts (Data Visualization)
- Sortable.js (Drag & Drop)

**Backend (后端):**
- FastAPI (High-performance Python web framework)
- Akshare (Financial data fetching)
- SQLite (Data persistence & KV caching)
- Multi-threading Daemon (Background market updates & cache pre-warming)

## Quick Start / 快速开始

### Prerequisites / 前置要求
- [Anaconda](https://www.anaconda.com/) or [Miniconda](https://docs.conda.io/en/latest/miniconda.html) installed.
- 您的电脑上已安装 Anaconda 或 Miniconda。

### Installation & Running / 安装与运行

**0. Extract node_modules.zip / 解压 node_modules.zip**

If the project includes a node_modules.zip file (to avoid uploading thousands of small files), extract it first:

如果项目包含 node_modules.zip 文件（用于避免上传大量小文件），请先将其解压：

unzip node_modules.zip

This will create a node_modules/ folder in your project directory.

这将在项目目录中生成一个 node_modules/ 文件夹。

**1. Create the environment / 创建运行环境**

Navigate to the extracted project folder in your terminal and create the environment using the provided `environment.yml` file:

在终端中进入该项目文件夹，并使用提供的 `environment.yml` 文件一键创建运行环境：

conda env create -f environment.yml

**2. Activate the environment / 激活环境**

conda activate fundvu
(Note: If your environment name in environment.yml is different, replace fundvu with your actual environment name. / 注：如果您的 environment.yml 中定义的环境名称不是 fundvu，请替换为您实际定义的名字。)

**3. Run the server / 启动服务**

Start the backend service using Uvicorn:
使用 Uvicorn 启动本地服务器：
uvicorn server:app --host 0.0.0.0 --port 8000 --reload

**4. Access the application / 访问应用**

Open your browser and navigate to / 打开浏览器并访问: http://localhost:8000

## Architecture Highlight / 架构亮点

**1. PRO Smart Estimation Engine / PRO 智能穿透估算引擎**

When the official real-time estimation API fails or drops out, the system automatically triggers the fallback engine. It penetrates the fund to extract its top 10 heavy-weight stocks, matches them with asynchronous local caches of A-shares, HK, and US stock markets, and dynamically reverse-calculates the fund's real-time net value based on weight normalization.

当官方实时估值接口抽风或数据缺失时，系统会自动触发兜底引擎。通过穿透获取基金的前十大重仓股，结合后台异步更新的 A股/港股/美股 实时行情池，按权重归一化动态反向推算基金的实时净值。

**2. 3-Tier Anti-Breakdown Cache / 三级防击穿缓存架构**

To ensure high concurrency and extreme speed, the backend implements a robust 3-level caching strategy:
- **L1 Global Memory:** Caches the real-time full-market estimation table with smart locking periods (e.g., locking during lunch breaks and after-market hours) to prevent API breakdown.
- **L2 LRU Cache:** Uses `OrderedDict` to cache up to 1000 active fund details for lightning-fast repeated queries.
- **L3 Persistent KV SQLite:** Physically separates long-term data (basic info/holdings, 7-day TTL) and short-term charts (expires at 5:00 AM daily) to provide a fallback during extreme network outages.

为了应对高并发和保障极致响应，后端实现了防御级的三级缓存策略：
- **L1 全局内存缓存**：针对全市场实时估值表，内置“盘后/午休智能状态锁”，防止高并发击穿上游接口。
- **L2 LRU 内存缓存**：维护最多 1000 只活跃基金的详情字典，加速高频查询。
- **L3 SQLite 持久化 KV 库**：物理隔离长效数据（持仓信息存7天）与短效数据（图表每日凌晨5点销毁），在极端断网情况下提供物理降级兜底。

**3. Asynchronous Daemon Hub / 全天候异步守护线程**

The system decouples data fetching from user requests using background daemon threads:
- **Market Hub:** Fetches A/HK stocks every 15 minutes during trading hours, and US stocks every 60 minutes at night.
- **5:00 AM Pre-warming:** Wakes up daily at 5:00 AM (Beijing Time) to forcefully clear L2/L3 garbage caches and pre-fetch the latest data for all user-saved funds, ensuring a "zero-latency" morning experience.

系统通过后台守护线程将数据抓取与用户请求彻底解耦：
- **行情调度中心**：盘中每 15 分钟静默更新 A/港股，夜间每 60 分钟更新美股。
- **凌晨 5 点预热任务**：每日北京时间凌晨 5 点准时唤醒，强刷清理 L2/L3 过期垃圾碎片，并根据用户数据库主动拉取全量自选基金数据，确保用户晨间醒来获得“秒开”体验。
