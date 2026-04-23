# r1-stock-bridge 详细部署指南

这份文档的目标不是只说明“原理”，而是尽量让后来的人可以按步骤部署自己的环境。

如果你是第一次接触 R1 这类方案，建议按下面的顺序来：

1. 先把后端在一台 Linux 机器上跑起来
2. 先在本机确认 `/health` 和模型调用都正常
3. 再去改路由器里的域名重写
4. 再让 R1 真机说话
5. 最后再加音乐、Home Assistant、ADB 辅助脚本

## 1. 整体架构

这套方案保留了 R1 原厂前端：

- 原厂唤醒
- 原厂录音
- 原厂 ASR
- 原厂播报

局域网后端负责接管：

- 原厂请求代理
- 本地指令规则
- 大模型问答
- Home Assistant
- 网易云音乐接口

链路如下：

1. R1 向原厂域名发请求
2. 路由器把关键域名解析到你的后端机器
3. `r1-stock-bridge` 收到请求
4. 后端先把请求继续转给原厂上游
5. 后端从上游结果里提取 `asr_recongize`
6. 后端依次尝试：
   - 本地规则
   - Home Assistant
   - 音乐
   - OpenAI 兼容大模型
7. 后端把最终结果包装成 R1 能播放的原厂风格 JSON
8. R1 继续用原厂播报链路把结果念出来

## 2. 你需要准备什么

### 必需条件

- 一台仍能联网、仍能正常唤醒的斐讯 R1
- 一台和 R1 在同一局域网里的 Linux 后端
- 路由器支持自定义 DNS 解析、Host 重写、Dnsmasq、AdGuard Home 或类似能力
- 一个可用的 OpenAI 兼容接口

### 可选条件

- Home Assistant：如果你想让 R1 控制智能家居
- Node.js 18+：如果你想接网易云音乐接口
- ADB：如果你想做 ADB 辅助测试

## 3. 后端机器建议

推荐一台长期在线的 Linux 盒子，例如：

- N1
- 软路由旁路机
- 小主机
- NAS 里的 Linux 环境

这套方案本身很轻，不需要本地跑大模型。

不建议在同一台轻量机器上再叠加：

- 本地 ASR
- 本地 TTS
- 本地 LLM 推理

## 4. 拉起后端

### 4.1 克隆仓库

```bash
git clone git@github.com:SaifLau/r1-stock-bridge.git
cd r1-stock-bridge
```

### 4.2 复制配置

```bash
cp config/.env.example config/.env
```

### 4.3 填写最小配置

先至少填这几个：

```dotenv
R1LAB_OPENAI_BASE_URL=https://your-openai-compatible-endpoint.example/v1
R1LAB_OPENAI_API_KEY=<YOUR_API_KEY>
R1LAB_OPENAI_MODEL=<YOUR_MODEL_NAME>

R1LAB_HOST=0.0.0.0
R1LAB_PORTS=80,18888
```

说明：

- `80` 端口用于接住被路由器改写后的原厂域名流量
- `18888` 端口用于你自己调试
- `R1LAB_R1_IP` 不是后端必填项，只在 ADB 脚本里使用

### 4.3.1 OpenAI 兼容接口适配说明

本项目不是只能接官方 OpenAI。

当前实现是面向 OpenAI-compatible backend 的，也就是：

- 官方 OpenAI 风格接口
- 你自己搭建的中转站
- 你自己维护的模型网关
- 任何 OpenAI 兼容代理服务

当前代码直接支持的接口路径：

- `GET /models`
- `POST /responses`
- `POST /chat/completions`

### 4.3.2 `responses` 和 `chat/completions` 的区别

当前项目支持两种主线路：

#### 方式 A：`responses`

推荐配置：

```dotenv
R1LAB_WIRE_API=responses
```

这时会优先请求：

```text
POST /responses
```

请求体核心结构：

```json
{
  "model": "YOUR_MODEL",
  "input": "用户文本",
  "store": false
}
```

如果开流式，还会额外带：

```json
{
  "stream": true
}
```

#### 方式 B：`chat/completions`

推荐配置：

```dotenv
R1LAB_WIRE_API=chat
```

这时会优先请求：

```text
POST /chat/completions
```

请求体核心结构：

```json
{
  "model": "YOUR_MODEL",
  "messages": [
    {
      "role": "user",
      "content": "用户文本"
    }
  ]
}
```

如果开流式，还会额外带：

```json
{
  "stream": true
}
```

### 4.3.3 流式和非流式

用这个环境变量控制：

```dotenv
R1LAB_PREFER_SSE=true
```

含义：

- `true`：优先使用 SSE 流式返回
- `false`：使用普通 JSON 返回

### 4.3.4 当前代码会从哪些返回里取文本

当前实现会自动从这些格式里提取最终答案：

#### `responses` 普通 JSON

优先读取：

- `output_text`

如果没有，再尝试：

- `output[].content[]`
- 其中 `type=output_text` 的 `text`

#### `chat/completions` 普通 JSON

优先读取：

- `choices[0].message.content`

如果 `content` 不是字符串，而是数组，也会继续尝试读取其中的 `text`

#### `responses` SSE

当前实现会读取事件：

- `type=response.output_text.delta`

#### `chat/completions` SSE

当前实现会读取：

- `object=chat.completion.chunk`
- `choices[].delta.content`

### 4.3.5 自动回退逻辑

当前代码不是“固定只打一种接口”。

逻辑是：

1. 如果你设为 `responses`，先请求 `/responses`
2. 如果请求成功，但抽不出最终文本，再自动尝试 `/chat/completions`
3. 如果你设为 `chat`，顺序反过来

这样做是因为很多兼容站虽然声称同时支持两种接口，但实际字段组织不完全一致。

### 4.3.6 如果你自己跑了中转站，应该怎么写

如果你是自己跑了一个 OpenAI 兼容中转站，那么这里完全可以直接接你自己的中转站。

示例：

```dotenv
R1LAB_OPENAI_BASE_URL=https://your-gateway.example/v1
R1LAB_OPENAI_API_KEY=<YOUR_GATEWAY_KEY>
R1LAB_OPENAI_MODEL=<YOUR_MODEL_NAME>
R1LAB_WIRE_API=responses
R1LAB_PREFER_SSE=true
```

如果你的中转站更适合 `chat/completions`，那就改成：

```dotenv
R1LAB_WIRE_API=chat
R1LAB_PREFER_SSE=false
```

也就是说，这里文档里说的“OpenAI 兼容接口”，完全可以是你自己建的中转站，不要求一定是官方接口。

### 4.4 安装并启动

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 scripts/run_server.py
```

### 4.5 本机自检

```bash
curl http://127.0.0.1:18888/health
curl -s http://127.0.0.1:18888/api/provider
curl -sG --data-urlencode 'q=把声音调小10%' http://127.0.0.1:18888/api/debug/stock-intent
```

## 5. 路由器里的 Hosts / DNS 到底怎么配

### 5.1 需要改写的域名

把下面三个域名都解析到你的后端机器 IP：

- `aios-home.hivoice.cn`
- `asrv3.hivoice.cn`
- `log.hivoice.cn`

假设你的后端机器 IP 是 `192.168.1.20`，目标就是：

- `aios-home.hivoice.cn -> 192.168.1.20`
- `asrv3.hivoice.cn -> 192.168.1.20`
- `log.hivoice.cn -> 192.168.1.20`

### 5.2 OpenWrt 例子

如果你用的是 OpenWrt，最常见的做法有两种。

#### 方案 A：LuCI 里加 Host 记录

在 `网络 -> DHCP/DNS -> Hostnames` 里添加三条：

- `aios-home.hivoice.cn` -> `192.168.1.20`
- `asrv3.hivoice.cn` -> `192.168.1.20`
- `log.hivoice.cn` -> `192.168.1.20`

保存并重启 `dnsmasq`。

#### 方案 B：直接写 dnsmasq

在 `/etc/dnsmasq.conf` 或 `/etc/dnsmasq.d/r1-stock-bridge.conf` 里加：

```conf
address=/aios-home.hivoice.cn/192.168.1.20
address=/asrv3.hivoice.cn/192.168.1.20
address=/log.hivoice.cn/192.168.1.20
```

然后执行：

```bash
/etc/init.d/dnsmasq restart
```

### 5.3 AdGuard Home 例子

如果你用 AdGuard Home，可以在 `DNS Rewrites` 里加三条重写：

- `aios-home.hivoice.cn` -> `192.168.1.20`
- `asrv3.hivoice.cn` -> `192.168.1.20`
- `log.hivoice.cn` -> `192.168.1.20`

### 5.4 其它支持 dnsmasq 的路由器

如果路由器支持附加 `dnsmasq` 配置，也可以直接写：

```conf
address=/aios-home.hivoice.cn/192.168.1.20
address=/asrv3.hivoice.cn/192.168.1.20
address=/log.hivoice.cn/192.168.1.20
```

### 5.5 改完后怎么验证

先在电脑上执行：

```bash
nslookup aios-home.hivoice.cn
nslookup asrv3.hivoice.cn
nslookup log.hivoice.cn
```

期望结果：

- 这三个域名都返回你的后端机器 IP

## 6. 为什么后端一定要监听 80 端口

因为 R1 访问原厂域名时，通常直接走 HTTP 默认端口。

也就是说：

- 你只开 `18888` 给自己调试还不够
- 给 R1 真机闭环时，后端最好同时监听 `80`

所以推荐：

```dotenv
R1LAB_PORTS=80,18888
```

## 7. 让 R1 真机接入

当下面三件事都满足后，就可以直接测试：

- 路由器 DNS 已改写
- 后端已经启动
- 后端的 `80` 和 `18888` 都在监听

现在可以直接对 R1 说一句短话，例如：

- `现在几点了`
- `今天天气怎么样`
- `暂停`

然后立刻查看：

```bash
curl -s http://127.0.0.1:18888/api/debug/r1
tail -f logs/server.log
```

## 8. 出问题时怎么排查

### 8.1 先看 DNS

如果 `nslookup` 不是你的后端 IP，先不要测 R1，说明路由器配置还没生效。

### 8.2 再看后端监听

在后端上执行：

```bash
ss -ltnp | grep -E ':80|:18888'
```

### 8.3 再看 `/api/debug/r1`

如果这里完全没有新事件，通常是：

- 路由器 DNS 还没生效
- R1 没拿到正确 DNS
- R1 走了别的出口

### 8.4 有请求但没有 `append_asr`

说明流量到了，但你拿到的不是预期的 stock 返回。

这时重点检查：

- `R1LAB_R1_REMOTE_HOST`
- `R1LAB_R1_REMOTE_PORT`
- `R1LAB_R1_REMOTE_HOST_HEADER`

## 9. Home Assistant 怎么接

如果你要接 HA，在 `config/.env` 里加：

```dotenv
R1LAB_HA_ENABLED=true
R1LAB_HA_BASE_URL=http://127.0.0.1:8123
```

## 10. 网易云音乐怎么接

如果你要开音乐接口，先在后端机器上安装 Node.js 18+，再执行：

```bash
bash scripts/install_netease_music_api.sh
```

然后在 `config/.env` 里启用：

```dotenv
R1LAB_MUSIC_ENABLED=true
R1LAB_MUSIC_ENDPOINT=http://127.0.0.1:3900
R1LAB_MUSIC_PUBLIC_BASE_URL=http://asrv3.hivoice.cn
```

## 11. ADB 脚本什么时候用

ADB 脚本主要用于两种情况：

- 你暂时还不想改路由器，想先做短链路验证
- 你要确认设备连通性、查看代理状态、手动触发说话

使用前先设置：

```bash
export R1LAB_R1_IP=<YOUR_R1_IP>
```

然后：

```bash
python3 scripts/r1_adb.py probe
bash scripts/r1_show_http_proxy.sh
bash scripts/r1_set_http_proxy.sh
bash scripts/r1_trigger_talk.sh
```

## 12. 公开部署边界

如果你在自己的 fork 里继续改，建议继续保持这条边界：

- 不提交真实 `.env`
- 不提交真实 cookie
- 不提交真实 token
- 不提交运行日志
- 不把第三方项目源码直接 vendoring 进来，除非你确认许可证边界

## 13. 参考仓库链接

如果你想继续研究这条路线，建议一起看这些仓库：

- `r1-iot-java`
  <https://github.com/ring1012/r1-iot-java>
- `r1-helper`
  <https://github.com/sagan/r1-helper>
- `NeteaseCloudMusicApi`
  <https://github.com/Binaryify/NeteaseCloudMusicApi>
- `Home Assistant Core`
  <https://github.com/home-assistant/core>
