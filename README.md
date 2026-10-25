# Minecraft 下载器 v1.0

一个面向 Windows 的 Minecraft 资源下载器（**只下载安装，不提供启动游戏**）。
支持 **Modrinth** 与 **CurseForge** 双渠道（CurseForge 通过国内镜像，无需 API Key）。

---

## 功能

| 模块 | 说明 |
| --- | --- |
| 游戏实例 | 原版 / Fabric / Forge / NeoForge，自动下载客户端、依赖库、natives、assets、日志配置，并完成加载器安装（内置处理器实现，不依赖官方安装器联网） |
| 模组 | 搜索、选版本、安装到当前实例 `mods/`，**自动递归补齐必需前置** |
| 整合包 | 支持 Modrinth `.mrpack` 与 CurseForge 整合包 zip，直接安装到**当前选中实例文件夹**（mods + overrides/config），实例缺少 MC 本体或加载器时自动补齐 |
| 光影包 | 安装到 `shaderpacks/` |
| 材质包 | 安装到 `resourcepacks/` |
| 数据包 | 安装到 `datapacks/` 或指定存档 `saves/<存档>/datapacks/` |
| 设置 | 添加/选择实例目录、下载线程数 1~64、限速（整数 KB/s，0 为不限）、下载源（镜像/官方）、CurseForge API Key（可选） |

## 使用

1. 双击 `Minecraft下载器.exe`
2. 顶部选择**实例目录**（可添加多个，其下每个子文件夹是一个实例）
3. 「游戏实例」页签选版本 → 选加载器 → 填写实例名 → **下载并安装**
4. 装模组/光影/材质/整合包前，先在顶部选中目标实例
5. 右上角「内容来源」切换 Modrinth / CurseForge

## 设置项

- **实例目录**：可添加多个目录，随时切换；默认目录在程序旁的 `MCDownloader-data/instances`
- **下载线程数**：1~64（默认 16）
- **限速**：整数，单位 KB/s，0 = 不限速（全局限速，多线程共享）
- **下载源**：`镜像加速`（BMCLAPI，国内推荐）/ `官方源`；镜像异常时会自动回退官方
- **CurseForge Key**：留空走 `mod.mcimirror.top` 镜像（免 Key）；填入官方 Key 则直连官方 API

## 目录结构

```
实例目录/
  <实例名>/
    .mcdl-instance.json      # 实例元数据（MC 版本、加载器、版本 id）
    versions/<版本id>/       # 版本 json + 客户端 jar
    libraries/               # 依赖库
    assets/                  # 资源文件（objects 硬链接自全局缓存）
    natives/                 # 原生库
    mods/ config/ saves/ shaderpacks/ resourcepacks/ datapacks/
```

全局缓存：`MCDownloader-data/cache/`（assets 按哈希去重，多实例硬链接复用，不重复占空间）
配置文件：`MCDownloader-data/config.json`

## 说明

- 所有下载均做 SHA1 校验（有哈希时）；失败自动重试并切换镜像/官方/阿里云 Maven
- 内置解析 Forge/NeoForge 的 `install_profile.json` 并执行 processors，**不需要 Java** 即可安装加载器
- 本程序不包含游戏本体，不含启动功能；版权归 Mojang / 各模组作者所有
- 数据包为标准 zip，安装后仍需在游戏内 `/datapack enable` 或放入世界 datapacks 目录

## 常见问题

**下载很慢 / 卡住**：设置里提高线程数（如 32），确认下载源为「镜像加速」。

**CurseForge 搜不到东西**：镜像搜索为模糊匹配，可用简称（如 `atm9 sky`、`jei`）；也可直接粘贴项目 id。

**安装 Forge/NeoForge 失败**：查看日志中的「处理步骤」，通常是某个 Maven 依赖下载失败，重试即可（已自带阿里云 Maven 回退）。
