# plex-multiversion-audit

审计 Plex 电影库里的「一个条目挂多个版本」，并说清每种该怎么修。

Plex 不区分「同一部片的不同画质」和「同一部片被切成两段」，两者都以多个 `<Media>`
塞进同一个 movie。结果就是：点开一部片有 4 个「版本」，选哪个都只有 23 分钟；
或者同一部片在两个卷上各存一份，白占 10 GB 谁也不知道。

这个工具只读地把这些条目捞出来，按**处理动作**分类，输出 Markdown 报告 / CSV。

## 输出长什么样

```
$ python scripts/plex_audit.py scan -s 学习资料

class     meaning                         items         size
SEG       分段文件被当成多版本                3      38.0 GB
ODD       时长异常（疑似残缺/剪辑版）         1      10.0 GB
UNMERGED  同片多条目未合并                    6      55.2 GB
DUP       同文件重复（两份拷贝）              3      39.9 GB
BADGE     版别变体（C/UC/U/LTX）             22     298.6 GB
RES       分辨率/编码变体                     8     129.7 GB
PARTS     单版本多 Part（正常分段）           6      65.0 GB
SINGLE    普通单版本                       3148   17091.7 GB

reclaimable duplicate space: 19.9 GB

-- SEG (3) --
  41733   SAMPLE-001 主片 + 3 个特典              [23/23/23/79 min] !分段命名,时长偏短
  22880   SAMPLE-002 上半 + 下半                   [135/103 min] !分段命名,时长偏短
  41572   SAMPLE-003 VR 三段                     [27/30/33 min]
```

## 分类与动作

| 类 | 判定 | 动作 |
|---|---|---|
| `SEG` | ≥3 个 Media，或 2 个且短的 <90% 且文件名像 `-A/-B`、`part1`、`CD1`、`_2` | 改名为 `-part1/-part2` 收进同一目录，让 Plex 合并为一个 Media 的多个 Part |
| `ODD` | 2 个 Media，时长差 >10% 但没有分段命名 | 残缺/剪辑版，确认后删或重下 |
| `UNMERGED` | 同一 GUID 被拆成两个 movie | 收进同一目录并按 `片名 - 1080p.mp4` 命名 |
| `DUP` | 文件名相同、体积差 <1%（不同卷各一份） | 删任一份 |
| `BADGE` | 时长一致，`-C`/`-UC`/`-U`/`-LTX` 不同 | 正常，别动 |
| `RES` | 时长一致，仅分辨率/编码不同 | 正常，别动 |
| `PARTS` | 单 Media 多 Part | 正常分段 |
| `SINGLE` | 其余 | — |

顺带一个容易踩的坑：Plex 里一个 movie **只有一张海报和一个角标**。同一条目混装
`-C`（中文字幕）与 `-UC`（中文无码）时，角标只能反映其中一个版本 —— 批量刷角标后
「角标与正在播的版本不一致」不是刷错了，而是这类条目本身没法两全。

## 安装

无第三方依赖，Python 3.6+（也兼容 2.7）。把仓库放到
`~/.workbuddy/skills/plex-multiversion-audit/`（Windows 是
`C:\Users\<你>\.workbuddy\skills\plex-multiversion-audit\`），入口是 `SKILL.md`。

### 让 Agent 自己装（复制这段提示词）

把下面整段丢给 WorkBuddy / Claude Code 这类能读写本地文件的 Agent 即可：

```text
把 https://github.com/sayaxia/plex-multiversion-audit 安装成我的 WorkBuddy skill。
公开仓库，直接 git clone 即可，不需要 token。

1. 克隆到 %USERPROFILE%\.workbuddy\skills\plex-multiversion-audit
   （macOS/Linux 换成 ~/.workbuddy/skills/plex-multiversion-audit），不要克隆到临时目录。
2. 复制 config.example.json 为 config.json，把 plex_url 改成我的 Plex 地址
   （例如 http://192.168.1.10:32400）。
   prefs_path 留空即可：脚本会按 plex_url 的主机名推导 SMB 路径读 Preferences.xml 拿 token。
3. 运行 python scripts/selftest.py，必须 8/8 通过，不通过就报错给我，不要改测试用例。
4. 运行 python scripts/plex_audit.py scan -s <库名，例如 学习资料>，把分类统计结果贴出来。
5. 这个工具是只读的：不要改任何文件名、不要删文件、不要触发 Plex 重扫。
```

Agent 装完你应该能直接说「检查一下某个库里的多版本条目」触发它。

已经在用的机器更新到最新版：

```bash
git -C ~/.workbuddy/skills/plex-multiversion-audit pull
python ~/.workbuddy/skills/plex-multiversion-audit/scripts/selftest.py
```

### 手动安装

```bash
git clone https://github.com/sayaxia/plex-multiversion-audit.git \
    ~/.workbuddy/skills/plex-multiversion-audit
cd ~/.workbuddy/skills/plex-multiversion-audit
cp config.example.json config.json    # 改成自己的 plex_url
python scripts/selftest.py            # 8 个离线用例，先确认环境
python scripts/plex_audit.py scan -s 电影
```

`config.json` 已在 `.gitignore` 里，克隆下来是空的 —— 这是故意的：地址、SMB 路径这类
环境信息留在本地，不进版本库；仓库是公开的，代码本身不含任何密钥或令牌。

## 配置

```json
{
  "plex_url": "http://192.168.1.10:32400",
  "prefs_path": "",
  "sections": ["1"],
  "timeout": 180
}
```

令牌优先级：`--token` → `$PLEX_TOKEN` → `Preferences.xml` 里的 `PlexOnlineToken`。
`prefs_path` 留空会按 `plex_url` 的主机名推导出 SMB 路径，所以最小配置只要一行
`plex_url`。

## 命令

| 命令 | 说明 |
|---|---|
| `scan -s 25` | 分类统计 + 逐条列出待处理项 |
| `report -s 25 -o r.md` | Markdown 报告：每个版本的分辨率/编码/时长/容量/版别/绝对路径 |
| `dups -s 25` | 只列重复拷贝并计算可回收空间 |
| `export -s 25 -f csv` | 每个 Media 一行，便于丢进表格 |
| `--save-xml dump/` + `--offline dump/` | 存快照后离线复跑，报告可复现 |

`-s` 接受 section key 或库名，可重复；不带 `-s` 时读 `config.sections`，
再退回「所有 movie 库」。

## 安全边界

只调用 `/library/sections` 与 `/library/sections/<n>/all?includeMedia=1` 两个只读接口。
不改名、不删除、不发 PUT/DELETE、不触发重扫。报告只给建议，动手由人确认。

## 测试

```bash
python scripts/selftest.py     # 8/8
```

`scripts/fixtures/section_sample.xml` 每种异常各一条，且专门盯住两个容易写错的边界：
`-C`（角标）不能被误判成分段，95 分钟的残缺版必须判 `ODD` 而不是 `SEG`。

## License

MIT
