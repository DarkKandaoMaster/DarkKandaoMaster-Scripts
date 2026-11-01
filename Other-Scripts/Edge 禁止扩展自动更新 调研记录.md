# Edge 禁止扩展自动更新 调研记录

日期：2026-10-11 至 10-12
环境：Edge 129.0.2792.65，Windows 11 家庭版 23H2，没有加入域或 MDM

## 结论

- Edge 没有"关闭扩展自动更新"的开关。
- **用 ExtensionSettings 策略冻结的方法，在本机实测失败**，原因是设备不受组织管理。
- 剩下可以试的路：
  - 启动参数 `--disable-background-networking`；
  - 开发者模式下"加载解压缩的扩展"。

## 方法一：ExtensionSettings 策略（失败）

做法：在 `HKLM\SOFTWARE\Policies\Microsoft\Edge` 下写一个 REG_SZ 值 `ExtensionSettings`，内容是：

```json
{"<扩展ID>":{"installation_mode":"normal_installed","update_url":"https://localhost","override_update_url":true}}
```

原理：`override_update_url=true` 会让 Edge 去策略里指定的 `update_url` 检查更新。这个地址无效，所以拿不到新版本。这个原理出自微软官方文档和 Blue Prism 文档。

实测结果（edge://policy 导出的内容）：

- 每个 ID 都被改成了 `[BLOCKED]<id>`，状态显示"错误, 警告"。
- 错误：`架构验证错误: Unknown property: [BLOCKED]...`
- 警告：此设备并非由你的组织管理。因此，它只能安装 Microsoft Store 中托管的扩展。
- 扩展的安装位置仍然是 1（用户安装），没有被策略接管。

为什么没有补救办法：如果把 `update_url` 改成 Edge 商店地址，倒是不会被拦，但官方文档写明，这种情况下 `override_update_url` 会被忽略。

我当初误判的原因：Chromium 上游源码里，"非托管设备不能安装商店外扩展"这条限制只拦 `force_installed`，不拦 `normal_installed`。Edge 自己额外加了限制，所以不能用上游源码推断 Edge 的行为。

其它限制（来自 Chromium 源码）：

- `override_update_url` 只在 `installation_mode` 为 `force_installed` 或 `normal_installed` 时才生效。
- `"*"` 通配符不能和 `normal_installed` 一起用。
- 所以就算策略能生效，也只能按扩展 ID 一个个写，以后新装的扩展不会自动覆盖到。

善后：策略已经撤销，注册表下没有值了；脚本已作废并删除。

## 方法二：启动参数 `--disable-background-networking`（未实测）

- Chromium 源码 `chrome_extensions_browser_client.cc` 里，`IsBackgroundUpdateAllowed()` 在带这个参数时返回 false。随后 `extension_downloader.cc` 里的 `StartUpdateCheck` 会直接判定更新失败。
- 对所有扩展都有效，包括以后新装的。
- 代价：
  - 会连带关掉其它后台联网，比如安全浏览名单和组件更新。
  - 只有带这个参数启动才生效，要改各处快捷方式，还要关掉"启动增强"。
  - 从其它程序点链接拉起 Edge 时不会带上这个参数。

## 方法三：解压加载（未实测）

- 按 Blue Prism 文档的说法，用"加载解压缩的扩展"装的扩展，浏览器不会自动更新。
- 代价：扩展 ID 可能变、数据可能丢；每次启动 Edge 都会提示关闭开发人员模式下的扩展。

## 来源

- [Detailed guide to the ExtensionSettings policy – Microsoft Learn](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-manage-extensions-ref-guide)
- [ExtensionSettings policy – Microsoft Learn](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/ExtensionSettings)
- [Disable browser extension automatic updates – Blue Prism](https://documentation.blueprism.com/bp-7-2/en-us/Guides/chrome-firefox/disable-auto-updates.htm)
- [Chromium CL 1140873004：后台联网禁用时不下载扩展更新](https://codereview.chromium.org/1140873004)
- Chromium 源码（main 分支，2026-10-11 读取）：`chrome/browser/extensions/extension_management_internal.cc`、`extension_management.cc`、`chrome_extensions_browser_client.cc`、`external_provider_manager.cc`、`extensions/browser/updater/extension_downloader.cc`、`extension_updater.cc`
