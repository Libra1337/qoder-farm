# 生产部署(Debian 12 实录)

目标:网关 + 面板 + 自动注册机 同机运行,域名自动 TLS。

## 1. 基础
```bash
apt-get update && apt-get install -y wget
curl -LsSf https://astral.sh/uv/install.sh | sh
wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
apt-get install -y /tmp/chrome.deb
# 浏览器自动化用 Playwright 驱动系统 Chrome(channel=chrome),无需 Xvfb:
# Playwright 自带 headless,Chrome 154+ 的 headless 下 DrissionPage 会 CDP 握手 404,故已弃用
```

## 2. 代码与依赖
```bash
mkdir -p /opt/qoder2api && tar -xzf qoder-farm.tar.gz -C /opt/qoder2api
cd /opt/qoder2api && uv sync    # pyproject 已带 Linux 平台标记
```

## 3. .env(0600)
```
QODER_HOST=127.0.0.1
QODER_PORT=5050
QODER_ADMIN_PASSWORD=<strong>
QODER_ACCOUNT_CONCURRENCY=2
MAIL_PROVIDER=shiro
SHIRO_API_KEY=sk_live-...
SHIRO_DOMAIN_ID=2
SLIDER_MANUAL=0
```

## 4. systemd(仅网关)
`qoder2api.service`:`ExecStart=/opt/qoder2api/.venv/bin/python -c 'from qoder2api.app import main; main()'`

> 注意:Chrome 以 root 跑必须 `--no-sandbox`(浏览器适配层在 Linux 下已内置);注册机默认 headless
> (`SLIDER_MANUAL=0`),无需 Xvfb/DISPLAY。本地人工过验证时设 `REG_HEADFUL=1` 走有头模式。

## 5. Caddy
```
your.domain.com {
    reverse_proxy 127.0.0.1:5050 { flush_interval -1 }
    request_body { max_size 64MB }
}
```

## 6. 导入账号
```bash
uv run python -c "
import json, glob
from qoder2api.database import init_db, get_db
init_db()
recs = json.load(open('accounts.json'))
for r in recs:
    with get_db() as conn:
        conn.execute('INSERT OR REPLACE INTO accounts (uid,name,user_type,security_oauth_token,refresh_token,machine_id,enabled,last_status,last_error) VALUES (?,?,?,?,?,?,1,?,NULL)',
            (r['user_id'], r['email'], 'registered', r['token'], r['refresh_token'], r.get('user_id','m'), 'ok'))
"
```

## 7. 压测
```bash
.venv/bin/python tools/qoder_route_probe.py --base https://your.domain.com --token <admin> --n 20 --conc 5
```
