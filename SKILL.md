---
name: plex-multiversion-audit
description: 审计 Plex 媒体库里"一个条目挂多个版本"的情况，分出哪几种需要修——被当成多版本的连续分段文件（-A/-B、CD1/CD2、_1_2）、同名同体积的重复拷贝、GUID 相同却没合并的两个条目、时长差 20% 以上的残缺版本，并生成 Markdown 报告/CSV。当用户说「检查多版本」「这个条目里好几个版本」「点进去只能看一半」「重复文件占空间」「库里哪些片子有多版本/多 Part」「合并一下重复条目」并涉及 Plex 时使用。只读，不改文件名、不删文件、不触发重扫。
agent_created: true
---

# Plex 多版本条目审计

Plex 把「同一部电影的多个文件」既不区分「不同版本」也不区分「同一部片的分段」，
一律塞进同一个 movie 的 `<Media>` 列表里。于是会出现：点开一部片有 4 个「版本」，
选哪个都只有 23 分钟（其实是 1 个主片 + 3 个特典）；或者同一部片存了两份，白占 10 GB。

这个 skill 把库里所有多版本条目拉出来，按**该怎么处理**分成 8 类，一条命令出报告。

## 何时使用

| 用户说的话 | 跑什么 |
|---|---|
| 「检查一下这个库里的多版本条目」 | `scan` |
| 「出一份报告给我」 | `report` |
| 「哪些是重复的，能省多少空间」 | `dups` |
| 「导出来我看看 / 丢进表格」 | `export --format csv` |
| 「上次那批片后来怎么处理的」 | `report --save-xml dump/` 后 `--offline dump/` |

## 快速上手

```bash
python <skill>/scripts/plex_audit.py scan -s 25
```

```
class     meaning                         items         size
SEG       分段文件被当成多版本                3      38.0 GB
ODD       时长异常（疑似残缺/剪辑版）         1      10.0 GB
UNMERGED  同片多条目未合并                    6      55.2 GB
DUP       同文件重复（两份拷贝）              3      39.9 GB
BADGE     版别变体（C/UC/无码破解 U）        22     298.6 GB
RES       分辨率/编码变体                     8     129.7 GB
PARTS     单版本多 Part（正常分段）           6      65.0 GB
SINGLE    普通单版本                       3148   17091.7 GB

reclaimable duplicate space: 19.9 GB
```

`-s` 接受 section key **或** 库名（`-s 学习资料` 也行），可重复。
默认详细列出 `SEG / ODD / UNMERGED / DUP` 四类，其余只在统计里出现。

## 命令

| 子命令 | 作用 | 是否写盘 |
|---|---|---|
| `scan` | 分类统计 + 逐条列出需要处理的 | 否 |
| `report` | 生成 Markdown 报告（每个版本的分辨率/编码/时长/容量/版别/绝对路径） | 写 .md |
| `dups` | 只列重复拷贝，算可回收空间 | 否 |
| `export` | 导出 CSV/JSON（每个 Media 一行） | 写文件 |

| 参数 | 含义 |
|---|---|
| `--section/-s KEY_OR_TITLE` | 目标库，可重复，缺省读 `config.sections` |
| `--only CODE` | `scan` 只看某一类，如 `--only DUP` |
| `--offline DIR\|FILE` | 用之前 `--save-xml` 存下的 XML 跑，不连服务器 |
| `--save-xml DIR` | 顺手把 section XML 存下来（3197 部约 6.7 MB） |
| `--config` / `--token` | 覆盖配置文件与令牌 |
| `--include-single` | `export` 时连普通单版本一起导 |

```bash
# 完整报告
python scripts/plex_audit.py report -s 25 -o 学习资料-多版本条目报告.md \
    --title "「学习资料」多版本检查" --host-note "192.168.1.10:32400" \
    --section-note "section 25 · 13 个路径" --date-note "2026-11-07"

# 先存一份快照，之后离线可复现
python scripts/plex_audit.py report -s 25 --save-xml dump/ -o r.md
python scripts/plex_audit.py scan --offline dump/
```

## 分类规则

判定顺序固定（改规则请同步 `scripts/selftest.py`）：

| 类 | 条件 | 该怎么办 |
|---|---|---|
| `SEG` | ≥3 个 Media；或 2 个且短的那个 <90%，且文件名像 `-A/-B`、`part1`、`CD1`、`_2` | 改名成 `-part1/-part2` 收进同一目录，让 Plex 认成同一个 Media 的多个 Part |
| `ODD` | 2 个 Media，时长差 >10% 但**没有**分段命名 | 通常是残缺/剪辑版，确认后删掉或重下 |
| `UNMERGED` | 同一个 GUID 被拆成两个 movie | 收进同一目录、按 `片名 - 1080p.mp4` 命名，Plex 才会合并 |
| `DUP` | 2 个 Media，**文件名相同**且体积差 <1%（通常是两个卷各存一份） | 删任一份 |
| `BADGE` | 时长一致，但角标类别不同（`C` 中文字幕 / `UC` 中文无码 / `U` 无码破解 / 无标记） | 正常，别动 |
| `RES` | 时长一致，仅分辨率/编码不同（4K vs 1080p、hevc vs h264） | 正常，别动 |
| `PARTS` | 1 个 Media、多个 Part | 正常分段，播放连续 |
| `SINGLE` | 其余 | — |

> **版别归类按语义，不按字面**：`-U` / `-LTX` / `_LTX` / `__restored` /
> `-BasicVSR++` / `-UNCENSORED` 都是无码破解，共用 `u.png`（带中文字幕则 `uc.png`）
> 和同一个「无码破解」类型标签，`badge_class()` 把它们归一成 `U`。所以
> `X-U.mp4` + `X-LTX.mp4` **不是**混装版别；`X-C.mp4` + `X-U.mp4` 才是。
> 角标后面可叠工具标记（`X_restored_wm`、`X_restored.LTX_wm`），正则取链上最后一个角标词元；
> `wm` 单独出现不算角标。报告「版别」列保留原始后缀，分类用归一后的值。

> ⚠ 一个 movie 在 Plex 里**只有一张海报、一个角标**。同一 item 内混装 `-C` 与 `-UC`
> 时，角标必然只对应其中一个版本 —— 所以批量刷角标后出现「角标和正在播的版本不一致」
> 不是刷错了，而是这类条目本身没法两全。要严格对应只能拆成两个条目。

## 配置

`<skill>/config.json`：

```json
{
  "plex_url": "http://192.168.1.10:32400",
  "prefs_path": "",
  "sections": ["1"],
  "timeout": 180
}
```

- Token 优先级：`--token` → `$PLEX_TOKEN` → `Preferences.xml` 的 `PlexOnlineToken`。
- `prefs_path` 留空会按 `plex_url` 的主机名推导 SMB 路径。
- 换 NAS / 换库只改这个文件，或 `--config other.json`；也可用环境变量 `PLEX_AUDIT_CONFIG`。

## 安全边界

只调用两个只读接口：`/library/sections` 和 `/library/sections/<n>/all?includeMedia=1`。
**不**改名、**不**删除、**不** PUT/DELETE、**不**触发重扫。报告只告诉你该怎么改，
动手由人确认。

## 离线回归

```bash
python scripts/selftest.py      # 8 个用例，不联网；改判定规则后必跑
```

`scripts/fixtures/section_sample.xml` 里每种异常各一条（含 `-C` 不能被当成分段、
`-A` 必须被当成分段、残缺版必须判 `ODD` 而不是 `SEG`）。
