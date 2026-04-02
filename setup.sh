#!/bin/bash
# 一键安装依赖
set -e
echo ">>> 安装 Python 依赖..."
pip install -r requirements.txt

echo ">>> 安装 Playwright 浏览器..."
playwright install chromium

echo ">>> 安装完成！"
echo ""
echo "使用步骤:"
echo "  1. 编辑 config.yaml，填写演出URL和购票人信息"
echo "  2. 首次使用先登录:  python ticket_bot.py --login"
echo "  3. 开始抢票:        python ticket_bot.py"
