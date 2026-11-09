# 美股 + 飞书推送配置指南

> 本文件是本 fork 的定制说明。上游仓库的通用文档见 [`docs/INDEX.md`](INDEX.md) 与 [`docs/full-guide.md`](full-guide.md)。

---

## 一、整体链路

本 fork 只保留**美股**场景，并把默认定时从「A 股收盘 18:00」改成「美股作息」，一天推两次：

| 时段 | 北京时间 | 触发条件 | 做什么 | 飞书收到的消息 |
|------|---------|---------|--------|---------------|
| 早报 | 07:30（周二~周六） | cron `30 23 * * 1-5`（UTC） | `python main.py --market-review` + `python scripts/us_news_digest.py` | ① 美股大盘复盘 ② 美股时事新闻早报 |
| 晚报 | 20:30（周一~周五） | cron `30 12 * * 1-5`（UTC） | `python main.py --no-market-review` | ③ 美股个股分析仪表盘 |

时间为**北京时间**；cron 写的是 UTC（北京时间 = UTC + 8）。

- 早报跑在美股收盘后 2~3 小时，指数与新闻都已定型，是最有价值的时段。
- 晚报 20:30 在冬令时是美东 07:30（开盘前 2 小时）、夏令时是 08:30（开盘前 1 小时）；此时分析用的是「上一交易日完整日线 + 当日盘前情绪」，定位是**盘前决策**。

> 只用一个 workflow 文件承载两条 cron，通过判断 `github.event.schedule` 的字符串来切换模式，避免复制几百行环境变量。**如果你改动了 cron 时间，必须同步改 `00-daily-analysis.yml` 里 `case "${{ github.event.schedule }}"` 的两个分支**，否则会退化成 `full` 模式（大盘+个股同时跑）。

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
| `ANSPIRE_API_KEYS` | **新闻早报必需**。搜索源，至少配一个；也可用 `SERPAPI_API_KEYS` / `TAVILY_API_KEYS` / `BOCHA_API_KEYS` / `BRAVE_API_KEYS` |

> 飞书官方现在有 lark 国际版，如你的租户在 Lark 而非飞书，自定义机器人的地址仍是 `open.feishu.cn`，本仓库的 Webhook 模式无需改 `FEISHU_DOMAIN`（该变量只影响应用机器人模式）。

### 3.2 必填 Variables（`New repository variable`）

| 名称 | 示例 | 说明 |
|------|------|------|
| `STOCK_LIST` | `QQQ,SPX,AAPL,NVDA,MSFT,TSLA,GOOGL,AMZN,META,SOXL,BABA,PDD,MU,INTC,CRCL` | 逗号分隔，英文逗号。不配置时默认就是这一串（15 只） |

> **`SPX` 是特例**：它是标普 500 指数，不是股票。仓库内置映射 `SPX → ^GSPC`，行情能正常取到，但指数没有财报/PE/机构持仓这些基本面字段，所以它的个股报告会比真股票"薄"。如果你觉得这份报告价值不大，把它从 `STOCK_LIST` 里去掉即可——早报的大盘复盘本来就包含标普 500。同理 `QQQ`、`SOXL` 是 ETF，也没有个股基本面，但价格与技术面分析正常。

### 3.3 可选 Variables

| 名称 | 默认 | 说明 |
|------|------|------|
| `MARKET_REVIEW_REGION` | `us` | 大盘复盘市场。已默认改成 `us` |
| `MARKET_REVIEW_COLOR_SCHEME` | `green_up` | `green_up`=绿涨红跌（美股惯例）；想用**红涨绿跌**（A 股习惯）就设 `red_up` |
| `REPORT_LANGUAGE` | 中文 | 想输出英文设 `en` |
| `REPORT_TYPE` | `simple` | 报告精简程度 |
| `US_NEWS_LIMIT` | `7` | 新闻早报要闻条数上限 |
| `US_NEWS_CARD_TITLE` | `美股时事新闻早报` | 飞书卡片标题 |
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
✅ 搜索源      Anspire(ANSPIRE_API_KEYS)
✅ 自选股      15 只: QQQ,SPX,AAPL,...
✅ 大盘区域    us（美股）
✅ 自选股解析  15 个全部可识别（stock×15）   [关键]
✅ 美股指数    取到 4 个: 标普500指数, 纳斯达克综合指数, ...
✅ 自选股日线  15/15 全部可取
✅ LLM 连通性  2.1s，返回「正常」
✅ 搜索源连通性 1.8s，provider=anspire，3 条
✅ 飞书推送    已发送测试卡片，请到群里确认   [关键]
```

**这一步会真发一条飞书消息**（标题「配置自检」）。收到了就说明链路全通，后面基本不会翻车。没收到就看表里哪个是 ❌。

要点：
- 「自选股解析」是**整批**校验。`main.py` 只要发现一个 token 不被支持，本轮**一只都不分析**——15 只票里打错一个字母，全天白跑。所以这项是 `[关键]`。
- 「自选股日线」会**逐只**探测，能直接告诉你是哪个代码取不到数据（退市、拼错、Yahoo 没收录）。
- 有 ❌ 且标了 `[关键]` 的必须修（飞书、自选股解析、行情数据）；没标关键的（LLM、搜索源）不阻塞推送，但报告内容会变薄。
- 想跳过飞书那次发送：本地跑时加 `--no-push`。

### 第 2 步：只测大盘 + 新闻

- `mode` = `market-only`
- `with_news` = ✅
- `force_run` = ✅（跳过交易日检查，周末也能测）

跑完飞书会收到**两条**消息：美股大盘复盘、美股时事新闻早报。

### 第 3 步：只测个股

- `mode` = `stocks-only`
- `force_run` = ✅

15 只票，YFinance 免费源 + 单线程，大概 3~8 分钟。

> 为什么不用 `full` 试？`full` = 大盘 + 个股一次跑完，任何一环出错都混在同一份日志里，不好定位。分三步跑，失败点一目了然。

---

## 五、本地调试

```bash
cp .env.example .env      # 然后填入 FEISHU_WEBHOOK_URL / DEEPSEEK_API_KEY / ANSPIRE_API_KEYS 等
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

# 大盘复盘
python main.py --market-review --force-run

# 个股
STOCK_LIST=AAPL,NVDA python main.py --no-market-review --force-run
```

`--dry-run` 生成的早报会落到 `reports/us_news_YYYYMMDD.md`。

---

## 六、新闻早报是怎么做的

`scripts/us_news_digest.py` 复用项目已有的能力，不引入新依赖：

1. **搜索**：按 5 组内置主题调用 `SearchService.search_topic_news_bounded()`，复用你已配置的任意搜索源；按 URL/标题去重。
   - 内置主题：美股隔夜要闻 / 美联储与流动性 / 科技与 AI 主线 / 美国经济与政策 / 全球市场与大宗
2. **归纳**：把素材交给 `GeminiAnalyzer.generate_text()`，输出「一句话核心矛盾 + 5~7 条要闻（要点+影响+来源）+ 今日关注」。Prompt 里明确禁止编造素材外的数字和政策。
3. **推送**：`FeishuSender.send_to_feishu()`，卡片标题为「美股时事新闻早报」。

**降级策略**（任何一步失败都不会静默）：

| 情况 | 行为 | 退出码 |
|------|------|--------|
| 没配任何搜索源 | 打错误日志，不推送 | 2 |
| 所有主题都没搜到新闻 | 打错误日志，不推送 | 2 |
| LLM 失败/未配置 | 降级为「按主题分组的原始新闻清单」照常推送 | 0 |
| 飞书推送失败 | 打错误日志 | 1 |

早报失败**不影响**大盘复盘与个股分析的推送（workflow 里做了隔离）。

---

## 七、常见问题

**Q：Actions 跑成功了，飞书没收到**
先跑一次 `mode=preflight`，它会直接把原因打出来。常见三种：
1. 「飞书配置 ❌ 未配置」→ `FEISHU_WEBHOOK_URL` 没加到 Secrets，或加成了 Variables。
2. 「飞书推送 ❌ 发送失败」+ 日志里有 `code=19021` / `19024` → 签名或关键词校验没过，回头核对第二节第 5 步。
3. 收到了测试卡片但定时任务没消息 → 看 `MARKET_REVIEW_REGION` 是不是被改回 `cn`，或 `TRADING_DAY_CHECK_ENABLED=true` 撞上了美股假期。

**Q：STOCK_LIST 里有一只看错了代码会怎样**
**整个批次一只都不分析**，日志会打 `STOCK_LIST 包含不支持的目标 'xxx'；本轮不执行任何分析。`。这是 `main.py` 的刻意设计（避免静默跳过）。所以改完清单先跑一次 `mode=preflight`，它的「自选股解析」会把不支持的 token 逐个列出来。

**Q：SPX / QQQ / SOXL 的报告比 AAPL 薄很多**
正常。`SPX` 是指数（映射到 `^GSPC`），`QQQ`、`SOXL` 是 ETF，这三类都没有财报、PE、机构持仓等基本面数据，只有价格与技术面。个股分析框架本来就为"公司"设计，用在指数/ETF 上会缺一块。想要完整基本面就换成真股票。

**Q：15 只票会不会太慢 / 太贵**
慢主要是行情拉取，不是 LLM——每只票一次分析调用，15 只约 15 次 LLM 调用。YFinance 免费源单线程下大约 3~8 分钟。想省钱省时间：
- 用 `REPORT_TYPE=simple`（默认已是）
- 把 `STOCK_LIST` 砍到 8 只以内
- `MAX_WORKERS` 保持 1~2，调高容易被 Yahoo 限流，反而更慢

**Q：早报在北京时间周六早上跑，会不会被「非交易日」拦掉？**
不会。交易日检查按**各市场本地时区**判断，北京时间周六 07:30 对应美东周五傍晚，判定为美股交易日，正常执行。

**Q：新闻早报退出码 2**
没配搜索源。新闻早报**只依赖搜索 API**，LLM 不是必需的。至少配 `ANSPIRE_API_KEYS`（新用户有免费额度）或 `TAVILY_API_KEYS`（每月 1000 次免费）。

**Q：个股数据有些字段是空的**
美股走 YFinance 免费源，量比、换手率、PE 等字段可能缺失，属正常降级。想要更完整可配 `LONGBRIDGE_OAUTH_CLIENT_ID` + `LONGBRIDGE_OAUTH_TOKEN_CACHE_B64`。

**Q：想改推送时间**
改 `.github/workflows/00-daily-analysis.yml` 两处：
1. `on.schedule` 里的 cron（UTC）
2. `run:` 中 `case "${{ github.event.schedule }}"` 的分支字符串，必须与 cron **完全一致**

常见对照：北京 06:30 = `30 22 * * 1-5`；北京 08:00 = `0 0 * * 2-6`；北京 21:00 = `0 13 * * 1-5`。注意跨日会导致星期几偏移。

**Q：消息太长被截断**
飞书单条上限约 20KB，`FeishuSender` 会自动分批发送（带 `(1/N)` 分页标记），无需处理。

---

## 八、免责声明

本项目仅供学习研究，不构成投资建议。LLM 生成的结论可能有误，务必自行复核。
