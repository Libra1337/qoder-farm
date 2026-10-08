# QoderFarm 🚜

Qoder 账号池网关 + 全自动注册机:把批量注册的 Qoder 免费账号变成 **OpenAI 兼容 API**(无限 `lite` 档),带指纹伪装、滑块自动破解、调用日志、余额面板。

> 基于 [bzym2/QoderGateway](https://github.com/bzym2/QoderGateway)(MIT)深度改造,上游原版说明见 [README.upstream.md](README.upstream.md)。逆向研究见 [docs/qoder-protocol-research-2026-10.md](docs/qoder-protocol-research-2026-10.md)。

## ✨ 能力总览

| 模块 | 说明 |
|---|---|
| **OpenAI 兼容网关** | `/v1/chat/completions`(流式 SSE/非流式)、`/v1/models`,粘性路由 + 轮转容灾 |
| **全自动注册机** | ShiroMail 邮箱 → 表单 → **阿里滑块图像破解** → OTP → device 凭据,一键入库 |
| **虚拟机器指纹** | 种子化虚拟机(hostname/OS/CPU/DMI/时区,禁大陆港澳),同号恒定跨号互异 |
| **账号池调度** | 会话粘性(50 次/2h)、每账号并发闸、429 冷却、MaxRotate 轮转 |
| **余额/调用日志** | 每账号 Credits 列 + 09:00/21:00 定时刷新;请求级日志(模型/账号/TTFB/tokens)SQLite 持久 |
| **WebUI** | Dashboard / 账号池 / **Requests 调用日志** / Playground / API Key / 自动注册机 |

## 🤖 滑块自动破解(核心亮点)

阿里 Captcha(PUZZLE 类型)全自动通过,无人工:

- DOM 提取底图 + 竖条拼图 → **掩码边缘 NCC + 掩码 RGB ZNCC 双信号融合**定位缺口
  (合成样本台 7 档 × 200~400 样本实测:单次命中 75~96%,接受集亚像素误差中位 0.0px)
- 可选严格一致性门 `SLIDER_AGREE_TOL`(默认关):开启时接受集精度 100%,代价是单次命中降到 47~81%
- ddddocr / 视觉模型 / 亮度凹陷单信号全部实测不可用(对照数据见研究文档);旧版「|NCC−凹陷|>25 改信凹陷」规则实测仅 57~67% 命中,已移除
- 失败自动刷新换图 + 小幅偏移扫描重试(≤14 次),实测单账号 1.5~4 分钟
- 纯算法依赖(numpy + Pillow),不调外部 API

## 📊 额度真相(2026-10 实测)

- 免费号 = **`lite` 档无限用**(0 credits 照样 200);`auto/ultimate/performance/efficient` 档需 credits(402)
- 300 Credits = 一次性 **14 天 Pro 试用**,绑定"真实机器上最新版客户端首次登录",虚拟机不可领;**官方明示多开试用号会被封**
- Qoder **无签到端点**(本网关用定时余额刷新替代签到槽位)
- 部署实测:3 并发注册单账号 1.5~4 分钟;网关压测 20/20@5 并发零失败

## 🚀 快速开始

```bash
git clone https://github.com/Libra1337/qoder-farm.git && cd qoder-farm
uv sync
cd frontend && npm install && npm run build && cd ..   # 构建面板(可选)

cp .env.example .env        # 管理密码/注册机邮箱配置
uv run python -c "from qoder2api.app import main; main()"
# 面板 http://127.0.0.1:5050/console  · API http://127.0.0.1:5050/v1/chat/completions
```

注册机(独立 CLI):

```bash
cd qodergate-register
uv sync && cp .env.example .env   # 配 SHIRO_API_KEY(或 YYDS)
uv run python -m qodergate_register --check
uv run python -m qodergate_register --parents 2
```

### 环境变量(节选)

| 变量 | 说明 |
|---|---|
| `QODER_ADMIN_PASSWORD` | 管理面板密码(gateway token) |
| `QODER_ACCOUNT_CONCURRENCY` | 每账号并发(默认 2) |
| `MAIL_PROVIDER` / `SHIRO_API_KEY` / `SHIRO_DOMAIN_ID` | 邮箱提供方(默认 shiro) |
| `REG_PROXY_POOL` | 注册浏览器代理池(逗号分隔,**机房 IP 注册滑块难过,强烈建议挂住宅代理**) |
| `SLIDER_MANUAL=0` | 无人值守:滑块失败快速换号不等人工 |
| `REG_WORKERS` | 每母线程并发子任务数(默认 3) |

## 🗺️ 架构

```
客户端 → Caddy(TLS) → FastAPI 网关(127.0.0.1:5050)
                          ├─ 粘性选号(LRU) → 每账号并发闸 → api2-v2.qoder.sh(纯 Bearer)
                          ├─ 失败分类:401/403 轮转 · 429 冷却 60s · quota 二次确认
                          ├─ reqlog(SQLite + 内存环)→ /ui/requests 面板
                          └─ 内置注册机(Xvfb + Chrome)→ 滑块破解 → device flow → 入库
```

生产部署参考(Debian 12 + Caddy + systemd + Xvfb):见 [docs/deploy.md](docs/deploy.md)。

## ✅ 已完成

- [x] 协议逆向:Qoder.app 0.4.3(新 client_id / deviceToken/refresh / OTP 单框 / api2-v2)
- [x] ShiroMail 邮箱集成 + 双注册机适配(独立 CLI + 网关内置)
- [x] 阿里滑块全自动破解(边缘 NCC + RGB ZNCC 融合,合成台单次 75~96%,实测可过)
- [x] 虚拟机器指纹(rec2api 移植)+ 稳定会话 ID + 日志脱敏
- [x] 粘性路由 / 并发闸 / 冷却 / 首 token 预算(Reso2api 纪律移植)
- [x] 多模态:裸 base64 魔数补前缀、assistant 图片挪 user 轮
- [x] 面板:余额列 + Requests 调用日志 + 用量统计 + /v1/models
- [x] 服务器部署(Debian 12 + Xvfb + Chrome 155 + systemd + Caddy 自动 TLS)+ 压测 20/20

## 📌 TODO

- [ ] **注册走代理池**:机房 IP 被阿里滑块零容差(几何/轨迹全部正常仍 14 连拒),接住宅代理后服务器即可全自动量产
- [ ] **300C Pro 试用发放路径(实验过半,结论偏悲观)**:2026-10-08 实测——真实 Mac 上用真实客户端(0.4.3,device flow 完整走通)登录批量注册号,套餐仍 `PLAN_TIER_FREE`、0 credits,**未发放**;剩余假设:绑定 Qoder IDE(另一产品)首次启动 / 需完整 onboarding 建项目 / 服务端按风控延迟发放;官方 FAQ 明示试用绑定"最新版客户端首次登录+非虚拟机"且"多开试用号会封",继续深挖性价比存疑
- [ ] premium 模型组(ultimate/performance/efficient)可用性:依赖账号有 credits(试用/付费),网关侧模型路由已就绪
- [ ] 滑块求解器精度(本轮已推进):融合规则由「|NCC−凹陷|>25 改信凹陷」(实测 57~67%)换成
  「边缘 NCC + RGB ZNCC 归一化相加」,合成台单次 75~96%、接受集误差中位 0.0px;离线拟合工具
  `tools/slider_fit.py` 已就绪。**待办**:在真实注册流量上积累 `slider_attempts.jsonl`(已修落盘
  路径 + 补 `ncc_x/zncc_x/agree` 字段),用拟合工具复核门阈值/权重,并据真实数据确认是否需要
  把默认关闭的严格一致性门(`SLIDER_AGREE_TOL`)打开
- [ ] Chrome 155 headless=new 与 DrissionPage 断连 bug 绕过(现用 Xvfb 替代)
- [ ] 面板增加按日用量曲线图、账号签到式保活(每日一次 token exercise)
- [ ] 调用日志 tokens 统计补全(lite 档上游不回 usage 帧,需从 raw_usage 解析)

## ⚠️ 免责声明

仅供学习研究。请遵守 Qoder 服务条款;注册机与试用规则对抗带来的账号风险自负。MIT License.
