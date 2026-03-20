# smart-fund-tracker
English: A high-performance personal fund tracker focused on providing real-time net value estimations. It features deep stock penetration, smart reverse-calculation, and robust multi-level caching for a seamless experience. 中文: 一款专注于提供基金实时净值估算的高性能追踪看板。支持底层重仓股穿透、智能净值反向推算，并内置防御级三级缓存架构。

# FundVu 📈

<p align="center">
  <strong>A high-performance, cross-device personal fund monitoring workspace.</strong><br>
  <strong>一个高性能、跨设备的个人基金实时监控工作站。</strong>
</p>

---

## 📖 Introduction / 项目简介

**FundVu** is a full-stack personal fund tracking dashboard designed to solve the pain points of delayed official fund estimations. By penetrating the underlying top 10 heavy-weight stocks and fetching real-time market data (A-shares, HK, US), FundVu's "PRO Engine" reverse-calculates real-time net values when official data is missing. It features a unique "Sync Code" mechanism for seamless cross-device roaming without traditional registration, backed by a robust 3-level caching system.

**FundVu** 是一个旨在解决官方基金估值延迟/缺失痛点的全栈追踪看板。当官方估值缺失时，它的 "PRO 引擎" 能够穿透并提取基金的前十大重仓股，结合实时抓取的 A股/港股/美股行情，反向加权推算实时净值。系统采用创新的“同步码”机制实现多设备无缝漫游（无需繁琐注册），并在后端构建了强悍的三级缓存防线以应对高并发。

## ✨ Key Features / 核心特性

- 🛡️ **No-Reg Sync (无痕同步漫游):** Use a unique 4-character + 1-color "Sync Code" to roam across devices. Includes color-wheel password protection and security questions.
- 🧠 **PRO Smart Estimation (PRO 智能穿透估算):** Reverse-calculates fund net values in real-time based on underlying stock market data when official estimations drop out.
- 📊 **Multi-dimensional Visualization (多维数据可视化):** Built-in ECharts for historical trends (line charts, boxplots) and multi-color indicator dots for 5/20/60-day cumulative performance.
- 🚀 **3-Level Cache Defense (三级缓存防线):** Global memory lock for real-time data + LRU cache for active funds + SQLite persistent KV cache for offline fallbacks.
- 🌙 **Modern UI & Dark Mode (现代 UI 与深色模式):** Tailwind CSS powered styling with smooth View Transitions API animations and draggable sorting.

## 🛠️ Tech Stack / 技术栈

**Frontend (前端):** - HTML5 + Vanilla JS
- Tailwind CSS
- ECharts (Data Visualization)
- Sortable.js (Drag & Drop)

**Backend (后端):**
- FastAPI (High-performance Python web framework)
- Akshare (Financial data fetching)
- SQLite (Data persistence & KV caching)
- Multi-threading Daemon (Background market updates & cache pre-warming)

## 🚀 Quick Start / 快速开始

### Prerequisites / 前置要求
- Python 3.8+
- pip (Python package manager)

### Installation / 安装部署

1. **Clone the repository / 克隆仓库:**
   ```bash
   git clone [https://github.com/yourusername/FundVu.git](https://github.com/yourusername/FundVu.git)
   cd FundVu
Install dependencies / 安装依赖:

Bash
pip install fastapi uvicorn pydantic akshare pandas openpyxl
Run the server / 启动服务:

Bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
Access the application / 访问应用:
Open your browser and navigate to http://localhost:8000

🏗️ Architecture Highlight / 架构亮点
The backend utilizes a Daemon Thread Pre-warming System. Every day at 5:00 AM (Beijing Time), a background worker clears expired caches and pre-fetches the latest data for all user-saved funds, ensuring a "zero-latency" experience when users wake up and check their portfolios.

后端采用守护线程预热系统。每日凌晨 5:00，后台线程会自动清理过期缓存，并根据用户数据库主动拉取最新一天的全量基金数据，确保用户晨间打开页面时获得“秒开”的极致体验。
