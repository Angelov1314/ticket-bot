# 🎫 演唱会抢票平台

基于 **Playwright + FastAPI** 的演唱会自动抢票 Web App，支持大麦网、猫眼演出、秀动等主流平台。实时浏览器截图、WebSocket 日志推送、定时开抢，一键部署到 Railway。

> **仅供个人购票使用**，请遵守各平台服务条款，禁止用于黄牛倒票。

---

## 功能特性

- **多平台支持** — 大麦网 / 猫眼演出 / 秀动
- **实时日志** — WebSocket 推送每条操作记录到浏览器
- **浏览器截图** — 每次轮询后自动截图，实时预览 bot 所见画面
- **定时开抢** — 设置开票时间，提前 30s 自动就位
- **模拟模式** — 只监控不下单，安全调试配置
- **登录态持久化** — 扫码一次，保存 Cookie 长期使用
- **反检测** — 屏蔽 `navigator.webdriver`，模拟真实 User-Agent

---

## 界面预览

| 区域 | 说明 |
|------|------|
| 左侧配置面板 | 平台、URL、购票人信息、票档偏好、定时设置 |
| 右侧日志终端 | 实时 WebSocket 日志流，颜色分级显示 |
| 浏览器截图 Tab | 展示 bot 正在操作的实际页面截图 |
| 顶部状态栏 | 空闲 / 监控中 / 发现有票 / 抢票成功 |

---

## 快速开始

### 本地运行

**1. 安装依赖**

```bash
pip3 install -r requirements.txt
playwright install chromium
```

**2. 配置**

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，填写演出 URL 和购票人信息（见下方配置说明）。

**3. 首次登录（保存扫码状态）**

```bash
python3 ticket_bot.py --login
```

浏览器打开后扫码登录，完成后按 `Ctrl+C`，登录状态自动保存。

**4. 启动 Web App**

```bash
python3 server.py
```

浏览器访问 `http://localhost:8765`

---

## 配置说明

`config.yaml` 各字段说明：

```yaml
platform: damai          # 平台: damai | maoyan | showstart

ticket_url: "https://detail.damai.cn/item.htm?id=XXXXXXXX"
# ↑ 演出详情页 URL，不是首页或搜索页

simulate: true           # true=模拟模式(不下单) | false=真实抢票

buyer:
  name: "张三"           # 购票人真实姓名（实名制演出必填）
  phone: "138xxxxxxxx"   # 手机号
  id_card: ""            # 身份证号（部分演出必填）

ticket:
  quantity: 1            # 购票数量，最多 6 张
  seat_type: "480"       # 票档关键词，留空自动选第一档

timing:
  start_time: "2026-05-01 10:00:00"  # 开票时间，留空立即开始监控
  poll_interval: 0.8     # 轮询间隔(秒)，建议 0.5~1.5
  pre_start_seconds: 30  # 开票前多少秒打开页面就位

browser:
  headless: false        # false=显示窗口 | true=后台运行(Railway必须true)
  user_data_dir: "~/.ticket-bot/browser-profile"  # 登录态保存路径

safety:
  max_retries: 50        # 最大检测次数
  request_jitter: 0.1    # 随机延迟抖动(秒)，降低风控概率
```

---

## 注意事项

### 登录相关

- **首次使用必须先登录**，否则进入订单页会跳转到登录界面导致失败
- 登录状态保存在 `browser-profile` 目录，长期有效，无需每次重新登录
- 大麦网登录需要**中国大陆手机号**验证码，海外号码无法使用
- Cookie 过期或账号被风控时需重新执行 `--login`

### 演出 URL

- 必须使用**演出详情页** URL（`detail.damai.cn/item.htm?id=...`），不是搜索结果页
- 获取方式：在大麦网搜索演出 → 点击进入演出详情 → 复制地址栏 URL
- 同一演出有多个城市/场次时，选择你要抢的**具体场次页面**

### 抢票时机

- 开票前建议提前 **5~10 分钟**启动脚本，让浏览器加载好页面
- `start_time` 填写**开票时间**，脚本会自动在 `pre_start_seconds`（默认30秒）前打开页面
- 热门演出（周杰伦、五月天等）开票瞬间压力极大，`poll_interval` 建议设 **0.5**

### 风控

- 不要把 `poll_interval` 设得过低（< 0.3s），容易触发大麦反爬导致封号
- 每个账号每场演出限购张数有限制，不要修改 `quantity` 超过平台上限
- 若出现滑块验证码，脚本会暂停，需手动在浏览器窗口完成验证（`headless: false`）

### 付款

- 脚本**只负责下单**，不处理付款；订单提交成功后会发出提示音并保持浏览器打开
- 大麦网订单有效期通常 **15 分钟**，务必及时付款
- 建议提前在大麦 App 绑定好支付方式

---

## Railway 云端部署

适合想让脚本在云端 24h 运行的用户。

**1. 推送代码到 GitHub**

```bash
git init && git add . && git commit -m "init"
gh repo create ticket-bot --public --source=. --push
```

**2. Railway 一键部署**

前往 [railway.app/new](https://railway.app/new) → **Deploy from GitHub** → 选择 `ticket-bot` 仓库 → Deploy

**3. 生成公网域名**

Railway 项目页 → **Settings → Networking → Generate Domain**

**4. 注意**

- 云端部署时 `browser.headless` **必须为 `true`**（已在 `config.example.yaml` 中设置）
- 云端运行时登录态需要通过 Cookie 注入（见下方）

### 云端登录态注入

由于 Railway 没有图形界面，无法扫码。推荐方案：

1. 本地扫码登录后，在 `browser-profile` 目录找到 Cookie 文件
2. 在 Railway 项目 **Variables** 中添加：
   ```
   DAMAI_COOKIE=your_cookie_string
   ```
3. 或通过 Railway Volume 挂载本地的 `browser-profile` 目录

---

## 项目结构

```
ticket-bot/
├── server.py              # FastAPI 后端 + WebSocket 服务
├── bot_core.py            # Bot 核心逻辑（平台适配器）
├── ticket_bot.py          # 命令行入口（独立运行）
├── static/
│   └── index.html         # 前端 SPA（深色主题 Dashboard）
├── config.yaml            # 本地配置（git ignored）
├── config.example.yaml    # 配置模板（可提交）
├── Dockerfile             # Railway/Docker 部署
├── railway.json           # Railway 配置
└── requirements.txt       # Python 依赖
```

---

## 支持平台

| 平台 | 标识符 | 官网 |
|------|--------|------|
| 大麦网 | `damai` | damai.cn |
| 猫眼演出 | `maoyan` | piao.maoyan.com |
| 秀动 | `showstart` | showstart.com |

---

## 依赖

- Python 3.9+
- Playwright (Chromium)
- FastAPI + Uvicorn
- PyYAML
