import{i as e,n as t,r as n,s as r,t as i}from"./jsx-runtime-C0waqQvW.js";import{i as a,n as o,r as s,t as c}from"./lib-Dp5P8zmZ.js";var l=`# Account Pool

The account pool lets QoderGate route requests through multiple Qoder accounts and recover when one account fails.

## Import Methods

### Auto Import

Reads the current local Qoder auth session from your machine and imports it into SQLite.

### Add PAT

Exchanges a Qoder Personal Access Token for a usable session and stores it in the account pool.

## Deduplication

Accounts are deduplicated by \`uid\`. Re-importing the same user updates session data instead of creating duplicates.

## Enable and Disable

Disabled accounts stay in SQLite but are skipped during routing.

## Active Account

The active account is the first account used for a request. If it fails, QoderGate rotates to another enabled account.

## Quota Fields

| Field | Meaning |
| --- | --- |
| \`quota\` | Current quota value reported by Qoder. |
| \`is_quota_exceeded\` | Whether the account is over quota. |
| \`plan\` | Account plan identifier. |
| \`user_tag\` | Display label from Qoder. |
| \`next_reset_at\` | When quota is expected to reset. |
`,u=`# 账号池

账号池让 QoderGate 可以通过多个 Qoder 账号处理请求，并在某个账号失败时自动切换到其他账号。

## 导入方式

### Auto Import

读取当前机器上的 Qoder 本地登录会话，并导入 SQLite。

### Add PAT

通过 Qoder Personal Access Token 换取可用会话，并保存到账号池。

## 自动去重

账号按 \`uid\` 去重。重复导入同一个用户时，会更新会话数据，而不是创建重复账号。

## 启用和禁用

禁用的账号仍保留在 SQLite 中，但不会参与请求路由。

## Active Account

Active 账号会作为请求的第一候选。若请求失败，QoderGate 会自动轮转到其他启用账号。

## 额度字段

| 字段 | 含义 |
| --- | --- |
| \`quota\` | Qoder 返回的当前额度值。 |
| \`is_quota_exceeded\` | 账号是否已经超出额度。 |
| \`plan\` | 账号套餐标识。 |
| \`user_tag\` | Qoder 返回的展示标签。 |
| \`next_reset_at\` | 额度预计重置时间。 |
`,d=`# API Reference

QoderGate exposes an OpenAI-compatible chat completions endpoint.

## Base URL

\`\`\`text
http://127.0.0.1:5050
\`\`\`

## Chat Completions

\`\`\`http
POST /v1/chat/completions
\`\`\`

### Request Body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| \`model\` | string | No | Defaults to \`lite\`. |
| \`messages\` | array | Yes | OpenAI-style message list. |
| \`stream\` | boolean | No | Enables SSE streaming when \`true\`. |

### Non-Streaming Example

\`\`\`bash
curl http://127.0.0.1:5050/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer qg_live_xxx" \\
  -d '{
    "model": "lite",
    "stream": false,
    "messages": [{ "role": "user", "content": "Explain QoderGate" }]
  }'
\`\`\`

### Streaming Example

\`\`\`bash
curl http://127.0.0.1:5050/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "lite",
    "stream": true,
    "messages": [{ "role": "user", "content": "Stream a short answer" }]
  }'
\`\`\`

## Error Responses

| Status | Meaning |
| --- | --- |
| \`401\` | Missing or invalid API key. |
| \`400\` | No active Qoder account available. |
| \`502\` | Upstream request failed across available accounts. |
`,f=`# API 参考

QoderGate 提供 OpenAI 兼容的 Chat Completions 接口。

## Base URL

\`\`\`text
http://127.0.0.1:5050
\`\`\`

## Chat Completions

\`\`\`http
POST /v1/chat/completions
\`\`\`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| \`model\` | string | 否 | 默认是 \`lite\`。 |
| \`messages\` | array | 是 | OpenAI 风格消息列表。 |
| \`stream\` | boolean | 否 | 为 \`true\` 时启用 SSE 流式输出。 |

### 非流式示例

\`\`\`bash
curl http://127.0.0.1:5050/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer qg_live_xxx" \\
  -d '{
    "model": "lite",
    "stream": false,
    "messages": [{ "role": "user", "content": "Explain QoderGate" }]
  }'
\`\`\`

### 流式示例

\`\`\`bash
curl http://127.0.0.1:5050/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "lite",
    "stream": true,
    "messages": [{ "role": "user", "content": "Stream a short answer" }]
  }'
\`\`\`

## 错误码

| 状态码 | 含义 |
| --- | --- |
| \`401\` | 缺少或传入了错误的 API Key。 |
| \`400\` | 当前没有可用的 Qoder 账号。 |
| \`502\` | 所有可用账号请求上游都失败。 |
`,p=`# Architecture

QoderGate bridges OpenAI-compatible clients to Qoder sessions.

## Request Flow

\`\`\`text
Client
  -> FastAPI /v1/chat/completions
  -> API key validation
  -> SQLite account router
  -> Qoder Bearer signing
  -> Qoder upstream API
  -> OpenAI-compatible response
\`\`\`

## Backend Components

| Module | Responsibility |
| --- | --- |
| \`app.py\` | FastAPI routes, UI auth, request routing. |
| \`accounts.py\` | SQLite account CRUD and active session selection. |
| \`auth.py\` | PAT exchange, local auth import, quota query. |
| \`bridge.py\` | OpenAI-compatible stream and response conversion. |
| \`signature.py\` | Bearer signing implementation. |
| \`database.py\` | SQLite schema and connection helpers. |

## Frontend Components

The WebUI is built with Vite, React, Tailwind CSS, GSAP, and Markdown rendering.

It is compiled into:

\`\`\`text
src/qoder2api/static
\`\`\`

FastAPI serves the compiled \`index.html\` and static assets directly.
`,m=`# 架构

QoderGate 把 OpenAI 兼容客户端请求桥接到 Qoder 会话。

## 请求流程

\`\`\`text
Client
  -> FastAPI /v1/chat/completions
  -> API Key 校验
  -> SQLite 账号路由器
  -> Qoder Bearer 签名
  -> Qoder 上游 API
  -> OpenAI 兼容响应
\`\`\`

## 后端模块

| 模块 | 职责 |
| --- | --- |
| \`app.py\` | FastAPI 路由、UI 鉴权、请求路由。 |
| \`accounts.py\` | SQLite 账号 CRUD 和活跃会话选择。 |
| \`auth.py\` | PAT 交换、本地 auth 导入、额度查询。 |
| \`bridge.py\` | OpenAI 兼容流式和非流式响应转换。 |
| \`signature.py\` | Bearer 签名实现。 |
| \`database.py\` | SQLite schema 和连接帮助函数。 |

## 前端模块

WebUI 使用 Vite、React、Tailwind CSS、GSAP 和 Markdown 渲染构建。

构建产物会输出到：

\`\`\`text
src/qoder2api/static
\`\`\`

FastAPI 会直接服务编译后的 \`index.html\`、\`console.html\`、\`docs.html\` 和静态资源。
`,h=`# Authentication

QoderGate has two authentication layers: one for the management console and one for external API clients.

## Management Console Token

The WebUI uses the gateway token you enter on login. Frontend requests send it as:

\`\`\`http
X-Gateway-Token: admin
\`\`\`

This protects routes such as:

- \`/ui/status\`
- \`/ui/accounts\`
- \`/ui/config\`
- \`/ui/logs\`

## External API Keys

The OpenAI-compatible API can optionally require Bearer keys.

When enabled, clients must send:

\`\`\`http
Authorization: Bearer <allowed-api-key>
\`\`\`

## Which Token Should I Use?

| Use case | Header | Scope |
| --- | --- | --- |
| WebUI management | \`X-Gateway-Token\` | \`/ui/*\` routes |
| OpenAI-compatible calls | \`Authorization\` | \`/v1/chat/completions\` |

## Recommended Setup

- Keep the management token private.
- Enable API key auth before exposing the gateway to other machines.
- Rotate API keys if they are shared in logs or scripts.
`,g=`# 鉴权机制

QoderGate 有两层鉴权：管理控制台鉴权，以及外部 API 调用鉴权。它们服务于不同场景，不应该混用。

## 管理控制台 Token

你在登录页输入的密钥会作为管理密钥使用。前端请求管理接口时会发送：

\`\`\`http
X-Gateway-Token: admin
\`\`\`

它保护这些接口：

- \`/ui/status\`
- \`/ui/accounts\`
- \`/ui/config\`
- \`/ui/logs\`

## 外部 API Key

OpenAI 兼容接口可以单独开启 Bearer Key 校验。

开启后，客户端必须传入：

\`\`\`http
Authorization: Bearer <allowed-api-key>
\`\`\`

## 两种密钥的区别

| 使用场景 | Header | 作用范围 |
| --- | --- | --- |
| 管理后台 | \`X-Gateway-Token\` | \`/ui/*\` 管理接口 |
| OpenAI 兼容调用 | \`Authorization\` | \`/v1/chat/completions\` |

## 推荐实践

- 不要把管理 Token 写入脚本或分享给外部客户端。
- 如果网关监听非本机地址，建议开启 API Key 鉴权。
- 如果 API Key 出现在日志、截图或脚本中，及时删除并重新生成。
`,_=`# Operations

Operational notes for running QoderGate locally.

## SQLite Storage

QoderGate stores runtime data in:

\`\`\`text
~/.qoder/qoder2api.db
\`\`\`

The database contains accounts, allowed API keys, and settings.

## Backup

Stop the server and copy the database file:

\`\`\`powershell
Copy-Item "$env:USERPROFILE\\.qoder\\qoder2api.db" "$env:USERPROFILE\\Desktop\\qoder2api.db.backup"
\`\`\`

## Reset the Gateway Token

The gateway token lives in the \`settings\` table under \`gateway_token\`.

If you lock yourself out, update the value directly in SQLite or remove the database to reinitialize defaults.

## Troubleshooting

### 401 Unauthorized

- WebUI route: check \`X-Gateway-Token\`.
- API route: check \`Authorization: Bearer <key>\`.

### No Active Session

Import an account or add a PAT from the Dashboard.

### Account Quota Exceeded

Disable the exhausted account or import another account and let rotation continue.

### Local Auth Import Failed

Make sure Qoder CLI has been logged in on this machine and the local auth files exist.
`,v=`# 运维

本页记录本地运行 QoderGate 时最常用的维护动作。

## SQLite 存储位置

QoderGate 的运行数据保存在：

\`\`\`text
~/.qoder/qoder2api.db
\`\`\`

数据库包含账号、允许的 API Key 和全局设置。

## 备份

停止服务后复制数据库文件：

\`\`\`powershell
Copy-Item "$env:USERPROFILE\\.qoder\\qoder2api.db" "$env:USERPROFILE\\Desktop\\qoder2api.db.backup"
\`\`\`

## 重置 Gateway Token

网关 Token 保存在 \`settings\` 表里的 \`gateway_token\` 字段。

如果忘记密钥，可以直接修改 SQLite，或者删除数据库让程序重新初始化默认配置。

## 常见问题

### 401 Unauthorized

- 管理接口：检查 \`X-Gateway-Token\`。
- API 接口：检查 \`Authorization: Bearer <key>\`。

### No Active Session

在 Dashboard 导入账号或添加 PAT。

### Account Quota Exceeded

禁用额度耗尽的账号，或者导入更多账号让自动轮转继续工作。

### Local Auth Import Failed

确认本机已经登录过 Qoder CLI，并且本地 auth 文件存在。
`,y=`# Quickstart

Start QoderGateway, manage Qoder accounts, and complete your first OpenAI-compatible API request.

## Quickstart Flow

Follow these steps:

- Start QoderGateway.
- Manage Qoder accounts and request routing.
- Complete your first API call.

## Install and Start

Clone the repository and install dependencies:

\`\`\`bash
git clone https://github.com/bzym2/QoderGateway.git
cd QoderGateway
uv sync
\`\`\`

Then start the server:

\`\`\`powershell
uv run qoder2api
\`\`\`

The WebUI is served at:

\`\`\`text
http://127.0.0.1:5050/
\`\`\`

## Login and Change the Default Password

The default administrator password is:

\`\`\`text
admin
\`\`\`

You can use \`admin\` for the first login.

Change it immediately before using the gateway seriously. Copy the environment template:

\`\`\`bash
mv .env.example .env
\`\`\`

Then set a strong administrator password in \`.env\`:

\`\`\`env
QODER_ADMIN_PASSWORD=your-strong-password
\`\`\`

This password protects all management routes under \`/ui/*\` with the \`X-Gateway-Token\` header.

## Manage Qoder Accounts

Use one of these options:

- Click **Auto Import** to import the current local Qoder auth session.
- Paste a Qoder Personal Access Token into **Add PAT**.

Imported accounts are stored in SQLite and deduplicated by \`uid\`.

## First API Call

Once an account is active, send a chat completion request:

\`\`\`bash
curl http://127.0.0.1:5050/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "lite",
    "messages": [{ "role": "user", "content": "Say hello" }],
    "stream": false
  }'
\`\`\`

If API key auth is enabled, also pass:

\`\`\`bash
-H "Authorization: Bearer <your-api-key>"
\`\`\`

## Verify Routing

Open **Service Logs**. Every request logs the account used for routing, for example:

\`\`\`text
Request routing via account: Alice (019ec5c6-4bb0-7c1c-bf93-5209e1367f2b)
\`\`\`

## Next Steps

- Read **Authentication** before exposing the gateway to another machine.
- Read **Account Pool** to understand rotation and quota behavior.
- Read **API Reference** if you want to connect an OpenAI SDK client.
`,b=`# 快速入门

本页说明如何从零启动 QoderGateway，管理 Qoder 账号，并完成第一次 API 调用。

## 快速入门

按顺序完成这三步：

- 启动 QoderGateway。
- 管理 Qoder 账号与请求路由。
- 完成第一次 OpenAI 兼容 API 调用。

## 安装并启动

先克隆仓库并安装依赖：

\`\`\`bash
git clone https://github.com/bzym2/QoderGateway.git
cd QoderGateway
uv sync
\`\`\`

然后启动服务：

\`\`\`powershell
uv run qoder2api
\`\`\`

WebUI 地址：

\`\`\`text
http://127.0.0.1:5050/
\`\`\`

## 登录与修改默认密码

默认网关登录密钥是：

\`\`\`text
admin
\`\`\`

第一次启动后可以直接用 \`admin\` 登录控制台。

强烈建议你立刻修改默认密码。复制环境变量模板：

\`\`\`bash
mv .env.example .env
\`\`\`

然后在 \`.env\` 中设置管理员密码：

\`\`\`env
QODER_ADMIN_PASSWORD=your-strong-password
\`\`\`

这个密码用于保护所有 \`/ui/*\` 管理接口，前端会自动把它作为 \`X-Gateway-Token\` 发送。

## 管理 Qoder 账号

你可以使用两种方式：

- 点击 **Auto Import**，从本机 Qoder auth 会话自动导入。
- 在 **Add PAT** 中粘贴 Qoder Personal Access Token。

导入后的账号会存入本地 SQLite，并按 \`uid\` 自动去重。

## 完成第一次 API 调用

账号就绪后，可以发送 Chat Completions 请求：

\`\`\`bash
curl http://127.0.0.1:5050/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "lite",
    "messages": [{ "role": "user", "content": "Say hello" }],
    "stream": false
  }'
\`\`\`

如果你开启了 API Key 鉴权，还需要额外传入：

\`\`\`bash
-H "Authorization: Bearer <your-api-key>"
\`\`\`

## 验证请求路由

打开 **Service Logs**，每次请求都会打印它被路由到哪个账号：

\`\`\`text
Request routing via account: Alice (019ec5c6-4bb0-7c1c-bf93-5209e1367f2b)
\`\`\`

## 下一步

- 如果要开放给其他机器使用，先阅读 **鉴权机制**。
- 如果要理解自动切换账号，阅读 **账号池**。
- 如果要接入 OpenAI SDK，阅读 **API 参考**。
`,x=r(e(),1),S=r(n(),1),C=i(),w=Object.assign({"./docs/account-pool.md":l,"./docs/account-pool.zh.md":u,"./docs/api-reference.md":d,"./docs/api-reference.zh.md":f,"./docs/architecture.md":p,"./docs/architecture.zh.md":m,"./docs/authentication.md":h,"./docs/authentication.zh.md":g,"./docs/operations.md":_,"./docs/operations.zh.md":v,"./docs/quickstart.md":y,"./docs/quickstart.zh.md":b}),T=[{id:`quickstart`,title:`Quickstart`,zhTitle:`快速开始`,group:`Getting Started`,zhGroup:`入门`,icon:`rocket_launch`},{id:`authentication`,title:`Authentication`,zhTitle:`鉴权机制`,group:`Guides`,zhGroup:`指南`,icon:`shield_lock`},{id:`api-reference`,title:`API Reference`,zhTitle:`API 参考`,group:`Reference`,zhGroup:`参考`,icon:`api`},{id:`account-pool`,title:`Account Pool`,zhTitle:`账号池`,group:`Guides`,zhGroup:`指南`,icon:`account_balance_wallet`},{id:`operations`,title:`Operations`,zhTitle:`运维`,group:`Operations`,zhGroup:`运维`,icon:`terminal`},{id:`architecture`,title:`Architecture`,zhTitle:`架构`,group:`Reference`,zhGroup:`参考`,icon:`schema`}].map(e=>({...e,source:w[`./docs/${e.id}.md`]||``,zhSource:w[`./docs/${e.id}.zh.md`]||w[`./docs/${e.id}.md`]||``}));function E(e){return typeof e==`string`||typeof e==`number`?String(e):Array.isArray(e)?e.map(E).join(``):x.isValidElement(e)?E(e.props.children):``}function D(e){return E(e).toLowerCase().replace(/`/g,``).replace(/[^\p{L}\p{N}\s-]/gu,``).trim().replace(/\s+/g,`-`)}function O(e){document.getElementById(e)?.scrollIntoView({behavior:`smooth`,block:`start`}),history.replaceState(null,``,`#${e}`)}function k(){let[e,n]=(0,x.useState)(`quickstart`),[r,i]=(0,x.useState)(``),[l,u]=(0,x.useState)(!1),[d,f]=(0,x.useState)(()=>{let e=localStorage.getItem(`qodergate_lang`);return e===`en`||e===`zh`?e:navigator.language.toLowerCase().startsWith(`zh`)?`zh`:`en`}),p=(0,x.useRef)(null),m=(0,x.useRef)(null),h=T.find(t=>t.id===e)||T[0],g=d===`zh`?h.zhSource:h.source,_=d===`zh`?h.zhTitle:h.title,v=(0,x.useMemo)(()=>Array.from(g.matchAll(/^(#{2,3})\s+(.+)$/gm)).map(e=>({id:D(e[2]),text:e[2].replace(/`/g,``),depth:e[1].length})),[g]),y=(0,x.useMemo)(()=>{let e=r.trim().toLowerCase();return e?T.filter(t=>{let n=d===`zh`?t.zhTitle:t.title,r=d===`zh`?t.zhGroup:t.group,i=d===`zh`?t.zhSource:t.source;return n.toLowerCase().includes(e)||r.toLowerCase().includes(e)||i.toLowerCase().includes(e)}):T},[r,d]).reduce((e,t)=>{let n=d===`zh`?t.zhGroup:t.group;return e[n]=e[n]||[],e[n].push(t),e},{}),b=e=>{f(e),localStorage.setItem(`qodergate_lang`,e)};(0,x.useEffect)(()=>{p.current&&(t.fromTo(p.current,{opacity:0,y:18},{opacity:1,y:0,duration:.45,ease:`power2.out`}),window.scrollTo({top:0,behavior:`smooth`}))},[e]),(0,x.useEffect)(()=>{let e=e=>{e.key===`/`&&document.activeElement?.tagName!==`INPUT`&&(e.preventDefault(),m.current?.focus())};return window.addEventListener(`keydown`,e),()=>window.removeEventListener(`keydown`,e)},[]);let S=e=>{navigator.clipboard.writeText(e.replace(/\n$/,``)),u(!0),setTimeout(()=>u(!1),1400)};return(0,C.jsxs)(`div`,{className:`docs-shell min-h-screen`,children:[(0,C.jsxs)(`header`,{className:`docs-topbar`,children:[(0,C.jsxs)(`a`,{href:`/`,className:`docs-brand`,children:[(0,C.jsx)(`span`,{className:`docs-brand-icon`,children:(0,C.jsx)(`span`,{className:`material-symbols-outlined`,style:{fontVariationSettings:`'FILL' 1`,fontSize:20},children:`gate`})}),(0,C.jsx)(`span`,{children:d===`zh`?`QoderGate 文档`:`QoderGate Docs`})]}),(0,C.jsxs)(`div`,{className:`docs-search`,children:[(0,C.jsx)(`span`,{className:`material-symbols-outlined`,children:`search`}),(0,C.jsx)(`input`,{ref:m,value:r,onChange:e=>i(e.target.value),placeholder:d===`zh`?`搜索文档...`:`Search documentation...`}),(0,C.jsx)(`kbd`,{children:`/`})]}),(0,C.jsxs)(`nav`,{className:`docs-topnav`,children:[(0,C.jsx)(`button`,{onClick:()=>b(d===`zh`?`en`:`zh`),className:`docs-lang-switch`,children:d===`zh`?`English`:`中文`}),(0,C.jsx)(`a`,{href:`/console`,children:d===`zh`?`控制台`:`Console`}),(0,C.jsx)(`a`,{href:`/`,children:d===`zh`?`首页`:`Home`})]})]}),(0,C.jsxs)(`div`,{className:`docs-layout`,children:[(0,C.jsx)(`aside`,{className:`docs-sidebar`,children:Object.entries(y).map(([t,r])=>(0,C.jsxs)(`section`,{className:`docs-nav-group`,children:[(0,C.jsx)(`div`,{className:`docs-nav-title`,children:t}),r.map(t=>(0,C.jsxs)(`button`,{onClick:()=>n(t.id),className:`docs-nav-item ${e===t.id?`active`:``}`,children:[(0,C.jsx)(`span`,{className:`material-symbols-outlined`,children:t.icon}),d===`zh`?t.zhTitle:t.title]},t.id))]},t))}),(0,C.jsxs)(`main`,{ref:p,className:`docs-content`,children:[(0,C.jsx)(`div`,{className:`docs-hero`,children:(0,C.jsxs)(`div`,{children:[(0,C.jsx)(`div`,{className:`docs-eyebrow`,children:`QoderGate Wiki`}),(0,C.jsx)(`h1`,{children:_}),(0,C.jsx)(`p`,{children:d===`zh`?`面向快速上手、API 接入、账号池管理和本地运维的完整项目 Wiki。`:`Fast, polished documentation for using and operating the QoderGate local API gateway.`})]})}),(0,C.jsx)(`article`,{className:`markdown-body`,children:(0,C.jsx)(a,{remarkPlugins:[s,o],rehypePlugins:[c],components:{h1(){return null},h2({children:e}){return(0,C.jsx)(`h2`,{id:D(e),children:e})},h3({children:e}){return(0,C.jsx)(`h3`,{id:D(e),children:e})},pre({children:e}){let t=String(e?.props?.children||``);return(0,C.jsxs)(`div`,{className:`code-card`,children:[(0,C.jsx)(`button`,{onClick:()=>S(t),children:l?`Copied`:`Copy`}),(0,C.jsx)(`pre`,{children:e})]})}},children:g})})]}),(0,C.jsx)(`aside`,{className:`docs-toc`,children:(0,C.jsxs)(`div`,{className:`docs-toc-card`,children:[(0,C.jsx)(`div`,{className:`docs-toc-title`,children:d===`zh`?`本页目录`:`On This Page`}),v.length===0?(0,C.jsx)(`p`,{children:d===`zh`?`暂无章节`:`No sections`}):v.map(e=>(0,C.jsx)(`button`,{onClick:()=>O(e.id),className:e.depth===3?`nested`:``,children:e.text},e.id))]})})]})]})}S.createRoot(document.getElementById(`root`)).render((0,C.jsx)(k,{}));