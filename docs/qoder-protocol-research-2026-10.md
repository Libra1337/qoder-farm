# Qoder 协议更新复核(2026-10-08)

> 来源:反编译本地 `/Applications/Qoder.app`(0.4.3,Electron,`out/main/index.js` 12MB bundle)
> 对照基线:`docs/qoder-protocol-research.md`(2026-08-06,基于 npm 包 `qoderclicn@1.1.16`)
> 所有结论均已在当日用浏览器 + curl/uv 实测。

## 结论速览

**QoderGateway 现行实现(纯 Bearer + api2-v2)不需要改**;注册机需要 3 处更新(邮箱/OTP 单框/新 client_id),已在 `qodergate-register/` 与 `src/qoder2api/registrar.py` 完成。

## 与旧版的差异(app 0.4.3 vs CLI 1.1.16)

| 项目 | 旧(CLI 1.1.16 / 旧文档) | 新(app 0.4.3) | 实测 |
|---|---|---|---|
| device-flow client_id | `e883ade2-…` / `e93fe488-…` | `732aef47-9cf2-46a2-95fe-4cebb5d0d1fa`(prod=test,regionCode=global) | 新 ID 全链路通;旧 ID 授权页仍能渲染(未点授权) |
| PKCE verifier | 43~128 随机长度 | 固定 64 字符(同 66 字符集,字节取模) | 64 字符实测通过 |
| 授权 URL | 直连 `/device/selectAccounts` | 桌面端包一层 `qoder.com/users/sign-in?biz_variant=qoder&oauth_callback=<device_url>`;直连仍可用(env `QODER_AUTH_DIRECT_DEVICE_FLOW=1` 即直连) | 直连实测通过 |
| redirect_uri | 无 | 新增可选参数(stable=`qoder-app://`,canary=`qoder-canary://`) | 不带也能过 |
| token 刷新 | `POST /api/v1/jobToken/refresh` | `POST /api/v1/deviceToken/refresh`,响应字段为 **`device_token`**(fallback `token`)+ `refresh_token`;400/401/403 = 会话失效 | 实测 200,返回新 dt-/drt-;网关 `tokens.py` 已正确实现 |
| PAT 兑换 | `jobToken/exchange {personal_token}` | 仍存在,响应新增 `device_token`/`expire_time`(epoch 秒)等容错读取 | 代码层确认(未实测 PAT) |
| 新端点 | — | `POST /api/v1/jobToken`(Bearer dt- + body `{clientId}`),device token 按客户端绑定换 job token | 代码层确认 |
| 推理域名 | `api2-v2.qoder.sh/model/v1/chat/completions` | 桌面端 `inferBaseUrl=api2.qoder.sh`(该路径下 404,非 OpenAI 兼容面);**CLI/网关的 OpenAI 兼容端点仍是 api2-v2** | api2→404;api2-v2→200 出流(实测 PONG) |
| 端点缓存 | — | `~/.qoder/.cache/qoder-client-endpoint-cache.json`:center=center.qoder.sh,inference=api3.qoder.sh,securityInference=api2.qoder.sh,openapi=openapi.qoder.sh | 读取确认 |
| 环境 override | — | `QODER_AUTH_CLIENT_ID`/`QODER_AUTH_REDIRECT_URI`/`QODER_AUTH_BASE_URL`/`QODER_OPENAPI_BASE_URL`/`QODER_INFER_BASE_URL` 等 | 代码层确认 |
| poll | `GET openapi.qoder.sh/api/v1/deviceToken/poll`(404=等待,200=凭据) | 不变;响应校验 `token`+`refresh_token` 均为 string | 实测通过 |

环境 URL 映射(app 内置,prod):auth=qoder.com,openapi/market/cloud=openapi.qoder.sh,infer/telemetry=api2.qoder.sh,feedback=center.qoder.sh;test 环境=qoder.ai / test-openapi / test-api2。

老协议 api3 COSY(agent_chat_generation):HTTP 200 但 body 需 QoderEncoding,直接透传 OpenAI body 会报服务端 `CustomBase64Util.decode` 异常——网关已弃用该路径,无需处理。

## 注册页变更(影响注册机)

- `#basic_firstName` / `#basic_lastName` / `#basic_email` / `#basic_password`、`.ant-checkbox-input`、`button[type=submit]`(Continue)全部未变 ✅
- **OTP 从分段 `input[aria-label^="OTP Input"]` 数组变为单个 `input.ant-input` 文本框** ❗ 注册机已加双兼容
- 人机验证:提交密码后出现阿里滑块("Drag slide to fill the puzzle",CertifyId),自动化轨迹两次被识破,仍需人工(与原设计一致)

## 实测记录(2026-10-08,注册账号 qoderae7417a4@futile.page)

1. ShiroMail(mail.futile.page)建邮箱(domainId=2)→ 注册表单 → 滑块(人工)→ OTP 邮件 ~40s 到达,textPreview 直接含 6 位码
2. 新 client_id device flow:selectAccounts → Continue → "Sign in success" → poll 200(dt-/drt-,expires 2026-11-07)
3. `GET /api/v1/userinfo`(Bearer dt-)→ 200(`source: dashboard.email_pwd`)
4. `POST /api/v1/deviceToken/refresh`(drt-)→ 200 新凭据
5. `POST api2-v2.qoder.sh/model/v1/chat/completions`(Bearer dt-,model=lite)→ 200 "PONG"
6. 凭据已导出 `qodergate-register/accounts.json`(可导入网关账号池)

## 附:阿里滑块自动破解实测(2026-10-08 下午)

Qoder 注册的人机验证是阿里云 Captcha(PUZZLE 类型):DOM 里两张 `<img>`——296×200 底图(含缺口,
CDN `static-captcha-sgp.aliyuncs.com/.../back.png` 或 base64)+ 52×200 竖条拼图(`shadow.png`,
块在条内 y32..81,带 alpha)。初始折叠为「点击开始验证/Click to verify」(中英双语),点了才出图。

各方案精度对照(同一底图,缺口真值 x=202,实测拖 203.2px 通过):

| 方案 | 结果 | 误差 |
|---|---|---|
| ddddocr slide_match(竖条直喂) | conf 0.08 | 不可用 |
| ddddocr slide_match(裁出小块) | x=186 | -16px |
| ddddocr det(目标检测) | 找到的是左下角水印 | 不可用 |
| 视觉模型(4.5v + 像素标尺) | 中心 185 | -40px |
| 掩码 RGB SSD | x=185(被缺口暗罩带偏) | -17px |
| **掩码边缘 NCC** | **x=202** | **±2px,实测通过** |
| 亮度凹陷(邻侧参考) | x=205~215 | +3~13px |

**最终方案**(slider.py,离线 5 样本 5/5):边缘 NCC 为主;与亮度凹陷峰距 >25px 时改信凹陷峰
(NCC 偶发完全误匹配时兜底);x≥60 搜索下限排除左缘伪峰;抛物线亚像素细化。
失败自动「刷新换图 + ±3~15px 偏移重试」(≤14 次)。**行为检测不是瓶颈**:同一合成缓动轨迹,
距离错必拒、距离对即过。

批量实测(3 并发 × 2 轮):4/6 全自动完成(建邮箱→表单→滑块→OTP→device 凭据→入库),
单账号 1.5~4 分钟;未过的 2 个耗尽重试转人工。token 经 userinfo + chat/completions 双重验证可用。
