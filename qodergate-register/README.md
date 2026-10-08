# qodergate-register

Qoder 独立注册机：无限循环注册（母线程 × 3 子任务并发），浏览器隐藏后台、
人机验证置顶一次，划完自动轮到下一个；每个成功注册自动**导出 JSON**。

> 从 QoderGateway 的注册机模块独立提取，无网关依赖。

## 安装

```bash
cd qodergate-register
uv sync          # 或 pip install -e .
```

## 配置

默认邮箱提供方为 **ShiroMail**（自建邮局 mail.futile.page）：

```bash
# 项目根 .env 或环境变量
MAIL_PROVIDER=shiro                 # shiro（默认）| yyds
SHIRO_API_KEY=sk_live-xxx
SHIRO_BASE_URL=https://mail.futile.page
SHIRO_DOMAIN_ID=2                   # futile.page 的域名 id（站点 id 1 下唯一域名）
SHIRO_MAILBOX_HOURS=24              # 邮箱有效期
```

切回 YYDS Mail：`MAIL_PROVIDER=yyds` + `YYDS_API_KEY=AC-xxx`。

## device-flow 协议参数（2026-10 对齐 Qoder.app 0.4.3）

- client_id 默认使用新版 `732aef47-9cf2-46a2-95fe-4cebb5d0d1fa`（可用 `QODER_DEVICE_CLIENT_ID` 覆盖，
  旧值 `e883ade2-...` 保留在 `DEVICE_CLIENT_ID_FALLBACK`）
- PKCE verifier 固定 64 字符（新版行为；旧 CLI 为 43~128 随机）
- 可选 `QODER_DEVICE_REDIRECT_URI=qoder-app://`（桌面端会带，注册机直连流程默认不带）
- token 刷新端点已变为 `POST openapi.qoder.sh/api/v1/deviceToken/refresh`（body `{"refresh_token": "drt-..."}`）

## 使用

```bash
uv run python -m qodergate_register --check              # 检查配置
uv run python -m qodergate_register --parents 2          # 2 个母线程（每批 3 子任务并发）
uv run python -m qodergate_register --parents 1 --output ./out.json
# Ctrl+C 停止（当前批次完成后停止并打印统计）
```

## 导出格式（accounts.json）

```json
[
  {
    "email": "qoderxxx@td3.mom",
    "password": "...",
    "name": "...",
    "user_id": "019f...",
    "token": "dt-...",
    "refresh_token": "drt-...",
    "expires_at": "2026-09-05T...Z",
    "refresh_token_expires_at": "2027-08-01T...Z",
    "exported_at": "..."
  }
]
```

可直接导入 QoderGateway 账号池（批量添加）。

## 说明

- **滑块自动破解（默认开启，`SLIDER_AUTO=0` 关闭）**：阿里 PUZZLE 滑块通过图像匹配自动通过
  （掩码边缘 NCC + 亮度凹陷双信号融合,±2px 精度,离线 5 样本 5/5);每次失败自动刷新换图并做
  ±3~15px 偏移扫描重试(最多 14 次),仍失败才转人工置顶。实测约 1/3 尝试即过、最差 14 次,
  单账号全程 1.5~4 分钟。依赖 `numpy` + `Pillow`(已加入 pyproject)。
- ddddocr 不适用本滑块(拼图是 52×200 竖条,slide_match 置信度 0.08、裁剪后仍偏 16px;
  det 找不到缺口;视觉模型单点定位偏 40px)——详见 `../docs/qoder-protocol-research-2026-10.md`。
- 人机验证兜底（自动失败时）：窗口平时隐藏，验证时置顶弹出，划完自动隐藏（窗口置顶仅 Windows,
  macOS 上窗口保持可见）。
- 每母线程每批 3 个子任务并发；批内 2s 错峰，保证验证时间错开、连续可划。
- 遵守 Qoder 服务条款，控制使用频率。

## 批量优化参数(2026-10-08)

- `REG_WORKERS=3` — 每母线程并发子任务数
- `SLIDER_MANUAL=0` — 无人值守模式:滑块自动破解失败直接判失败换下一个,不等人工(推荐服务器/批量用)
- `REG_PROXY_POOL=http://user:pass@host:port,http://...` — 注册浏览器代理池,按任务随机轮换
  (单代理用 `REG_PROXY`;不出代理时所有账号同 IP 注册,是最主要的关联信号)
- 每账号一次性临时 profile(等效无痕,用完即毁)+ 随机窗口尺寸/UA 指纹多样化
- 滑块尝试记录自动追加 `slider_attempts.jsonl`(供后续离线调优)
- 实测吞吐:3 并发无人值守,单账号 1.5~3 分钟,单批 2/3~3/3 成功
