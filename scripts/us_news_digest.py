#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===================================
美股时事新闻早报 → 飞书推送
===================================

职责：
1. 复用项目已配置的搜索源（Anspire / SerpAPI / Tavily / Bocha / Brave / MiniMax / SearXNG）
   抓取隔夜美股与宏观相关的新闻素材
2. 复用项目已配置的 LLM 把素材归纳成条理清晰的「美股时事新闻早报」
3. 复用 FeishuSender 推送到飞书（Webhook 或应用机器人）

与 `python main.py --market-review` 的区别：
- 大盘复盘关注指数与技术面，输出的是"交易计划"
- 本脚本只关注"发生了什么"，输出的是"新闻要点 + 影响判断"
两者互补，可以各自独立推送。

用法：
    python scripts/us_news_digest.py                 # 抓取 + LLM 归纳 + 推送飞书
    python scripts/us_news_digest.py --dry-run       # 只打印并落盘，不推送
    python scripts/us_news_digest.py --no-llm        # 跳过 LLM，直接整理成新闻清单
    python scripts/us_news_digest.py --limit 8       # 控制要闻条数上限
    python scripts/us_news_digest.py --save          # 额外把早报写入 reports/

环境变量（可选）：
    US_NEWS_QUERIES   自定义搜索主题，多个用英文逗号分隔；留空使用内置美股主题
    US_NEWS_LIMIT     要闻条数上限，默认 7
    US_NEWS_CARD_TITLE 飞书卡片标题，默认「美股时事新闻早报」

退出码：
    0 成功
    1 推送失败
    2 没有可用的新闻素材（搜索源未配置或全部失败）
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import get_config, setup_env  # noqa: E402

setup_env()

logger = logging.getLogger("us_news_digest")

REPORT_TZ = ZoneInfo("Asia/Shanghai")

# 内置搜索主题：(标签, 实际搜索 query)
# query 会作为 focus_keywords 传入，避免 SearchService 默认拼上「A股」语境。
DEFAULT_TOPICS: Tuple[Tuple[str, str], ...] = (
    ("美股隔夜要闻", "美股 隔夜 收盘 标普500 纳斯达克 道琼斯 三大指数"),
    ("美联储与流动性", "美联储 利率 降息 通胀 美债收益率 美股 影响"),
    ("科技与AI主线", "美股 科技股 AI 英伟达 微软 苹果 特斯拉 财报 指引"),
    ("美国经济与政策", "美国 经济数据 CPI 非农 GDP 关税 财政 政策 股市"),
    ("全球市场与大宗", "全球市场 原油 美元指数 黄金 地缘政治 隔夜 美股"),
)

MAX_MATERIAL_CHARS = 200
MAX_SNIPPET_CHARS = 220


def _setup_logging(debug: bool = False) -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    if debug:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="美股时事新闻早报生成与飞书推送",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dry-run", action="store_true", help="只生成并打印早报，不推送")
    parser.add_argument("--no-llm", action="store_true", help="跳过 LLM 归纳，直接整理新闻清单")
    parser.add_argument("--save", action="store_true", help="把早报写入 reports/ 目录")
    parser.add_argument("--limit", type=int, default=None, help="要闻条数上限（默认取 US_NEWS_LIMIT 或 7）")
    parser.add_argument("--max-material", type=int, default=40, help="送入 LLM 的新闻素材条数上限（默认 40）")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------


def load_topics() -> List[Tuple[str, str]]:
    """加载搜索主题，支持 US_NEWS_QUERIES 覆盖。"""
    raw = (os.getenv("US_NEWS_QUERIES") or "").strip()
    if not raw:
        return list(DEFAULT_TOPICS)

    queries = [item.strip() for item in re.split(r"[,;\n]", raw) if item.strip()]
    if not queries:
        return list(DEFAULT_TOPICS)

    topics: List[Tuple[str, str]] = []
    for idx, query in enumerate(queries, 1):
        label = query if len(query) <= 18 else f"主题{idx}"
        topics.append((label, query))
    return topics


def build_search_service(config: Any) -> Any:
    """按 pipeline 的同一套参数构造 SearchService。"""
    from src.search_service import SearchService

    return SearchService(
        bocha_keys=getattr(config, "bocha_api_keys", None),
        tavily_keys=getattr(config, "tavily_api_keys", None),
        anspire_keys=getattr(config, "anspire_api_keys", None),
        brave_keys=getattr(config, "brave_api_keys", None),
        serpapi_keys=getattr(config, "serpapi_keys", None),
        minimax_keys=getattr(config, "minimax_api_keys", None),
        searxng_base_urls=getattr(config, "searxng_base_urls", None),
        searxng_public_instances_enabled=getattr(config, "searxng_public_instances_enabled", False),
        searxng_timeout_seconds=getattr(config, "searxng_timeout_seconds", None),
        news_max_age_days=getattr(config, "news_max_age_days", 3),
        news_strategy_profile=getattr(config, "news_strategy_profile", "short"),
    )


def _clean(text: Any, limit: int) -> str:
    if not text:
        return ""
    value = re.sub(r"\s+", " ", str(text)).strip()
    if len(value) > limit:
        value = value[:limit].rstrip() + "…"
    return value


def _dedupe_key(title: str, url: str) -> str:
    if url:
        return "url:" + url.split("?")[0].strip().lower()
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", (title or "").lower())
    return "title:" + normalized[:60]


def collect_news(service: Any, topics: List[Tuple[str, str]], per_topic: int = 6) -> List[Dict[str, Any]]:
    """按主题抓取新闻并去重。"""
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    failures = 0

    for label, query in topics:
        try:
            response = service.search_topic_news_bounded(
                topic=label,
                max_results=per_topic,
                focus_keywords=[query],
                timeout_seconds=25.0,
            )
        except Exception as exc:  # noqa: BLE001 - 单个主题失败不应中断整批
            failures += 1
            logger.warning("主题「%s」搜索异常: %s: %s", label, type(exc).__name__, exc)
            continue

        results = getattr(response, "results", None) or []
        if not results:
            failures += 1
            logger.warning(
                "主题「%s」无结果 (provider=%s, error=%s)",
                label,
                getattr(response, "provider", "?"),
                getattr(response, "error_message", None),
            )
            continue

        kept = 0
        for result in results:
            title = _clean(getattr(result, "title", ""), 160)
            url = str(getattr(result, "url", "") or "").strip()
            if not title:
                continue
            key = _dedupe_key(title, url)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "topic": label,
                    "title": title,
                    "snippet": _clean(getattr(result, "snippet", ""), MAX_SNIPPET_CHARS),
                    "source": _clean(getattr(result, "source", ""), 40),
                    "published_date": _clean(getattr(result, "published_date", ""), 30),
                    "url": url,
                }
            )
            kept += 1

        logger.info("主题「%s」收录 %d 条", label, kept)

    logger.info("合计收录 %d 条原始新闻，失败主题 %d 个", len(items), failures)
    return items


# ---------------------------------------------------------------------------
# LLM 归纳
# ---------------------------------------------------------------------------


def _build_material(items: Sequence[Dict[str, Any]], limit: int) -> str:
    lines: List[str] = []
    for idx, item in enumerate(items[:limit], 1):
        meta = " / ".join(part for part in (item["source"], item["published_date"]) if part)
        meta_suffix = f" ({meta})" if meta else ""
        snippet = item["snippet"] or "-"
        url_line = f"\n   链接: {item['url']}" if item["url"] else ""
        lines.append(
            f"{idx}. [{item['topic']}] {item['title']}{meta_suffix}\n   {snippet}{url_line}"
        )
    return "\n".join(lines)


def build_prompt(material: str, report_date: str, limit: int, language: str = "zh") -> str:
    if language == "en":
        return (
            f"You are a US market news editor. Below is raw news material collected on {report_date} "
            "morning Beijing time. Build a concise 'US Market Overnight Brief'.\n\n"
            "Rules:\n"
            "1. Start with one sentence (<=40 words) capturing the core tension of the session.\n"
            f"2. Then output 5-{limit} headlines ordered by importance. Format each as:\n"
            "   **Sub-headline (<=8 words)**\n"
            "   What happened + why it matters + directional read-through (<=50 words).\n"
            "   Source: <source name>\n"
            "3. End with a 'Watch Today' section of 1-3 items (events/data/earnings); write 'No major catalyst' if none.\n"
            "4. Use ONLY facts present in the material. Never invent numbers, companies or policies.\n"
            "5. Drop material unrelated to US equities.\n"
            "6. Output Markdown only. No JSON, no preamble, no explanation of your process.\n\n"
            f"=== MATERIAL ===\n{material}\n=== END ===\n"
        )

    return (
        f"你是美股市场新闻编辑。下面是 {report_date} 早上抓取的原始新闻素材，可能包含噪音。\n"
        "请产出「美股时事新闻早报」，读者是关注美股的中国投资者。\n\n"
        "要求：\n"
        "1. 先用一句话（不超过 60 字）概括隔夜美股与宏观的核心矛盾。\n"
        f"2. 再输出 5-{limit} 条要闻，按重要性排序。每条格式严格如下：\n"
        "   **小标题（不超过 14 字）**\n"
        "   发生了什么 + 为什么重要 + 对美股或板块的方向性影响，合计不超过 90 字。\n"
        "   来源：<来源名>\n"
        "3. 最后给「今日关注」1-3 条（事件 / 数据 / 财报），没有就写“无明确日程”。\n"
        "4. 只使用素材中的事实。素材没提到的数字、公司、政策一律不要编造。\n"
        "5. 与美股无关的素材（如纯 A 股公告、国内民生新闻）直接丢弃。\n"
        "6. 全部用简体中文。不要输出 JSON，不要解释你的分析过程，不要加“以下是”之类的开场白。\n\n"
        f"=== 素材开始 ===\n{material}\n=== 素材结束 ===\n"
    )


def summarize_with_llm(config: Any, prompt: str) -> Optional[str]:
    from src.analyzer import GeminiAnalyzer

    analyzer = GeminiAnalyzer(config=config)
    text = analyzer.generate_text(prompt, max_tokens=2600, temperature=0.4)
    if not text or not text.strip():
        return None
    return text.strip()


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def build_header(report_date: str) -> str:
    return f"# 📰 美股时事新闻早报\n\n**{report_date}**  ·  北京时间"


def render_fallback(items: Sequence[Dict[str, Any]], limit: int) -> str:
    """LLM 不可用时的降级输出：按主题列原始新闻。"""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in items[: limit * 4]:
        grouped.setdefault(item["topic"], []).append(item)

    lines: List[str] = [
        "> ⚠️ 未启用 LLM 归纳，以下为原始新闻清单，按主题归类。",
        "",
    ]
    count = 0
    for topic, entries in grouped.items():
        if count >= limit * 2:
            break
        lines.append(f"### {topic}")
        for entry in entries:
            if count >= limit * 2:
                break
            meta = " / ".join(part for part in (entry["source"], entry["published_date"]) if part)
            title = entry["title"]
            if entry["url"]:
                title = f"[{title}]({entry['url']})"
            lines.append(f"- **{title}**" + (f"（{meta}）" if meta else ""))
            if entry["snippet"]:
                lines.append(f"  {entry['snippet']}")
            count += 1
        lines.append("")
    return "\n".join(lines).strip()


def compose_report(body: str, report_date: str) -> str:
    return f"{build_header(report_date)}\n\n{body}".strip()


def save_report(content: str, report_date: str) -> Optional[Path]:
    try:
        reports_dir = ROOT_DIR / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = report_date.replace("-", "").replace(" ", "_").replace(":", "")
        path = reports_dir / f"us_news_{stamp}.md"
        path.write_text(content, encoding="utf-8")
        logger.info("早报已保存: %s", path)
        return path
    except OSError as exc:
        logger.warning("早报落盘失败: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 推送
# ---------------------------------------------------------------------------


def push_to_feishu(config: Any, content: str, title: str) -> bool:
    from src.notification_sender.feishu_sender import FeishuSender

    sender = FeishuSender(config)
    try:
        return bool(sender.send_to_feishu(content, title=title))
    except TypeError:
        # 兼容尚未支持 title 参数的旧版本 sender
        return bool(sender.send_to_feishu(content))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.debug)

    now = datetime.now(REPORT_TZ)
    report_date = now.strftime("%Y-%m-%d")
    limit = args.limit if args.limit and args.limit > 0 else int(os.getenv("US_NEWS_LIMIT", "7") or 7)
    card_title = (os.getenv("US_NEWS_CARD_TITLE") or "美股时事新闻早报").strip()

    config = get_config()
    topics = load_topics()
    logger.info("开始抓取美股新闻，主题 %d 个，要闻上限 %d 条", len(topics), limit)

    try:
        service = build_search_service(config)
    except Exception as exc:  # noqa: BLE001
        logger.error("搜索服务初始化失败: %s: %s", type(exc).__name__, exc)
        return 2

    if not getattr(service, "is_available", False):
        logger.error(
            "未配置任何搜索源，无法抓取新闻。请至少配置一个："
            "ANSPIRE_API_KEYS / SERPAPI_API_KEYS / TAVILY_API_KEYS / BOCHA_API_KEYS / BRAVE_API_KEYS"
        )
        return 2

    items = collect_news(service, topics)
    if not items:
        logger.error("所有主题都未抓取到有效新闻，放弃推送")
        return 2

    body: Optional[str] = None
    if not args.no_llm:
        material = _build_material(items, args.max_material)
        prompt = build_prompt(material, report_date, limit)
        logger.info("调用 LLM 归纳，素材 %d 条 / %d 字符", min(len(items), args.max_material), len(material))
        try:
            body = summarize_with_llm(config, prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 归纳失败: %s: %s", type(exc).__name__, exc)
            body = None
        if not body:
            logger.warning("LLM 未返回内容，降级为原始新闻清单")

    if not body:
        body = render_fallback(items, limit)

    content = compose_report(body, report_date)

    if args.save or args.dry_run:
        save_report(content, report_date)

    if args.dry_run:
        print("\n" + "=" * 60)
        print(content)
        print("=" * 60 + "\n")
        logger.info("dry-run 模式，未推送")
        return 0

    ok = push_to_feishu(config, content, card_title)
    if ok:
        logger.info("飞书推送成功")
        return 0

    logger.error("飞书推送失败，请检查 FEISHU_WEBHOOK_URL 或 FEISHU_APP_* 配置")
    return 1


if __name__ == "__main__":
    sys.exit(main())
