# r1-stock-bridge

`r1-stock-bridge` 是一个面向斐讯 R1 的局域网桥接后端。

它的目标不是刷机，而是尽量保留：

- 原厂唤醒
- 原厂麦克风采集
- 原厂 ASR
- 原厂播报

同时把后端能力替换成你自己的局域网服务：

- OpenAI 兼容大模型
- Home Assistant
- 网易云音乐接口
- 本地规则优先的播放/音量/模式控制

## 适合谁

这个仓库适合下面这类场景：

- 不想刷机，只想保留原厂前端
- 想把 R1 接到自己的 OpenAI 兼容接口
- 想在局域网里接管天气、音乐、家居控制和问答
- 想用一台轻量 Linux 盒子长期运行，不上本地大模型

## 核心思路

链路是这样的：

1. R1 继续向原厂域名发起请求
2. 路由器把几个关键域名重写到你的局域网后端
3. 本项目先把请求转发到原厂上游
4. 从上游返回里提取 `asr_recongize`
5. 本地规则先处理高频控制词
6. 再尝试 Home Assistant
7. 再尝试音乐分支
8. 最后才回退到 OpenAI 兼容大模型
9. 再把结果包装成 R1 能接受的原厂风格 JSON

## 当前能力

- 代理原厂 `/trafficRouter/cs` 链路
- 本地规则优先：
  - `暂停`
  - `继续播放`
  - `下一首`
  - `上一首`
  - `把声音调小10%`
  - `把声音调小百分之十`
  - `音量调到50%`
  - `静音`
  - `取消静音`
  - `随机播放`
  - `顺序播放`
  - `单曲循环`
  - `列表循环`
  - `休眠`
  - `关机`
  - `现在几点了`
  - `今天天气怎么样`
  - `我现在在哪里`
- OpenAI 兼容接口接入
- Home Assistant 中文实体匹配
- 网易云音乐搜索、播放、喜欢歌单、心动模式
- ADB 辅助脚本
- Linux 盒子一键安装脚本

## 文档

- 详细部署文档：
  [docs/DEPLOYMENT_CN.md](docs/DEPLOYMENT_CN.md)
- 第三方引用和许可证边界：
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- 示例环境变量：
  [config/.env.example](config/.env.example)

## 快速开始

1. 复制示例配置：

```bash
cp config/.env.example config/.env
```

2. 填写你的 OpenAI 兼容接口：

- `R1LAB_OPENAI_BASE_URL`
- `R1LAB_OPENAI_API_KEY`
- `R1LAB_OPENAI_MODEL`

3. 安装 Python 依赖并启动：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 scripts/run_server.py
```

4. 本机检查：

```bash
curl http://127.0.0.1:18888/health
curl -s http://127.0.0.1:18888/api/provider
curl -sG --data-urlencode 'q=把声音调小10%' http://127.0.0.1:18888/api/debug/stock-intent
```

5. 如果你已经完成路由器 DNS / Hosts 劫持，再对 R1 说一句话，然后查看：

```bash
curl -s http://127.0.0.1:18888/api/debug/r1
tail -f logs/server.log
```

## 公开仓库边界

这个公开仓库不包含以下内容：

- 真实 API Key
- 真实 cookie
- 真实 Home Assistant token
- 你的局域网 IP、设备 MAC、路由器配置快照
- 运行期日志
- 第三方项目源码打包副本

也就是说，这个仓库是“可学习、可部署、可二次开发”的公开版，不是某一台设备的完整私有快照。

## 引用说明

本项目在方案设计和兼容思路上参考了若干社区项目，例如：

- `ring1012/r1-iot-java`
- `r1-helper`
- `Binaryify/NeteaseCloudMusicApi`
- `Home Assistant`

公开版仓库里不会直接打包这些项目的源码副本；具体说明见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 免责声明

- 本项目仅用于学习、研究、个人设备改造和局域网自用。
- 请自行评估对原厂服务链路、第三方服务条款和所在地区法律法规的影响。
- 本项目与斐讯、云知声、网易云音乐、Home Assistant、OpenAI 均无官方隶属关系。
