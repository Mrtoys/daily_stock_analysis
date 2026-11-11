# 美股 + 飞书推送配置指南

> 本文件是本 fork 的定制说明。上游仓库的通用文档见 [`docs/INDEX.md`](INDEX.md) 与 [`docs/full-guide.md`](full-guide.md)。

---

## 一、整体链路

本 fork 保留**美股 + A 股**个股场景，并把默认定时从「A 股收盘 18:00」改成「美股盘前 + 晚间新闻」，一天推两次：

| 时段 | 北京时间 | 触发条件 | 做什么 | 飞书收到的消息 |
|------|---------|---------|--------|---------------|
| 新闻 | 19:00（周二~周六） | cron `0 11 * * 2-6`（UTC） | `python scripts/us_news_digest.py` | ① 美股时事晚报 |
| 个股 | 20:00（周一~周五） | cron `0 12 * * 1-5`（UTC） | `python main.py --no-market-review` | ② 美股 + A股 个股分析仪表盘 |

时间为**北京时间**；cron 写的是 UTC（北京时间 = UTC + 8）。

- 新闻 19:00 在美股盘前取当日隔夜要闻，周二~周六覆盖美股周一~周五的盘前时段。
- 个股 20:00 在冬令时是美东 07:00（开盘前 2.5 小时）、夏令时是 08:00（开盘前 1.5 小时）；此时美股用的是「上一交易日完整日线 + 当日盘前情绪」，A 股（如 `510500`）刚收盘 5 小时、当日日线已完整，定位都是**当日决策**。
- **大盘复盘已取消**（原先 07:30 的「美股大盘复盘 + 新闻早报」整条下线），只保留个股与新闻。

> 只用一个 workflow 文件承载两条 cron，通过判断 `github.event.schedule` 的字符串来切换模式，避免复制几百行环境变量。**如果你改动了 cron 时间，必须同步改 `00-daily-analysis.yml` 里 `case "${{ github.event.schedule }}"` 的两个分支**，否则会退化成 `full` 模式（大盘+个股+新闻一起跑）。

---

## 二、创建飞书群机器人

1. 打开飞书，进入你接收推送的群（建议单独建一个「美股复盘」群，消息不打扰别人）。
2. 群右上角 `···` → **设置** → **群机器人** → **添加机器人** → **自定义机器人**。
3. 起个名字（如「美股分析」），点「添加」。
4. 复制生成的 **Webhook 地址**（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx`）。
5. **安全设置**（建议二选一，否则任何拿到地址的人都能往群里发消息）：
   - **签名校验**：复制密钥，稍后填到 `FEISHU_WEBHOOK_SECRET`。推荐，最省心。
   - **自定义关键词**：填一个词（如 `股票`），稍后填到 `FEISHU_WEBHOOK_KEYWORD`。注意关键词校验对**卡片消息同样生效**，本项目已自动把关键词拼到消息正文开头（`FEISHU_WEBHOOK_KEYWORD` 配置后生效）。
6. 点「完成」。

---

## 三、GitHub 配置

路径：仓库 → `Settings` → `Secrets and variables` → `Actions`。

### 3.1 必填 Secrets（`New repository secret`）

| 名称 | 说明 |
|------|------|
| `FEISHU_WEBHOOK_URL` | 上一步复制的飞书 Webhook 地址 |
| `FEISHU_WEBHOOK_SECRET` | 飞书「签名校验」密钥（若你选了签名校验） |
| `FEISHU_WEBHOOK_KEYWORD` | 飞书「自定义关键词」（若你选了关键词校验，可放 Variables） |
| `DEEPSEEK_API_KEY` | LLM Key。只填这一行时系统自动使用 `deepseek/deepseek-v4-flash`。也可改用 `GEMINI_API_KEY` / `AIHUBMIX_KEY` / `MODELFLARE_KEY` / `OPENAI_API_KEY` |
| `TAVILY_API_KEYS` | **新闻推送必需**。搜索源，至少配一个；也可用 `SERPAPI_API_KEYS` / `BOCHA_API_KEYS` / `BRAVE_API_KEYS` / `ANSPIRE_API_KEYS` |

> 飞书官方现在有 lark 国际版，如你的租户在 Lark 而非飞书，自定义机器人的地址仍是 `open.feishu.cn`，本仓库的 Webhook 模式无需改 `FEISHU_DOMAIN`（该变量只影响应用机器人模式）。

### 3.2 必填 Variables（`New repository variable`）

| 名称 | 示例 | 说明 |
|------|------|------|
| `STOCK_LIST` | `QQQ,SPX,AAPL,NVDA,MSFT,TSLA,GOOGL,AMZN,META,SOXL,BABA,PDD,MU,INTC,CRCL,510500` | 逗号分隔，英文逗号。前 15 个是美股，`510500` 是 A 股（南方中证 500ETF）。不配置时默认就是这一串（16 只） |

> **`SPX` 是特例**：它是标普 500 指数，不是股票。仓库内置映射 `SPX → ^GSPC`，行情能正常取到，但指数没有财报/PE/机构持仓这些基本面字段，所以它的个股报告会比真股票"薄"。同理 `QQQ`、`SOXL`、`510500` 是 ETF，也没有个股基本面，但价格与技术面分析正常。
>
> **A 股与美股可以混在同一个 `STOCK_LIST` 里**：解析层按代码形态分流（6 位纯数字 → A 股，字母代码 → 美股），交易日检查也是**逐只按各自市场**判断——A 股休市日只跳过 A 股标的，美股照跑；反之亦然。数据源同理按市场路由：美股走 YFinance/Longbridge，A 股走腾讯/akshare/efinance，YFinance 也支持 `510500.SS` 作为兜底。
>
> ⚠️ **`STOCK_LIST` 若在 Repository variables 里已配置，改默认值不会生效**——必须去 `Settings → Secrets and variables → Actions → Variables` 把那一行也补上 `,510500`。workflow 里 Variables 优先于内置默认值。

### 3.3 可选 Variables

| 名称 | 默认 | 说明 |
|------|------|------|
| `MARKET_REVIEW_REGION` | `us` | 大盘复盘市场。**当前流程已不跑大盘复盘**，此项保留仅为兼容 |
| `MARKET_REVIEW_COLOR_SCHEME` | `green_up` | `green_up`=绿涨红跌（美股惯例）；想用**红涨绿跌**（A 股习惯）就设 `red_up` |
| `REPORT_LANGUAGE` | 中文 | 想输出英文设 `en` |
| `REPORT_TYPE` | `simple` | 报告精简程度 |
| `US_NEWS_LIMIT` | `7` | 新闻要闻条数上限 |
| `US_NEWS_CARD_TITLE` | `美股时事晚报` | 飞书卡片标题 |
| `US_NEWS_QUERIES` | 空 | 自定义新闻搜索主题，多个用英文逗号分隔；留空用内置的 5 组美股主题 |
| `ANALYSIS_TIMEOUT_MINUTES` | `30` | Job 超时分钟数，个股多的时候调大 |
| `TRADING_DAY_CHECK_ENABLED` | `true` | 非交易日跳过。设为 `false` 则天天跑 |
| `MAX_WORKERS` | `1` | 并发数，美股建议保持 1~2，避免 yfinance 限流 |

### 3.4 启用 Actions

`Actions` 标签 → 若提示则点 `I understand my workflows, go ahead and enable them`。

---

## 四、试跑（三步，从快到慢）

`Actions` → **每日股票分析** → `Run workflow`。别一上来就跑 `full`，按下面顺序来，出问题能立刻定位。

### 第 1 步：配置自检（30 秒，先跑这个）

- `mode` = **`preflight`**
- 其余不用管

它只做检查、不做分析，日志末尾会输出一张表：

```
✅ 飞书配置    Webhook (http***abcd)
✅ LLM Key     DeepSeek(DEEPSEEK_API_KEY)
✅ 搜索源      Tavily(TAVILY_API_KEYS)
✅ 自选股      16 只: QQQ,SPX,AAPL,...
✅ 大盘区域    us（美股）
✅ 自选股解析  16 个全部可识别（stock×16）   [关键]
✅ 美股指数    取到 4 个: 标普500指数, 纳斯达克综合指数, ...
✅ 自选股日线  16/16 全部可取
✅ LLM 连通性  2.1s，返回「正常」
✅ 搜索源连通性 1.8s，provider=tavily，3 条
✅ 飞书推送    已发送测试卡片，请到群里确认   [关键]
```

**这一步会真发一条飞书消息**（标题「配置自检」）。收到了就说明链路全通，后面基本不会翻车。没收到就看表里哪个是 ❌。

要点：
- 「自选股解析」是**整批**校验。`main.py` 只要发现一个 token 不被支持，本轮**一只都不分析**——16 只票里打错一个字母，全天白跑。所以这项是 `[关键]`。
- 「自选股日线」会**逐只**探测，能直接告诉你是哪个代码取不到数据（退市、拼错、Yahoo 没收录）。加了 A 股以后**务必看这一项**：`510500` 走的是腾讯/akshare 通道，和美股不是一条链。
- 有 ❌ 且标了 `[关键]` 的必须修（飞书、自选股解析、行情数据）；没标关键的（LLM、搜索源）不阻塞推送，但报告内容会变薄。
- 想跳过飞书那次发送：本地跑时加 `--no-push`。

### 第 2 步：只测新闻

- `mode` = `news-only`
- `force_run` = ✅（跳过交易日检查，周末也能测）

跑完飞书会收到**一条**消息：美股时事晚报。

### 第 3 步：只测个股

- `mode` = `stocks-only`
- `force_run` = ✅

16 只票（15 美股 + 1 A 股），单线程，大概 4~10 分钟。

> 为什么不用 `full` 试？`full` = 大盘 + 个股一次跑完，任何一环出错都混在同一份日志里，不好定位。分三步跑，失败点一目了然。

---

## 五、本地调试

```bash
cp .env.example .env      # 然后填入 FEISHU_WEBHOOK_URL / DEEPSEEK_API_KEY / TAVILY_API_KEYS 等
pip install -r requirements.txt

# 配置自检（推荐第一步；--no-push 则不真发飞书）
python scripts/preflight_check.py --no-push

# 只想快速确认飞书通不通
python scripts/preflight_check.py --quick

# 只生成不推送，看一眼效果
python scripts/us_news_digest.py --dry-run

# 生成并推送
python scripts/us_news_digest.py

# 跳过 LLM，直接推原始新闻清单（验证搜索源是否可用）
python scripts/us_news_digest.py --no-llm

# 大盘复盘（当前定时流程已不跑，仅调试用）
python main.py --market-review --force-run

# 个股（美股 + A 股混排）
STOCK_LIST=AAPL,510500 python main.py --no-market-review --force-run
```

`--dry-run` 生成的新闻稿会落到 `reports/us_news_YYYYMMDD.md`。

---

## 六、美股时事新闻是怎么做的

`scripts/us_news_digest.py` 复用项目已有的能力，不引入新依赖：

1. **搜索**：按 5 组内置主题调用 `SearchService.search_topic_news_bounded()`，复用你已配置的任意搜索源；按 URL/标题去重。
   - 内置主题：美股隔夜要闻 / 美联储与流动性 / 科技与 AI 主线 / 美国经济与政策 / 全球市场与大宗
2. **归纳**：把素材交给 `GeminiAnalyzer.generate_text()`，输出「一句话核心矛盾 + 5~7 条要闻（要点+影响+来源）+ 今日关注」。Prompt 里明确禁止编造素材外的数字和政策。
3. **推送**：`FeishuSender.send_to_feishu()`，卡片标题为「美股时事晚报」（可用 `US_NEWS_CARD_TITLE` 改）。

**降级策略**（任何一步失败都不会静默）：

| 情况 | 行为 | 退出码 |
|------|------|--------|
| 没配任何搜索源 | 打错误日志，不推送 | 2 |
| 所有主题都没搜到新闻 | 打错误日志，不推送 | 2 |
| LLM 失败/未配置 | 降级为「按主题分组的原始新闻清单」照常推送 | 0 |
| 飞书推送失败 | 打错误日志 | 1 |

新闻失败**不影响**个股分析的推送（workflow 里做了隔离，且 19:00 与 20:00 是两次独立运行）。

---

## 七、常见问题

**Q：Actions 跑成功了，飞书没收到**
先跑一次 `mode=preflight`，它会直接把原因打出来。常见三种：
1. 「飞书配置 ❌ 未配置」→ `FEISHU_WEBHOOK_URL` 没加到 Secrets，或加成了 Variables。
2. 「飞书推送 ❌ 发送失败」+ 日志里有 `code=19021` / `19024` → 签名或关键词校验没过，回头核对第二节第 5 步。
3. 收到了测试卡片但定时任务没消息 → 大概率是撞上了非交易日：`TRADING_DAY_CHECK_ENABLED=true` 时，A 股假期只跳过 A 股标的、美股假期只跳过美股标的，两边都休市才会整轮跳过；想天天跑就设为 `false`。

**Q：STOCK_LIST 里有一只看错了代码会怎样**
**整个批次一只都不分析**，日志会打 `STOCK_LIST 包含不支持的目标 'xxx'；本轮不执行任何分析。`。这是 `main.py` 的刻意设计（避免静默跳过）。所以改完清单先跑一次 `mode=preflight`，它的「自选股解析」会把不支持的 token 逐个列出来。

**Q：SPX / QQQ / SOXL / 510500 的报告比 AAPL 薄很多**
正常。`SPX` 是指数（映射到 `^GSPC`），`QQQ`、`SOXL`、`510500` 是 ETF，这几类都没有财报、PE、机构持仓等基本面数据，只有价格与技术面。个股分析框架本来就为"公司"设计，用在指数/ETF 上会缺一块。想要完整基本面就换成真股票。

**Q：16 只票会不会太慢 / 太贵**
慢主要是行情拉取，不是 LLM——每只票一次分析调用，16 只约 16 次 LLM 调用。单线程下大约 4~10 分钟。想省钱省时间：
- 用 `REPORT_TYPE=simple`（默认已是）
- 把 `STOCK_LIST` 砍到 8 只以内
- `MAX_WORKERS` 保持 1~2，调高容易被 Yahoo 限流，反而更慢
- Job 超时已通过 `ANALYSIS_TIMEOUT_MINUTES=60` 放宽，正常不会被打断

**Q：19:00 的新闻和 20:00 的个股，周末会不会被拦掉？**
两次都是**独立运行**，且各自只跑自己那部分：
- 新闻 19:00 走的是北京时间**周二~周六**（= 美股周一~周五的盘前），周六那次对应美股周五盘前，正常推送；周日、周一不推。
- 个股 20:00 走北京时间**周一~周五**，且受逐只交易日检查约束，美股或 A 股假期会各自过滤。

**Q：新闻推送退出码 2**
没配搜索源。新闻**只依赖搜索 API**，LLM 不是必需的。至少配 `TAVILY_API_KEYS`（每月 1000 次免费）或 `BOCHA_API_KEYS` / `SERPAPI_API_KEYS`。

**Q：个股数据有些字段是空的**
美股走 YFinance 免费源，量比、换手率、PE 等字段可能缺失，属正常降级。想要更完整可配 `LONGBRIDGE_OAUTH_CLIENT_ID` + `LONGBRIDGE_OAUTH_TOKEN_CACHE_B64`。A 股走腾讯/akshare，字段通常比美股更全。

**Q：想改推送时间**
改 `.github/workflows/00-daily-analysis.yml` 两处：
1. `on.schedule` 里的 cron（UTC）
2. `run:` 中 `case "${{ github.event.schedule }}"` 的分支字符串，必须与 cron **完全一致**

常见对照：北京 19:00（周二~周六）= `0 11 * * 2-6`；北京 20:00（周一~周五）= `0 12 * * 1-5`；北京 21:00（周一~周五）= `0 13 * * 1-5`。若时间早于 08:00，UTC 会落到前一天，星期几要相应减一（例如北京周二 07:30 → UTC 周一 23:30 → `30 23 * * 1-5`）。

**Q：消息太长被截断**
飞书单条上限约 20KB，`FeishuSender` 会自动分批发送（带 `(1/N)` 分页标记），无需处理。

---

## 八、免责声明

本项目仅供学习研究，不构成投资建议。LLM 生成的结论可能有误，务必自行复核。
