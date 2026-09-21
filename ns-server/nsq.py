#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NodeSeek 签到控制台工具（Linux 服务器友好）

- 纯 API 请求 + curl_cffi 指纹伪装，不需要 Chrome / xvfb
- 支持代理（http / socks5），用干净 IP 绕开 Cloudflare
- 多账号、可选 TG / 企业微信通知
- 交互式控制台菜单（无参数运行），或 --run 供 cron 定时调用

用法：
    python3 nsq.py            # 打开控制台菜单
    python3 nsq.py --run      # 直接给所有账号签到（cron 用）
    python3 nsq.py --test     # 用第一个账号试签，打印原始响应便于排错
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime

# ----- 依赖检查 -----
try:
    from curl_cffi import requests as creq
except ImportError:
    print("缺少依赖 curl_cffi，请先安装：pip install curl_cffi", file=sys.stderr)
    sys.exit(1)

CONFIG_PATH = os.environ.get("NSQ_CONFIG") or os.path.expanduser("~/.config/nsq/config.json")

DEFAULT_CONFIG = {
    "accounts": [],            # [{"name": "主号", "cookie": "session=...; pjwt=..."}]
    "proxy": "",               # http://127.0.0.1:7890 或 socks5://127.0.0.1:1080
    "random_reward": False,    # True=试试手气(随机), False=固定鸡腿
    "impersonate": "chrome",   # curl_cffi 伪装目标：chrome / chrome110 / edge / safari 等
    "notify": {
        "tg_bot_token": "",
        "tg_user_id": "",
        "tg_api_host": "https://api.telegram.org",
        "tg_proxy": "",        # 留空则复用主代理；填 "none" 表示通知不走代理
        "wecom_webhook": "",
    },
}

ATTENDANCE_URL = "https://www.nodeseek.com/api/attendance?random={random}"


# ============ 配置读写 ============
def load_config():
    if not os.path.exists(CONFIG_PATH):
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"[!] 配置文件损坏（{e}），使用默认配置")
        return json.loads(json.dumps(DEFAULT_CONFIG))
    # 补齐缺失字段
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update(cfg)
    merged["notify"] = {**DEFAULT_CONFIG["notify"], **cfg.get("notify", {})}
    return merged


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)  # 配置含 cookie，仅本人可读
    except Exception:
        pass


# ============ 签到核心 ============
def sign_in(cookie, proxy, random_reward, impersonate, timeout=30):
    """
    调用 NodeSeek 签到接口。返回 (ok, message, raw)。
    ok=True 表示签到成功或今日已签到（都算达成目标）。
    """
    url = ATTENDANCE_URL.format(random="true" if random_reward else "false")
    headers = {
        "Cookie": cookie,
        "Origin": "https://www.nodeseek.com",
        "Referer": "https://www.nodeseek.com/board",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = creq.post(url, headers=headers, proxies=proxies,
                     impersonate=impersonate, timeout=timeout)
    raw = resp.text
    # 解析 JSON
    try:
        data = resp.json()
    except Exception:
        # 非 JSON：可能是 Cloudflare 挑战页或未登录跳转
        low = raw.lower()
        if "just a moment" in low or "cloudflare" in low or "challenge" in low:
            return False, "被 Cloudflare 挑战拦截（建议换干净代理）", raw[:300]
        return False, f"非预期响应（HTTP {resp.status_code}）", raw[:300]

    msg = data.get("message") or data.get("msg") or json.dumps(data, ensure_ascii=False)
    # NodeSeek: success=True 签到成功；已签到时通常 success=False + message 含“已签到/已完成”
    if data.get("success"):
        return True, msg, data
    if any(k in msg for k in ("已完成", "已签到", "重复")):
        return True, msg, data  # 今日已签到，视为成功
    return False, msg, data


# ============ 通知 ============
def send_notify(cfg, title, content):
    n = cfg.get("notify", {})
    text = f"{title}\n\n{content}"
    # Telegram
    token, chat = n.get("tg_bot_token"), n.get("tg_user_id")
    if token and chat:
        host = (n.get("tg_api_host") or "https://api.telegram.org").rstrip("/")
        tgp = n.get("tg_proxy")
        if tgp == "none":
            proxies = None
        elif tgp:
            proxies = {"http": tgp, "https": tgp}
        elif cfg.get("proxy"):
            proxies = {"http": cfg["proxy"], "https": cfg["proxy"]}
        else:
            proxies = None
        try:
            creq.post(f"{host}/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
                      proxies=proxies, impersonate="chrome", timeout=20)
            print("[通知] Telegram 已发送")
        except Exception as e:
            print(f"[通知] Telegram 失败: {e}")
    # 企业微信
    webhook = n.get("wecom_webhook")
    if webhook:
        try:
            creq.post(webhook, json={"msgtype": "text", "text": {"content": text}},
                      impersonate="chrome", timeout=20)
            print("[通知] 企业微信已发送")
        except Exception as e:
            print(f"[通知] 企业微信失败: {e}")


# ============ 批量运行 ============
def run_all(cfg, verbose_raw=False):
    accounts = cfg.get("accounts", [])
    if not accounts:
        print("[!] 未配置任何账号，先在菜单里添加 cookie")
        return 1
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    all_ok = True
    for i, acc in enumerate(accounts, 1):
        name = acc.get("name") or f"账号{i}"
        cookie = acc.get("cookie", "")
        if not cookie:
            lines.append(f"【{name}】跳过：cookie 为空")
            all_ok = False
            continue
        if i > 1:
            time.sleep(3)  # 多号之间稍作间隔
        print(f"=== [{i}/{len(accounts)}] {name} 签到中 ===")
        try:
            ok, msg, raw = sign_in(cookie, cfg.get("proxy"), cfg.get("random_reward"),
                                   cfg.get("impersonate", "chrome"))
        except Exception as e:
            ok, msg, raw = False, f"请求异常: {type(e).__name__} {e}", None
        if verbose_raw:
            print("  原始响应:", raw)
        status = "✅ 成功" if ok else "❌ 失败"
        print(f"  {status}: {msg}")
        lines.append(f"【{name}】{status}\n{msg}")
        all_ok = all_ok and ok
    # 通知
    title = "NodeSeek 签到" + ("" if all_ok else "（有异常）")
    content = f"时间: {started}\n代理: {cfg.get('proxy') or '未使用'}\n\n" + "\n\n".join(lines)
    send_notify(cfg, title, content)
    return 0 if all_ok else 1


# ============ 控制台菜单 ============
def _input(prompt, default=None):
    v = input(prompt).strip()
    return v if v else (default if default is not None else "")


def menu_accounts(cfg):
    while True:
        print("\n--- 账号管理 ---")
        accs = cfg.get("accounts", [])
        if not accs:
            print("  (空)")
        for i, a in enumerate(accs, 1):
            ck = a.get("cookie", "")
            has_session = "session=" in ck
            print(f"  {i}. {a.get('name','?')}  cookie长度{len(ck)}  {'✓有session' if has_session else '⚠无session'}")
        print("操作: [a]添加  [d]删除序号  [b]返回")
        c = _input("> ").lower()
        if c == "a":
            name = _input("账号备注名（如 主号）: ", "账号")
            print("粘贴该号的完整 cookie（一行，形如 session=xxx; pjwt=yyy），回车确认：")
            cookie = _input("cookie: ")
            if cookie:
                accs.append({"name": name, "cookie": cookie})
                cfg["accounts"] = accs
                save_config(cfg)
                print("[✓] 已添加并保存")
        elif c == "d":
            idx = _input("删除第几个: ")
            if idx.isdigit() and 1 <= int(idx) <= len(accs):
                removed = accs.pop(int(idx) - 1)
                cfg["accounts"] = accs
                save_config(cfg)
                print(f"[✓] 已删除 {removed.get('name')}")
        elif c == "b":
            return


def menu_notify(cfg):
    n = cfg["notify"]
    print("\n--- 通知配置（留空跳过该项，输入 - 清空）---")
    for key, label in [("tg_bot_token", "TG Bot Token"),
                       ("tg_user_id", "TG User ID"),
                       ("tg_api_host", "TG API Host(默认官方)"),
                       ("tg_proxy", "TG 代理(留空=复用主代理, none=不走代理)"),
                       ("wecom_webhook", "企业微信 Webhook")]:
        cur = n.get(key, "")
        v = _input(f"{label} [{cur or '空'}]: ", cur)
        n[key] = "" if v == "-" else v
    cfg["notify"] = n
    save_config(cfg)
    print("[✓] 通知配置已保存")


def print_cron_hint():
    exe = os.path.abspath(sys.argv[0])
    py = sys.executable
    print("\n--- 定时运行（cron）---")
    print("在服务器执行 crontab -e，加入一行（每天 08:00 北京时间签到）：")
    print(f"  0 8 * * *  {py} {exe} --run >> ~/.config/nsq/run.log 2>&1")
    print("如果打包成了二进制 nsq，则：")
    print("  0 8 * * *  /path/to/nsq --run >> ~/nsq.log 2>&1")
    print("（服务器时区若不是北京时间，请自行换算，或设 TZ=Asia/Shanghai）")


def console(cfg):
    while True:
        print("\n============ NodeSeek 签到控制台 ============")
        print(f"配置文件: {CONFIG_PATH}")
        print(f"账号数: {len(cfg.get('accounts', []))}   "
              f"代理: {cfg.get('proxy') or '未设置'}   "
              f"奖励: {'随机' if cfg.get('random_reward') else '固定鸡腿'}")
        print("-------------------------------------------")
        print("  1) 账号管理（增删 cookie）")
        print("  2) 设置代理")
        print("  3) 切换奖励模式（固定/随机）")
        print("  4) 通知配置（TG / 企业微信）")
        print("  5) 立即签到（所有账号）")
        print("  6) 测试签到（第一个号，打印原始响应）")
        print("  7) 显示 cron 定时命令")
        print("  8) 切换伪装指纹（当前: %s）" % cfg.get("impersonate", "chrome"))
        print("  0) 退出")
        c = _input("请选择: ")
        if c == "1":
            menu_accounts(cfg)
        elif c == "2":
            v = _input("代理地址（如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080，留空清除）: ")
            cfg["proxy"] = v
            save_config(cfg)
            print(f"[✓] 代理已设为: {v or '无'}")
        elif c == "3":
            cfg["random_reward"] = not cfg.get("random_reward")
            save_config(cfg)
            print(f"[✓] 奖励模式: {'随机(试试手气)' if cfg['random_reward'] else '固定鸡腿'}")
        elif c == "4":
            menu_notify(cfg)
        elif c == "5":
            run_all(cfg)
        elif c == "6":
            if cfg.get("accounts"):
                acc = cfg["accounts"][0]
                print(f"用 [{acc.get('name')}] 测试...")
                ok, msg, raw = sign_in(acc["cookie"], cfg.get("proxy"),
                                       cfg.get("random_reward"), cfg.get("impersonate", "chrome"))
                print("结果:", "✅" if ok else "❌", msg)
                print("原始响应:", raw)
            else:
                print("[!] 没有账号")
        elif c == "7":
            print_cron_hint()
        elif c == "8":
            v = _input("输入伪装目标（chrome/chrome110/chrome124/edge/safari，回车默认 chrome）: ", "chrome")
            cfg["impersonate"] = v
            save_config(cfg)
            print(f"[✓] 伪装指纹: {v}")
        elif c == "0":
            print("再见")
            return
        else:
            print("无效选择")


def main():
    ap = argparse.ArgumentParser(description="NodeSeek 签到控制台工具")
    ap.add_argument("--run", action="store_true", help="给所有账号签到（cron 用），不进菜单")
    ap.add_argument("--test", action="store_true", help="用第一个号试签并打印原始响应")
    args = ap.parse_args()
    cfg = load_config()
    if args.run:
        sys.exit(run_all(cfg))
    if args.test:
        sys.exit(run_all(cfg, verbose_raw=True))
    console(cfg)


if __name__ == "__main__":
    main()
