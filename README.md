# 每日早报

每天 08:00 启动一次，采集本机状态、当地天气和 arXiv 论文条目，把一份纯文本发到飞书群，然后进程退出。没有常驻进程。

运行时只用 Python 3.11 及以上的标准库。本机使用已安装的 Miniconda Python（`/home/lucas/miniconda3/bin/python3`）即可，不需要另建 conda 环境，也不需要第三方包。

## 早报里有什么

- 本机：`/` 与家目录剩余空间、内存、失败的 systemd 服务、`~/Downloads` 里大于 100MB 的文件（只看一层）。
- 天气与空气： [Open-Meteo](https://open-meteo.com/) 的预报和空气质量。城市第一次会做地理编码，经纬度写回本地配置，之后不再解析。
- 论文： [arXiv API](https://info.arxiv.org/help/api/index.html) 的标题、作者、日期和摘要页链接。检索式写在本机配置的 `[arxiv]` 里，最多 3 条，每条最多 5 篇。新论文先筛选，再写简要介绍；每天最多一篇精读，并读取 arXiv 的 HTML 全文。不下载 PDF。

某一块失败时，该段写成「本次未获取」及原因，其余照常发送。

## 配置

仓库里的 [`config.example.toml`](config.example.toml) 使用占位邮箱。真正的配置放在本机，不提交：

```bash
mkdir -p ~/.config/daily-brief
cp config.example.toml ~/.config/daily-brief/config.toml
```

把 `city` 和 `[arxiv].queries` 改成自己的。检索方向只看这个配置，不改代码。第一次成功解析城市后，经纬度会写进配置；换城市时删掉 `latitude` 和 `longitude` 两行。

默认投递到飞书。在群里添加自定义机器人，把 Webhook 地址写入本机文件：

```bash
umask 077
printf '%s\n' '这里换成完整的 Webhook 地址' > ~/.config/daily-brief/feishu-webhook
chmod 600 ~/.config/daily-brief/feishu-webhook
```

如果创建机器人时打开了签名校验，把签名密钥写入 `~/.config/daily-brief/feishu-sign`。没打开就不要建这个文件。

DeepSeek 使用 `deepseek-flash`，只处理没见过的新论文。筛选、简要撰写、精读和核对分成几次请求；摘要全文送入，不截断。精读只取一篇，程序下载该篇的 arXiv HTML，不把网页交给模型自己打开。介绍按论文 id 缓存，原始回复和 HTML 都不保存。API Key 放在仓库外面：

```bash
umask 077
printf '%s\n' '这里换成 DeepSeek API Key' > ~/.config/daily-brief/deepseek-key
chmod 600 ~/.config/daily-brief/deepseek-key
```

Webhook 还没填时，程序保存早报正文并退出，不会请求飞书。Key 还没填时，早报照常发送，论文段注明本次没有介绍。

仍要发到邮箱时，把 `[delivery].channel` 改成 `email`，并填写 `[email]`。QQ 邮箱的 SMTP 授权码放在 `~/.config/daily-brief/secret`。

同一天已经成功发过信时，再次运行会直接退出。发出的正文按日期长期写在 `~/.local/share/daily-brief/reports/YYYY-MM-DD.md`，发送成功后不删除更早的副本。`~/.local/state/daily-brief/state.json` 记录是否已发送、最多 400 个已见论文编号、最近 30 篇介绍，以及上一批推送过的论文。检索仍会取到最新条目，但已推送过的不再进入卡片，也不再调用 DeepSeek。当天没有新论文时，卡片注明「今日没有新论文」，并再次列出上一批。不保存论文 HTML、PDF，也不保存 DeepSeek 的原始回复。

## 手动运行

在仓库根目录：

```bash
PYTHONPATH=. /home/lucas/miniconda3/bin/python3 -m daily_brief --dry-run
PYTHONPATH=. /home/lucas/miniconda3/bin/python3 -m daily_brief
```

`--dry-run` 打印并写入本地副本，不发送，不调用 DeepSeek，也不把当天记为已发送。

## 定时

[`systemd/daily-brief.service`](systemd/daily-brief.service) 是 `Type=oneshot`：进程结束，服务就停止。内存上限 128MB，CPU 配额 20%，启动超过 180 秒会被结束。

[`systemd/daily-brief.timer`](systemd/daily-brief.timer) 在每天本地时间 08:00 启动它。`Persistent=true` 表示如果 8 点时没开机，下次登录后补跑一次。

```bash
mkdir -p ~/.config/systemd/user
cp systemd/daily-brief.service systemd/daily-brief.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now daily-brief.timer
systemctl --user list-timers daily-brief.timer
```

定时器只在这个用户已登录时生效。未登录也要跑的话，再执行 `loginctl enable-linger "$USER"`。默认不开启。

换一台机器时，把 service 里的 `ExecStart` 改成那台机器的 Python 路径，并把 `PYTHONPATH` 指到本仓库。

## 数据与许可

代码使用 MIT 许可证，见 [LICENSE](LICENSE)。

- 本机信息只从本地读取。
- Open-Meteo 提供免密钥的天气预报和空气质量接口。
- arXiv 只取条目元数据。精读的一篇会临时读取 HTML 全文，用完即丢，早报里不粘贴全文，也不保存 PDF。

公开仓库时保留示例配置中的占位地址。不要提交 `~/.config/daily-brief/` 里的 Webhook、签名密钥、DeepSeek API Key、SMTP 授权码、个人配置或本地早报副本。
