#!/bin/bash
# 在 Linux 服务器上把 nsq.py 打包成单文件二进制 nsq。
# 必须在与目标服务器相同的 Linux 架构上打包（curl_cffi 含编译扩展）。
# 用独立 venv 打包，不污染系统 Python，也避免 PEP 668 拦截。
set -e
cd "$(dirname "$0")"

# 需要 venv 模块：sudo apt install python3-venv
python3 -m venv .build-venv
.build-venv/bin/pip install --upgrade pip
.build-venv/bin/pip install curl_cffi pyinstaller
.build-venv/bin/pyinstaller --onefile --name nsq nsq.py

echo
echo "完成，二进制在 dist/nsq"
echo "用法： ./dist/nsq        # 控制台菜单"
echo "      ./dist/nsq --run  # 直接签到（cron 用）"
echo "打包环境 .build-venv 可以删掉： rm -rf .build-venv build nsq.spec"
