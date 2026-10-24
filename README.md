# m-dlp

在终端中解析磁力链接或本地 `.torrent`、筛选文件并下载，支持交互文件树、持久化任务队列、限速、配置文件、元数据缓存、快速恢复和网络诊断。

## 安装

支持 Python **3.12 / 3.13**，推荐 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。在项目目录运行：

```bash
uv sync
uv run m-dlp --help
```

也可以安装为独立命令：

```bash
uv tool install --python 3.12 .
m-dlp --help
```

或在 Python 3.12 虚拟环境中使用 `pip install .`。libtorrent 包含原生扩展；
若安装提示没有匹配的 wheel，请检查 Python 版本、操作系统和 CPU 架构是否在
[libtorrent 的发行文件](https://pypi.org/project/libtorrent/2.0.15/#files)中。
当前已在 macOS ARM64 / Python 3.12 上验证。

## 使用

磁力链接包含 `&` 等 shell 特殊字符，**请始终用引号包裹完整链接**。
下方的 `<磁力链接>` 需替换成实际的 `magnet:?xt=...` 链接。

```bash
# 交互模式：方向键移动、空格多选、/ 搜索、Enter 确认下载
uv run m-dlp '<磁力链接>' -o ./downloads

# 先按扩展名筛选，再进入文件树选择
uv run m-dlp '<磁力链接>' --ext mkv --interactive -o ./downloads

# 简单终端中使用编号输入（例如 1,3-5）、all 或 q
uv run m-dlp '<磁力链接>' --simple-select -o ./downloads

# 只查看文件列表，不创建输出目录、不下载文件内容
uv run m-dlp '<磁力链接>' --list

# 指定文件编号，适合脚本和无交互终端
uv run m-dlp '<磁力链接>' --select '1,3-5' -o ./downloads

# 下载所有普通文件
uv run m-dlp '<磁力链接>' --all -o ./downloads

# 调整元数据和传输停滞超时；停滞超时设为 0 表示一直等待
uv run m-dlp '<磁力链接>' --all --metadata-timeout 180 --stall-timeout 600

# 按文件名、类型和大小筛选；重复的 include/ext 条件分别取并集
uv run m-dlp '<磁力链接>' --include '*.mkv' --exclude '*sample*' -o ./downloads
uv run m-dlp '<磁力链接>' --ext mkv --ext mp4 --min-size 500M --max-size 8GiB

# 在符合条件的文件中下载最大的一个
uv run m-dlp '<磁力链接>' --ext mp4 --largest

# 先查看筛选结果（保留原文件编号）
uv run m-dlp '<磁力链接>' --list --include 'Season 1/*'

# 恢复相同目录中上次保存的文件选择和下载状态
uv run m-dlp '<磁力链接>' --resume -o ./downloads

# 强制重新校验已有内容；适合怀疑文件损坏时使用
uv run m-dlp '<磁力链接>' --resume --recheck -o ./downloads

# 仅探测 15 秒，观察指定文件的分片覆盖和网络状态，不下载文件内容
uv run m-dlp '<磁力链接>' --diagnose --select 1,3 --probe-seconds 15

# 限制下载为 10 MiB/s、上传为 1 MiB/s
uv run m-dlp '<磁力链接>' --all --download-limit 10M --upload-limit 1M

# 读取本地种子，沿用相同的文件选择、筛选和恢复参数
uv run m-dlp ./example.torrent --list
uv run m-dlp ./example.torrent --ext mkv -o ./downloads

# 从磁力链接导出种子后退出，不下载文件内容
uv run m-dlp '<磁力链接>' --export-torrent ./example.torrent

# 指定配置文件，或临时忽略所有配置文件
uv run m-dlp ./example.torrent --all --config ./settings.toml
uv run m-dlp ./example.torrent --list --no-config

# 从文件读取多个磁力链接，最多同时运行 3 个任务
uv run m-dlp --batch-file links.txt --all --jobs 3 -o ./downloads
```

`python -m m_dlp` 与 `m-dlp` 等价。文件编号从 **1** 开始，以显示的列表为准。
`--list`、`--select`、`--all`、`--resume`、`--export-torrent`、`--interactive`、`--simple-select` 互斥。
未配置输出目录时写入当前目录。
无交互输入时必须显式指定选择参数、筛选条件、`--list`、`--diagnose` 或 `--export-torrent`，避免意外下载全部内容。

### 交互选择文件

单资源命令没有指定选择参数或筛选条件时，默认进入交互文件树。初始不选择任何文件，确认前只获取元数据。
目录置于文件之前，文件保留原始编号。目录可展开、收起，支持滚动及窗口大小变化；当前完整路径显示在列表下方。

| 按键 | 操作 |
| --- | --- |
| `↑` / `↓` | 移动光标 |
| `←` / `→` | 收起 / 展开目录；已收起时向左返回父目录，已展开时向右进入子项 |
| `Home` / `End` | 跳到首项 / 末项 |
| `PageUp` / `PageDown` | 翻页 |
| `Space` | 切换文件选择；在目录上递归选择或取消其文件 |
| `/` | 进入搜索输入，实时筛选完整路径，不区分大小写，支持中文和空格 |
| 搜索时 `Enter` / `↓` | 返回列表，保留搜索条件 |
| `Esc` | 清空搜索并返回列表 |
| `a` / `n` | 全选 / 清空当前搜索结果，包含收起目录中的匹配文件 |
| 列表中 `Enter` | 确认已选文件并下载；未选择时提示继续选择 |
| `q` | 取消选择，不下载，退出码 `0` |
| `Ctrl+C` / `Ctrl+D` | 中断并退出，退出码 `130` |

复选框 `[ ]` 表示未选，`[-]` 表示目录部分选中，`[x]` 表示已全选。
目录选择包含所有层级的子文件，收起目录不会丢失选择。
底部实时显示**已选文件数、总大小及精确字节数**，零字节文件也能独立选择。

搜索按字面子串匹配完整路径，不使用 glob 或正则。搜索不会清空已选内容，
底部的“搜索隐藏”显示已选但不符合当前搜索词的文件数量，确认时这些文件仍包含在下载中。
搜索期间，目录复选框、目录大小、空格和 `a` / `n` **只作用于匹配文件**，不会额外选中隐藏的文件。
清空搜索后恢复原来的目录展开状态。

已有 `--select`、`--all`、`--resume` 和直接筛选下载的行为保持不变；
需要对筛选结果继续手选时，增加 `--interactive`，例如 `--ext mkv --interactive`。
筛选后保留原始编号，不重新编号。
`--interactive` 和 `--simple-select` 要求终端输入，不支持批量模式或 `--diagnose`。

全屏界面要求标准输入、输出均连接到可用终端。`TERM=dumb` / `unknown` 或输出被重定向时自动回退到编号输入；
也可用 `--simple-select` 主动选择编号输入。脚本和管道请继续使用明确的非交互参数。
退出选择器时恢复正常终端输入、光标和原来的屏幕。

### 上传和下载限速

`--download-limit`、`--upload-limit` 独立控制传输上限，单位是**字节/秒**，支持与文件大小相同的单位。
例如 `500K` = 512,000 B/s，`1MB` = 1,000,000 B/s，`1.5M` = 1,572,864 B/s。
默认均为 `0`，表示不限速；**上传设为 0 不代表禁止上传**。
上限适用于单个任务的 BT 传输，包括局域网节点；短时速度可能波动，不是整个系统网络流量的限制。
批量模式中每个任务分别应用该限速，例如 3 个并发任务、每个任务 `--download-limit 10M`，合计可能达到 30 MiB/s。
支持范围为 0～2,147,483,647 B/s。恢复下载时采用本次参数或配置中的限速，覆盖上次保存的值。
设置很低的限速时，元数据获取也可能变慢，可相应增大超时。

### 配置文件

使用标准 TOML，支持以下全部配置项（均可省略）：

```toml
output = "./downloads"
jobs = 3
download-limit = "10M"
upload-limit = "1M"
metadata-timeout = 120
stall-timeout = 300
probe-seconds = 10
no-cache = false
# cache-dir = "./cache"
# queue-db = "./state/queue.sqlite3"
```

可直接参考仓库中的 `config.example.toml`。配置项名称与长命令行参数相同，写在文件顶层，不使用表头。
限速可用带单位的字符串或表示 B/s 的整数；秒数可用整数或小数；`jobs` 为正整数；`no-cache` 使用 TOML 布尔值。
配置里的路径支持 `~`，相对路径以**配置文件所在目录**为基准；命令行相对路径仍以当前工作目录为基准。

配置查找顺序为：

1. `--config FILE` 指定的文件。
2. 环境变量 `M_DLP_CONFIG` 指定的文件。
3. 默认位置：设置了 `XDG_CONFIG_HOME` 时使用 `$XDG_CONFIG_HOME/m-dlp/config.toml`；否则 macOS/Linux 使用 `~/.config/m-dlp/config.toml`，Windows 使用 `%APPDATA%/m-dlp/config.toml`。

只读取其中一个文件，不叠加多个配置。**命令行参数 > 配置文件 > 内置默认值**。
`--no-config` 跳过所有配置；默认位置没有文件时正常使用内置默认值，显式指定的文件不存在时会报错。
语法错误、未知配置项或无效值会在联网前报错，退出码为 `2`；`--help` 和 `--version` 不受损坏配置影响。
`--cache` 可覆盖配置中的 `no-cache = true`；`--download-limit 0` / `--upload-limit 0` 可取消配置中的限速。
缓存路径优先级为 `--cache-dir` > 配置中的 `cache-dir` > `M_DLP_CACHE_DIR` > 平台默认缓存目录。
选择方式和筛选条件需显式传入命令行，不从配置文件自动开启下载。

### 批量下载和任务队列

将磁力链接保存到 UTF-8 文本文件，每行一个，允许空行和以 `#` 开头的整行注释，也接受 UTF-8 BOM。
文件中直接放链接，不加 shell 引号，不使用行尾注释。例如 `links.txt`：

```text
# 替换为实际磁力链接
magnet:?xt=urn:btih:...
magnet:?xt=urn:btih:...
```

```bash
# 下载每个资源的全部普通文件，最多并发 3 个任务
uv run m-dlp -i links.txt --all -j 3 -o ./downloads

# 每个资源只下载匹配条件的最大文件
uv run m-dlp -i links.txt --ext mkv --largest --jobs 2 -o ./downloads

# 预览各资源文件清单，不创建下载目录
uv run m-dlp -i links.txt --list

# 对已有选择记录的批量任务恢复选择和下载进度
uv run m-dlp -i links.txt --resume -o ./downloads
```

`-i` 等同于 `--batch-file`，与位置参数中的单个磁力链接或种子文件互斥。
`-j` 等同于 `--jobs`，默认 **3**，设为 **1** 时顺序下载，也可以用 TOML 的 `jobs` 设置。
新任务默认按输入顺序启动；已有任务或运行期间调整过优先级、置顶和顺序时，按保存的调度顺序启动。
一个任务从元数据解析到下载、保存状态完成，全程占用一个并发名额。

批量模式必须明确指定 `--all`、`--select`、`--resume`、筛选条件或 `--list`，不会进入交互选择。
编号选择和筛选条件分别应用于每个资源；某个资源编号无效、没有匹配文件或下载失败，会记录失败并继续其他任务。
批量文件目前接受磁力链接；批量模式支持下载和列表预览，不与 `--diagnose`、`--export-torrent` 同用。

开始前先校验整个输入文件；格式错误会标出行号并以退出码 `2` 结束。
按已知 info hash 去重，重复资源只保留最早一行的链接及其 tracker/peer 信息，并显示跳过数量。
v1/v2 两种链接之间只有在文件里的混合链接提供两种 hash 的对应关系时才能去重。

每个资源下载到 **`<输出目录>/<info-hash>/`**，内部保留种子原有路径，避免不同资源的同名文件互相覆盖。
纯 v1 使用 40 位 hash，已知 v2 时优先使用 64 位 hash。相同链接重新排序不改变任务目录。
批量进度与单链接使用输出根目录时的记录分开；若改为单链接继续某个批量任务，`-o` 需指向它的 hash 子目录。

批量下载会先把任务、选择条件和下载参数保存到持久化队列，再处理本次提交的任务 ID；
`--list` 预览不会添加持久化任务。结果会显示成功、失败、暂停、取消或等待状态，有失败返回 `1`，否则返回 `0`。
`Ctrl+C` 停止启动新任务，保存正在运行任务的恢复状态，将它们重新置为等待，返回 `130`。
以后执行 `m-dlp queue run` 即可继续，无需原来的输入文件，也无需重复传入选择条件。
`--no-cache` 时仍保存任务队列并保留已下载文件，但不保存快速恢复缓存；重启后会重新获取元数据并校验已有内容。

### 持久化队列和任务管理

```bash
# 只添加任务，暂不下载；支持磁力链接、本地种子和批量文件
uv run m-dlp queue add '<磁力链接>' --all -o ./downloads
uv run m-dlp queue add ./example.torrent --select 1,3 -o ./downloads
uv run m-dlp queue add -i links.txt --ext mkv -o ./downloads

# 获取元数据并交互选择，确认后仅入队，不开始下载
uv run m-dlp queue add ./example.torrent --interactive -o ./downloads

# 前台运行等待中的任务，最多同时运行 2 个
uv run m-dlp queue run --jobs 2

# 在交互面板中运行，空闲时继续等待新任务或恢复任务
uv run m-dlp queue run --interactive --jobs 2

# 已在另一个终端运行下载时，打开管理面板
uv run m-dlp queue tui

# 在另一个终端查看和控制任务；数字为持久化任务 ID
uv run m-dlp queue list
uv run m-dlp queue show 1
uv run m-dlp queue pause 1 2
uv run m-dlp queue resume 1
uv run m-dlp queue cancel 2

# 优先下载任务 2、3；移动同级任务；置顶或取消置顶
uv run m-dlp queue priority 2 3 --level high
uv run m-dlp queue move 3 --direction up
uv run m-dlp queue pin 1
uv run m-dlp queue pin 1 --off

# 便于脚本读取：状态字段使用英文，详情包含保存的参数和时间戳
uv run m-dlp queue list --state failed --json
uv run m-dlp queue show 1 --json
```

`queue add` 要求明确传入 `--all`、`--select`、`--resume` 或筛选条件，或使用 `--interactive` / `--simple-select` 现场选择。
普通入队不联网；交互入队会获取元数据，确认后保存明确的文件编号，取消时不创建任务，两种方式都不下载文件内容。
不接受 `--list`、`--diagnose` 和 `--export-torrent`。它沿用批量下载的 **`<输出目录>/<info-hash>/`** 目录规则，
单个种子也有 hash 子目录。普通单链接命令保持原来的交互方式；需要持久化管理时使用 `queue add`。

任务 ID 在同一个数据库里稳定且不重复。重复添加同一输出目录的未完成任务会沿用原 ID、参数、状态和排序设置，
不会自动恢复暂停任务或覆盖保存的限速。已完成或已取消的资源再次添加会生成新 ID，并复用目录中的已有文件。
同一队列内，未完成任务会保留其输出目录，防止任务同时写入相同位置。

| 状态 | 含义 |
| --- | --- |
| `pending` | 等待调度；包括被 Ctrl+C 中断后等待继续的任务 |
| `running` | 正在获取元数据、下载或保存进度 |
| `pausing` / `paused` | 正在保存并停止 / 已暂停，不占并发名额 |
| `cancelling` / `cancelled` | 正在保存并退出 / 已取消 |
| `completed` | 所选文件已完成 |
| `failed` | 下载失败，`queue show ID` 查看原因 |

暂停和取消都保留已下载文件及可用的恢复缓存，暂停会释放并发名额。
`queue resume` 将暂停或失败任务重新排入队列，也可以撤回尚在保存进度的暂停请求；旧下载完全退出后才会重启。
已完成、已取消的任务不会被 `resume` 重启，需要重新 `queue add`。
同一条管理命令中的多个 ID 一起校验、一起修改；任一 ID 无效时不会部分执行。

`queue run` 在前台运行，自动接收运行期间新添加或恢复的任务；没有运行中或等待中的任务时退出，
默认不会驻留等待暂停任务；`queue run --interactive` 则会保持面板和调度器运行，直至主动退出。
**`queue resume` 只修改状态；没有运行中的队列进程时，还需执行 `queue run`。**
原来的 `-i FILE` 命令只处理本次提交的 ID；其他任务需交给 `queue run`。
每个队列数据库最多运行一个下载进程，管理命令可以在其他终端同时执行。
进程崩溃或被强制结束后，下一次 `queue run` 会恢复遗留的运行状态，并保留已有暂停/取消请求；
未落盘的最新进度可能需要重新校验。数据库锁随进程退出释放，无需手动删除锁文件。

交互面板每半秒刷新列表，下载进度由运行进程约每秒保存一次。显示状态、百分比、已下载/总大小、
速度和节点数，下方显示当前任务的输出目录与错误信息；列表也显示优先级和“顶”标记。
窄终端保留优先级、置顶、状态和百分比，高度不足时收起详情区。
刷新时按任务 ID 保持焦点和勾选状态；当前任务不再符合筛选条件时，焦点移到可见列表中的相邻任务。

| 按键 | 操作 |
| --- | --- |
| `↑` / `↓` | 移动焦点 |
| `Home` / `End`、`PageUp` / `PageDown` | 首尾跳转、翻页 |
| `/` | 搜索名称、ID、hash 或输出目录，输入时实时过滤；Enter 返回列表 |
| `f` | 打开状态筛选菜单，方向键选择，Enter 应用，Esc 取消 |
| `Space` | 勾选或取消勾选当前任务 |
| `a` / `n` | 勾选全部当前搜索/筛选结果 / 清空所有勾选（含隐藏项） |
| `Esc` | 在列表或搜索框中清空搜索和状态筛选，保留勾选 |
| `p` | 暂停全部已选任务；未勾选时暂停当前任务 |
| `r` | 恢复或重试全部已选任务；未勾选时操作当前任务 |
| `c` | 取消全部已选任务；未勾选时操作当前任务，保留文件，取消后不能直接恢复 |
| `K` / `J`（大写） | 将已选任务上移 / 下移一位；未勾选时移动当前任务；限相同置顶状态、相同优先级组内 |
| `t` | 置顶已选任务；若全部已置顶则取消置顶；未勾选时切换当前任务 |
| `+` / `-` | 提高 / 降低已选任务的优先级；未勾选时调整当前任务；限制在低、普通、高三级 |
| `q` | 退出面板 |
| `Ctrl+C` / `Ctrl+D` | 中断退出，返回 `130` |

搜索使用不区分大小写的子串匹配，与状态筛选同时生效。状态菜单包含全部、等待运行、运行中、暂停中、
已暂停、取消中、已取消、已完成和失败。搜索输入期间，空格及 `p/r/c/f/a/n/q/K/J/t/+/-` 都作为普通文字输入，
不会执行任务操作；先按 Enter 返回列表，再使用快捷键。

状态栏显示可见/总任务数、勾选数量及隐藏勾选数量。**批量操作会包含被搜索或状态筛选隐藏的已选任务**；
`a` 只增加当前可见任务，`n` 会清空所有勾选。实时刷新和新增任务不会自动勾选新任务，操作完成后保留勾选，
方便先暂停再恢复。例如：`/` 输入名称 → Enter → `a` → `p` 可暂停匹配的任务。
批量操作使用同一事务校验所有任务；如果其中任何任务当前状态不支持操作（例如暂停已完成任务），
整批不执行并显示原因，勾选也会保留。可调整勾选或状态筛选后重试。

调度和列表共用顺序：**置顶任务优先 → 高、普通、低优先级 → 同组手动顺序**。
新任务为普通优先级、不置顶，追加到该组末尾。置顶移动到相应置顶优先级组的前面；取消置顶移到原优先级的非置顶组末尾。
改变优先级时移到目标组末尾，已经达到最高 / 最低级的任务保持原顺序。
`queue move` 支持 `up`、`down`、`top`、`bottom`，分别在同组内上移、下移、移到最前、移到最后；
多选时保持选中任务之间的相对顺序。置顶优先于高优先级，需要跨组调整时使用 `t` 或 `+/-`。

排序操作也包含隐藏的已选任务，移动按**完整队列**计算相邻位置，可能经过被筛选隐藏的任务。
搜索和状态筛选只改变显示，不改变调度范围；任务编号、下载文件和已保存进度不随排序变化。
调整后，调度器在下一个并发名额空出时按新顺序选择等待任务，**不会打断正在下载的任务**。
暂停、取消和已完成任务即使排在前面也不会自动开始下载；恢复任务会保留优先级和置顶设置。

优先级、置顶和手动顺序保存在队列数据库中，重启后继续生效。旧数据库首次打开时自动升级，原任务保持原顺序及普通优先级。
升级程序后请重新启动旧版本的下载进程，使其采用新的调度规则。
`queue list/show --json` 的 `priority` 为 `0`（低）、`1`（普通）、`2`（高），`pinned` 为布尔值，
`position` 是同组排序值，调整后可能重新编号，不等同于任务 ID 或列表行号。

`queue tui` 只负责查看和管理，退出不会停止其他终端里的下载；按 `r` 后需有运行中的队列进程才能继续下载。
`queue run --interactive` 可以在空队列时启动，暂停全部任务后仍等待恢复，也会接收另一个终端新添加的任务。
退出该面板时会等待当前下载保存进度，将尚在运行的任务重新排队；暂停和取消状态继续保留。
已完成的文件不会重新下载，后续再次 `queue run` 可继续未完成任务。
每个数据库仍只允许一个下载进程；已有下载进程时请用 `queue tui` 连接管理。
交互模式要求标准输入和输出均为可用终端，不支持管道或 `TERM=dumb`；脚本继续使用原有管理命令。
两个入口均支持 `--queue-db`、`--config` 和 `--no-config`，交互运行的 `--jobs` 仍可从配置文件读取。

队列保存源链接、选择条件、限速、超时、绝对输出及缓存路径、错误、时间和进度快照（运行时约每秒更新）。
本地 `.torrent` 内容也会保存，原文件移动或删除后仍能运行任务。
已入队任务采用保存的下载参数，不受后来工作目录或配置文件变化影响；`queue run --jobs` 决定本次并发数。
若要改变未完成任务的选择或限速，先取消该任务，再用新参数添加。
队列与可清理的下载缓存独立存放，默认目录如下：

- 设置了 `XDG_DATA_HOME`：`$XDG_DATA_HOME/m-dlp/queue.sqlite3`。
- macOS：`~/Library/Application Support/m-dlp/queue.sqlite3`。
- Linux：`~/.local/share/m-dlp/queue.sqlite3`。
- Windows：`%LOCALAPPDATA%/m-dlp/queue.sqlite3`。

路径优先级：`--queue-db FILE` > TOML 中的 `queue-db` > `M_DLP_QUEUE_DB` > 默认目录。
管理命令也接受 `--config` / `--no-config`，参数放在子命令后，例如：

```bash
uv run m-dlp queue list --queue-db ./state/queue.sqlite3
uv run m-dlp queue run --queue-db ./state/queue.sqlite3 -j 2
```

所有操作应使用同一个数据库；不同数据库的任务需要使用不同输出目录。
数据库包含磁力链接（可能带 tracker 凭据）和本地路径，请保存在个人目录；新建数据库使用仅用户可读写的权限。

### 本地 .torrent 和导出

本地种子支持 v1、v2 和混合格式，可离线列出文件，并保留其中的 tracker、web seed 和分片哈希层。
后续下载、诊断、筛选、限速和恢复行为与磁力链接一致；同一资源和输出目录可在两种输入形式间继续下载。
这里接受本地文件路径，不接受 HTTP 种子 URL。

`--export-torrent FILE` 获取元数据并导出整个资源后退出，不下载文件内容，不创建 `-o` 指定的下载目录。
导出目标存在时拒绝覆盖；需要换一个文件名。该选项不能与文件选择、筛选条件、`--list` 或 `--diagnose` 同用。
本地种子导出保留原始文件内容。磁力链接导出保留 info hash、链接携带的 tracker 和 web seed。

**v2 导出限制：** 磁力元数据交换可能尚未取得完整的 piece layers（分片哈希层）。
此时会给出提示，并按 libtorrent 支持的方式导出；m-dlp 可在下载时从在线节点补齐，部分其他客户端可能不接受该文件。
需要完整种子文件用于跨客户端分享时，请使用发布者提供的原始 `.torrent`。

### 筛选规则

- `--include` / `--exclude`：使用 glob 通配符，匹配完整的种子内相对路径或文件名；匹配不区分大小写。`*` 可跨目录，模式需加引号。
- `--ext`：按扩展名匹配，不区分大小写，`mkv` 和 `.mkv` 等价；多种类型请重复参数。
- 多个 `--include` 条件取并集，多个扩展名取并集；与编号选择、排除条件和大小范围取交集。
- `--min-size` / `--max-size`：包含边界。纯数字和 `B` 表示字节；`K/M/G/T` 与 `KiB/MiB/GiB/TiB` 使用 1024 进制，`KB/MB/GB/TB` 使用 1000 进制。
- `--largest` 最后生效，只选一个最大文件；大小相同时选编号最小的文件。
- 指定筛选条件后会直接下载匹配文件，不再进入编号输入；没有匹配文件时退出码为 `2`，不会创建下载目录。可先加 `--list` 预览。

### 缓存与快速恢复

下载时显示已完成字节、速度、节点数及预计剩余时间。选中文件完成校验并写入后退出。
元数据成功获取后自动缓存，再次列出文件无需等待在线节点。
下载状态和文件选择按 **资源 info hash + 输出目录绝对路径** 保存；每 30 秒请求一次检查点，
正常完成、`Ctrl+C` 和可处理的下载错误退出时也会尝试保存。
缓存有效时使用 libtorrent 快速恢复，减少重新校验；没有缓存、缓存损坏，或文件大小、时间、身份等属性发生变化时回退到校验。

使用同样的选择参数会自动尝试快速恢复；`--resume` 额外恢复上次保存的文件选择。
可以换用新的 `--select` 或筛选条件调整选择。不同输出目录的记录互不混用。
`--recheck` 强制校验内容；快速恢复依赖文件属性，不会逐字节重新比对，怀疑文件损坏时请使用此选项。
突然断电或强制结束进程可能丢失最近一次检查点后的状态，但已落盘数据仍可在重新校验时复用。

缓存目录可通过 `--cache-dir DIR` 指定，其次读取环境变量 `M_DLP_CACHE_DIR`。
默认遵循 `XDG_CACHE_HOME/m-dlp`；未设置时 macOS 使用 `~/Library/Caches/m-dlp`，
Linux 使用 `~/.cache/m-dlp`，Windows 使用 `%LOCALAPPDATA%/m-dlp`。
`--no-cache` 禁止读写缓存，与 `--resume` 互斥。缓存包含资源元数据、绝对输出路径和恢复状态，
使用本机私有目录保存；缓存写入失败会提示，并继续下载。

### 下载诊断

`--diagnose` 获取元数据后观察网络，默认 10 秒，可用 `--probe-seconds` 调整。
支持 `--select`、筛选条件和 `--resume` 限定诊断文件，与 `--list` 互斥。
它不创建下载目录，不请求文件内容，但可能写入元数据缓存。

诊断展示 tracker 响应/错误、DHT 是否启用及路由节点数、当前连接和做种节点数，
以及每个文件按字节计算的分片覆盖率、已校验的本地字节和暂未观测到的分片数。
下载或元数据获取失败时也会自动输出诊断。

**覆盖率是当前观察结果，不是成功保证。** 在线节点可能断开，暂时缺失的分片也可能随后出现。
只探测时本地文件未被加载校验，不把磁盘上同名文件当作已有分片；下载失败后的诊断则包含本次已校验的数据。
tracker 展示隐藏用户名、密码、路径及查询参数；未配置 tracker 时仍可能通过 DHT 或显式 peer 找到节点。

| 退出码 | 含义 |
| --- | --- |
| `0` | 查看或下载成功，或在选择时输入 `q` 退出 |
| `1` | 网络、元数据超时、传输停滞或文件读写失败 |
| `2` | 参数、配置文件、磁力链接、种子内容或文件选择无效 |
| `130` | `Ctrl+C` 或交互输入结束 |

## 技术选择与行为

- **libtorrent 2.0.x 的 Python 绑定**：负责 v1 / v2 / 混合种子磁力链接、tracker、DHT、peer exchange、元数据交换、文件优先级和分片校验。直接调用库，无需运行额外的下载服务。
- **argparse / tomllib**：标准库参数和 TOML 配置解析，提供校验、互斥选项和命令行覆盖。
- **sqlite3 + 操作系统文件锁**：持久化队列、事务化任务管理和单进程调度，无需额外服务。
- **Rich**：展示文件表格、元数据等待状态和下载进度。
- **[prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit)**：交互文件树、键盘操作、搜索和终端恢复。
- **pytest + Ruff**：测试和代码检查。

也评估了 [aria2](https://aria2.github.io/manual/en/html/aria2c.html)：它支持磁力下载和文件选择，
但此项目只处理 BitTorrent，直接用 libtorrent 更方便控制“先获取元数据 → 选择 → 下载”的生命周期，
也省去外部可执行文件、子进程或 RPC 的管理。

元数据阶段使用临时目录，并设置 `upload_mode`、`default_dont_download`、关闭 torrent 的
`auto_managed`，防止在用户选择前请求文件内容。选择后只给选中文件设置下载优先级，
其他文件优先级为 0。磁力链接自带的 `so=` 选择不会覆盖 CLI 中的选择。
参见 [libtorrent 磁力链接说明](https://libtorrent.org/manual-ref.html#magnet-links)
和 [文件优先级 API](https://libtorrent.org/reference-Torrent_Handle.html)。

需要了解的协议行为：

- 磁力链接通常不包含文件清单，需要从在线节点取得元数据。能列出文件并不代表在线节点拥有全部所需分片；工具不会把它当作可下载完成的保证。
- 未选文件不作为下载目标，但 BT 的一个分片可能跨越多个文件，因此可能传输少量边界数据，并在输出目录留下隐藏的 `.parts` 文件。继续下载时请保留它。
- libtorrent 的 padding 文件和符号链接不列入可选普通文件，因此编号可能不连续。
- 下载过程中会按 BitTorrent 协议向其他节点上传已有分片，下载完成后退出，不继续长期做种。
- 元数据默认等待 120 秒；下载默认连续 300 秒无文件数据传输后退出。可通过命令行调整。

## 开发与验证

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

集成测试在本机创建种子和做种节点，通过磁力链接实际交换元数据并下载文件，
覆盖选择性下载、全部下载、空文件、已有数据校验、取消与重试、超时、v2 / 混合种子、
离线缓存读取、恢复状态失效、筛选、限速、配置覆盖、种子导入导出、批量并发/失败继续/取消、
队列持久化/跨进程暂停恢复/崩溃恢复、交互树选择/搜索/目录全选/真实终端按键与恢复，以及无内容下载的可用性探测。
测试无需外部公开资源；引擎测试禁用 DHT，使用本机 peer 和本地 HTTP tracker。

代码结构：`cli.py` 负责终端交互，`selection.py` 解析文件选择与筛选，`engine.py` 管理下载生命周期，
`cache.py` 存储元数据及恢复状态，`diagnostics.py` 汇总网络观察并计算文件覆盖率，
`config.py` 读取配置，`limits.py` 校验限速，`source.py` 统一磁力链接和本地种子输入，
`batch.py` 校验批量输入、去重并调度临时预览队列，`queue_store.py` 存储持久化任务，
`persistent_queue.py` 负责可管理的下载调度，`queue_cli.py` 提供队列命令。
`picker.py` 管理交互选择状态，`picker_ui.py` 负责终端文件树与快捷键。
