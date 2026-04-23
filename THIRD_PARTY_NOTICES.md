# Third-Party Notices

本仓库公开版只包含 `r1-stock-bridge` 自身的代码、脚本和文档。

为了避免许可证和再分发边界不清，这个公开版不直接打包第三方项目源码副本；如果你要继续集成上游项目源码，请自行检查对应许可证并按要求保留版权声明。

## 参考项目

### `ring1012/r1-iot-java`

- 仓库：<https://github.com/ring1012/r1-iot-java>
- 用途：参考其 stock 代理思路、`/trafficRouter/cs` 闭环方向、`18888` 调试端口习惯、R1 返回 JSON 的组织方式
- 本仓库处理方式：仅参考设计思路，不直接复制其源码文件进入公开版仓库
- 说明：在准备公开版时，本地检出里未看到顶层 `LICENSE` 文件；因此这里不打包其源码副本

### `r1-helper`

- 仓库：<https://github.com/sagan/r1-helper>
- 用途：参考其设备侧控制、包名、ADB 辅助处理思路
- 本地参考副本许可证：GPL-2.0
- 本仓库处理方式：公开版不打包其源码副本

### `Binaryify/NeteaseCloudMusicApi`

- 仓库：<https://github.com/Binaryify/NeteaseCloudMusicApi>
- 用途：作为可选的外部音乐接口服务
- 本仓库处理方式：只通过安装脚本按需安装，不在公开版仓库内打包其源码

### `Home Assistant Core`

- 仓库：<https://github.com/home-assistant/core>
- 用途：作为可选的局域网智能家居控制目标
- 本仓库处理方式：通过 REST API 交互，不打包其源码

## 商标与服务说明

本仓库提到的品牌、服务名和产品名，包括但不限于：

- Phicomm / 斐讯
- 云知声
- 网易云音乐
- Home Assistant
- OpenAI

均归各自权利人所有。这里仅用于说明兼容目标、部署对象或接口来源，不代表任何官方关联。
