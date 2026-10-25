# 测试报告（2026-10-25）

环境：Windows 10.0.26200 / Python 3.12.10 / 网络走 BMCLAPI 镜像 + 阿里云 Maven 回退
客户端：`app.cli`（无弹窗），线程数 32，含 assets，全部用例真实下载安装

| 编号 | 用例 | 结果 | 耗时 | 校验内容 |
| --- | --- | --- | --- | --- |
| t1 | 26.4-snapshot-2 原版 | PASS | 160s | 客户端 jar / 114 依赖库 / assets(476MB) / 版本 json |
| t2 | 1.21.1 NeoForge | PASS | 267s | 手动执行 install_profile 10 个 processor，生成 `neoforge-21.1.264-client.jar` |
| t3 | 1.21.1 Fabric | PASS | 96s | fabric-loader 0.20.1 profile + 依赖库 |
| t4 | 23w13a_or_b 原版 | PASS | 26s | 愚人节快照，旧格式 meta / assets |
| t5 | 1.12.2 原版 | PASS | 25s | legacy assets（1305 objects）+ natives |
| t6 | 1.12.2 Forge | PASS | 31s | spec0 install_profile，14.23.5.2859 universal |
| t7 | Create 6.0.5 模组 | PASS | 650s | `create-1.21.1-6.0.5.jar` 自动装到 1.21.1 NeoForge 实例 |
| t8 | 整合包（Modrinth） | PASS | 270s | skyblock 包：自动装 MC 1.21.4 + Fabric 0.18.0 + 15 模组 + overrides |
| cf1 | CurseForge 模组（JEI） | PASS | 7s | 自动补齐前置 `mezz_config` |
| cf2 | CurseForge 整合包 All the Mods 9 Sky | PASS | 135s | 自动装 Forge 1.20.1-47.4.10，310 个模组 + 1865 个 overrides 文件 |

补充验证：
- 光影包：Complementary Reimagined → `shaderpacks/`
- 材质包：Fresh Animations → `resourcepacks/`
- 数据包：VeinMiner → `datapacks/`（前置库自动分流到 `mods/`）
- GUI：7 个页签构建正常，实例目录/实例识别、来源切换（Modrinth ↔ CurseForge）正常
- exe：`dist/Minecraft下载器.exe`（17.2MB，带图标），启动正常，自动创建 `MCDownloader-data/`

备注：
- `assets` 采用全局缓存 + 硬链接，多实例复用；断点/低速检测（<20KB/s 持续 25s 自动换源）
- CurseForge 走 `mod.mcimirror.top` 镜像 API（免 Key）+ `mediafilez.forgecdn.net` 文件 CDN
- 测试实例已按要求删除
