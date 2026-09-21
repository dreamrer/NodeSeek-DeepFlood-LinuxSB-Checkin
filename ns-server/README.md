# NodeSeek 签到控制台工具（服务器版）

纯 API 请求 + `curl_cffi` 指纹伪装，**不需要 Chrome / xvfb**，一个脚本就能在 Linux 服务器上签到。
支持代理（用干净 IP 绕开 Cloudflare）、多账号、TG / 企业微信通知，带交互式控制台菜单。

## 为什么用它

GitHub Actions 的数据中心 IP 会被 Cloudflare 间歇性挑战，导致签到时好时坏。
放到自己的服务器上跑、并（可选）配一个干净代理，CF 基本不会拦，稳得多。

## 文件

- `nsq.py` —— 主程序
- `requirements.txt` —— 依赖（只有 curl_cffi）
- `build.sh` —— 打包成单文件二进制 `nsq`（可选）

## 快速开始（venv 方式，推荐）

现在的 Debian/Ubuntu 直接 `pip install` 会被系统拦（externally-managed-environment），
用 venv 隔离最省事：

```bash
# 1. 传到服务器
scp -r ns-server user@你的服务器:~/
cd ~/ns-server

# 2. 建虚拟环境并装依赖（若没有 venv 模块： sudo apt install python3-venv）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. 打开控制台配置（注意用 .venv 里的 python）
.venv/bin/python nsq.py
```

之后凡是跑本工具都用 `.venv/bin/python nsq.py`（菜单）或 `.venv/bin/python nsq.py --run`（签到）。

> 不想折腾 Python 环境？直接看下面「打包成二进制」——`build.sh` 内部也用 venv 打包，
> 产物是单文件 `nsq`，之后运行完全不依赖 Python 和 venv。

控制台菜单：

```
1) 账号管理（增删 cookie）      # 粘贴 session=xxx; pjwt=yyy
2) 设置代理                     # http://ip:port 或 socks5://ip:port，没有就留空
3) 切换奖励模式（固定/随机）
4) 通知配置（TG / 企业微信）
5) 立即签到（所有账号）
6) 测试签到（打印原始响应）      # 先用这个确认接口通不通
7) 显示 cron 定时命令
8) 切换伪装指纹
0) 退出
```

**第一次务必先选 6 测试**，看返回是不是 `签到成功 / 已签到`。若返回 Cloudflare 挑战或未登录，再调代理 / 换 cookie。

## 定时运行（cron）

菜单选 `7` 会打印现成命令。典型：

```bash
crontab -e
# 加一行（每天 08:00），注意用 venv 里的 python 绝对路径：
0 8 * * *  TZ=Asia/Shanghai /home/user/ns-server/.venv/bin/python /home/user/ns-server/nsq.py --run >> ~/nsq.log 2>&1
```
（菜单选 `7` 会按你的实际路径自动打印这行，直接抄。二进制版则用 `/path/to/nsq --run`。）

- `--run`：给所有账号签到后退出，全部成功退出码 0，否则 1
- `--test`：用第一个号试签并打印原始响应，排错用

## 打包成二进制（可选）

想要一个不依赖 Python 的单文件 `nsq`：

```bash
# 必须在与目标服务器相同架构的 Linux 上打包
bash build.sh
# 产物在 dist/nsq
./dist/nsq          # 菜单
./dist/nsq --run    # cron 用
```

## 配置文件

保存在 `~/.config/nsq/config.json`（权限 600，含 cookie，注意别外泄）。
可用环境变量 `NSQ_CONFIG` 指定其他路径。

结构示例：

```json
{
  "accounts": [{"name": "主号", "cookie": "session=xxx; pjwt=yyy"}],
  "proxy": "socks5://127.0.0.1:1080",
  "random_reward": false,
  "impersonate": "chrome",
  "notify": {"tg_bot_token": "", "tg_user_id": "", "wecom_webhook": ""}
}
```

## 注意

- 只做**签到**（签到是本工具的核心目标，无风险）。不含自动评论/加鸡腿——那些需要浏览器且有被举报风险，服务器 API 版故意不带。
- cookie 里至少要有 `session=`。多账号就在菜单里加多条。
- 服务器必须能访问 nodeseek.com（境外服务器一般没问题；境内需走代理）。
- 若签到接口路径日后变动，`nsq.py` 顶部 `ATTENDANCE_URL` 改一下即可。
