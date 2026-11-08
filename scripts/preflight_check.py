#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===================================
配置自检（Preflight Check）
===================================

用途：在正式跑分析之前，用 30 秒确认「配置到底通不通」。
比直接跑 `main.py` 快得多，因为不会触发完整的 LLM 复盘。

检查项：
    1. 环境变量  —— 飞书 / LLM / 搜索源 / 自选股 是否配齐
    2. 行情数据  —— Yahoo Finance 能否取到自选股日线与美股主要指数
                    （可提前发现写错的代码，如把 ^GSPC 写成 SPX500）
    3. LLM       —— 发一句极短的 prompt，确认 Key 有效、模型可调
    4. 搜索源    —— 跑一次最小的新闻搜索，确认能返回结果
    5. 飞书推送  —— 真发一张卡片到群里（这是唯一能验证端到端的方式）

用法：
    python scripts/preflight_check.py              # 全量自检（会真发一条飞书消息）
    python scripts/preflight_check.py --no-push    # 不发飞书，只检查配置
    python scripts/preflight_check.py --quick      # 跳过 LLM / 搜索 / 行情，只查配置和飞书
    python scripts/preflight_check.py --json       # 输出 JSON，便于程序消费

退出码：
    0 关键项全部通过（飞书 + 行情）
    1 有关键项失败
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import get_config, setup_env  # noqa: E402

setup_env()

logger = logging.getLogger("preflight")

REPORT_TZ = ZoneInfo("Asia/Shanghai")

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"

_STATUS_ICON = {OK: "✅", WARN: "⚠️ ", FAIL: "❌", SKIP: "⏭️ "}

# 各类 Key 的环境变量名（任一存在即视为该能力可用）
LLM_KEY_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("DeepSeek", ("DEEPSEEK_API_KEY",)),
    ("Gemini", ("GEMINI_API_KEY",)),
    ("AIHubMix", ("AIHUBMIX_KEY",)),
    ("Modelflare", ("MODELFLARE_KEY", "LLM_MODELFLARE_API_KEY", "LLM_MODELFLARE_API_KEYS")),
    ("OpenAI", ("OPENAI_API_KEY",)),
    ("Anthropic", ("ANTHROPIC_API_KEY",)),
    ("LiteLLM", ("LITELLM_API_KEY", "LITELLM_MODEL", "LITELLM_CONFIG", "LLM_CHANNELS")),
)

SEARCH_KEY_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Anspire", ("ANSPIRE_API_KEYS",)),
    ("SerpAPI", ("SERPAPI_API_KEYS",)),
    ("Tavily", ("TAVILY_API_KEYS",)),
    ("Bocha", ("BOCHA_API_KEYS",)),
    ("Brave", ("BRAVE_API_KEYS",)),
    ("MiniMax", ("MINIMAX_API_KEYS",)),
    ("SearXNG", ("SEARXNG_BASE_URLS",)),
)


class Check:
    """一条检查结果。"""

    def __init__(self, name: str, status: str, detail: str = "", critical: bool = False):
        self.name = name
        self.status = status
        self.detail = detail
        self.critical = critical

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "critical": self.critical,
        }


def _mask(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "***"
    return f"{value[:4]}***{value[-4:]}"


def _first_env(names: Sequence[str]) -> Tuple[Optional[str], Optional[str]]:
    """返回第一个非空环境变量的 (名称, 值)。"""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return name, value
    return None, None


# ---------------------------------------------------------------------------
# 1. 环境变量
# ---------------------------------------------------------------------------


def check_env(config: Any) -> List[Check]:
    results: List[Check] = []

    # 飞书
    webhook = (os.getenv("FEISHU_WEBHOOK_URL") or "").strip()
    app_id = (os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = (os.getenv("FEISHU_APP_SECRET") or "").strip()
    chat_id = (os.getenv("FEISHU_CHAT_ID") or "").strip()

    if webhook:
        mode = "Webhook"
        if (os.getenv("FEISHU_WEBHOOK_SECRET") or "").strip():
            mode += " + 签名校验"
        elif (os.getenv("FEISHU_WEBHOOK_KEYWORD") or "").strip():
            mode += " + 关键词校验"
        results.append(Check("飞书配置", OK, f"{mode} ({_mask(webhook)})", critical=True))
    elif app_id and app_secret and chat_id:
        results.append(Check("飞书配置", OK, f"应用机器人 (app_id={_mask(app_id)}, chat_id={_mask(chat_id)})", critical=True))
    elif app_id or app_secret or chat_id:
        missing = [n for n, v in (("FEISHU_APP_ID", app_id), ("FEISHU_APP_SECRET", app_secret), ("FEISHU_CHAT_ID", chat_id)) if not v]
        results.append(Check("飞书配置", FAIL, f"应用机器人参数不全，缺 {', '.join(missing)}", critical=True))
    else:
        results.append(
            Check(
                "飞书配置",
                FAIL,
                "读取不到 FEISHU_WEBHOOK_URL：请到 Settings → Secrets and variables → Actions，"
                "在 Secrets（或 Variables）标签下新增该名称，注意必须点进本仓库 Mrtoys/daily_stock_analysis 的 Settings",
                critical=True,
            )
        )

    # LLM
    available_llm = []
    for label, names in LLM_KEY_GROUPS:
        name, value = _first_env(names)
        if name:
            available_llm.append(f"{label}({name})")
    # Anspire 的 Key 可同时充当搜索源和 OpenAI 兼容大模型通道
    # （ANSPIRE_LLM_ENABLED 默认 true），因此只配它时也应判为 LLM 可用。
    if not available_llm:
        anspire_env, anspire_key = _first_env(("ANSPIRE_API_KEYS",))
        anspire_enabled = (os.getenv("ANSPIRE_LLM_ENABLED") or "true").strip().lower()
        if anspire_env and anspire_enabled not in ("false", "0", "no", "off"):
            available_llm.append(f"Anspire 共用({anspire_env} {_mask(anspire_key)})")
    if available_llm:
        results.append(Check("LLM Key", OK, " / ".join(available_llm)))
    else:
        results.append(
            Check(
                "LLM Key",
                FAIL,
                "未配置任何 LLM Key，分析无法生成结论；可配 DEEPSEEK_API_KEY / GEMINI_API_KEY / AIHUBMIX_KEY / MODELFLARE_KEY，或直接配 ANSPIRE_API_KEYS",
                critical=False,
            )
        )

    # 搜索源
    available_search = []
    for label, names in SEARCH_KEY_GROUPS:
        name, value = _first_env(names)
        if name:
            available_search.append(f"{label}({name})")
    if available_search:
        results.append(Check("搜索源", OK, " / ".join(available_search)))
    else:
        results.append(Check("搜索源", WARN, "未配置搜索源：新闻早报会直接退出(exit 2)，个股舆情为空"))

    # 自选股
    stock_list = (os.getenv("STOCK_LIST") or "").strip()
    if stock_list:
        codes = [c.strip().upper() for c in stock_list.replace(";", ",").split(",") if c.strip()]
        results.append(Check("自选股", OK, f"{len(codes)} 只: {','.join(codes)}"))
    else:
        results.append(Check("自选股", WARN, "未设置 STOCK_LIST，将使用 workflow 里的默认清单"))

    # 大盘区域
    region = (os.getenv("MARKET_REVIEW_REGION") or "cn").strip().lower()
    if region == "us":
        results.append(Check("大盘区域", OK, "us（美股）"))
    else:
        results.append(Check("大盘区域", WARN, f"当前为 {region}，本 fork 建议设为 us"))

    return results


# ---------------------------------------------------------------------------
# 2. 自选股解析（main.py 的整批校验）
# ---------------------------------------------------------------------------


def check_target_parse(stock_list: str) -> List[Check]:
    """复现 main.py 的整批校验。

    `main.py` 的 `_classify_stock_list_tokens` 是**整批**校验：任意一个 token
    被判为 unsupported，本轮一只都不分析。所以这里必须逐个过一遍解析器，
    而不是只抽查几只。
    """
    try:
        from src.services.stock_list_parser import ParseStatus, parse_analysis_target
    except Exception as exc:  # noqa: BLE001
        return [Check("自选股解析", SKIP, f"无法导入解析器: {type(exc).__name__}: {exc}")]

    codes = _resolve_codes(stock_list)
    unsupported: List[str] = []
    kinds: Dict[str, int] = {}

    for code in codes:
        try:
            target = parse_analysis_target(code)
        except Exception as exc:  # noqa: BLE001
            unsupported.append(f"{code}({type(exc).__name__})")
            continue
        if target.asset_type == ParseStatus.UNSUPPORTED:
            unsupported.append(f"{code}({target.unsupported_reason or 'unsupported'})")
        else:
            kinds[target.asset_type] = kinds.get(target.asset_type, 0) + 1

    if unsupported:
        return [
            Check(
                "自选股解析",
                FAIL,
                f"这些代码不被支持，会导致整批一只都不分析: {', '.join(unsupported)}",
                critical=True,
            )
        ]

    detail = "，".join(f"{kind}×{count}" for kind, count in sorted(kinds.items()))
    return [Check("自选股解析", OK, f"{len(codes)} 个全部可识别（{detail}）", critical=True)]


# ---------------------------------------------------------------------------
# 3. 行情数据
# ---------------------------------------------------------------------------


def _resolve_codes(stock_list: str) -> List[str]:
    raw = (stock_list or "").replace(";", ",")
    codes = [c.strip().upper() for c in raw.split(",") if c.strip()]
    if not codes:
        codes = ["AAPL", "NVDA", "MSFT", "TSLA", "GOOGL"]
    # 去重保序
    seen = set()
    ordered = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def check_data(stock_list: str, max_tickers: int = 20) -> List[Check]:
    results: List[Check] = []

    try:
        from data_provider.yfinance_fetcher import YfinanceFetcher
    except Exception as exc:  # noqa: BLE001
        return [Check("行情数据", SKIP, f"无法导入数据层（缺依赖？）: {type(exc).__name__}: {exc}")]

    try:
        import yfinance  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return [Check("行情数据", SKIP, f"未安装 yfinance: {type(exc).__name__}: {exc}")]

    fetcher = YfinanceFetcher()

    # 2.1 美股主要指数（大盘复盘依赖）
    try:
        indices = fetcher.get_main_indices(region="us") or []
        if indices:
            names = ", ".join(str(item.get("name") or item.get("code")) for item in indices[:6])
            results.append(Check("美股指数", OK, f"取到 {len(indices)} 个: {names}", critical=True))
        else:
            results.append(Check("美股指数", FAIL, "未取到任何美股指数，大盘复盘会为空", critical=True))
    except Exception as exc:  # noqa: BLE001
        results.append(Check("美股指数", FAIL, f"{type(exc).__name__}: {exc}", critical=True))

    # 2.2 自选股日线（逐只探测，能定位到具体哪个代码有问题）
    codes = _resolve_codes(stock_list)
    probe = codes[:max_tickers]
    end = datetime.now(REPORT_TZ)
    start = end - timedelta(days=12)
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    good: List[str] = []
    bad: List[str] = []
    for code in probe:
        try:
            # 传入原始代码即可：_fetch_raw_data 内部会走 _convert_stock_code，
            # 因此 SPX 会被自动映射成 ^GSPC，和正式分析走的是同一条链路。
            df = fetcher._fetch_raw_data(code, start_s, end_s)  # noqa: SLF001 - 与其他脚本一致地复用内部取数
            rows = 0 if df is None else len(df)
            if rows > 0:
                good.append(code)
            else:
                bad.append(f"{code}(空)")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{code}({type(exc).__name__})")
        time.sleep(0.3)  # 轻微限速，避免 Yahoo 429

    if good and not bad:
        results.append(Check("自选股日线", OK, f"{len(good)}/{len(probe)} 全部可取: {','.join(good)}", critical=True))
    elif good and bad:
        results.append(
            Check("自选股日线", WARN, f"{len(good)}/{len(probe)} 可取；失败: {', '.join(bad)}", critical=False)
        )
    else:
        results.append(Check("自选股日线", FAIL, f"全部取不到: {', '.join(bad[:8])}", critical=True))

    if len(codes) > len(probe):
        results.append(Check("自选股范围", WARN, f"仅探测前 {len(probe)} 只（共 {len(codes)} 只），其余请自行确认"))

    return results


# ---------------------------------------------------------------------------
# 4. LLM
# ---------------------------------------------------------------------------


def check_llm(config: Any) -> List[Check]:
    try:
        from src.analyzer import GeminiAnalyzer
    except Exception as exc:  # noqa: BLE001
        return [Check("LLM 连通性", SKIP, f"无法导入 analyzer: {type(exc).__name__}: {exc}")]

    started = time.time()
    try:
        analyzer = GeminiAnalyzer(config=config)
        text = analyzer.generate_text("只回复两个字：正常", max_tokens=32, temperature=0.0)
    except Exception as exc:  # noqa: BLE001
        return [Check("LLM 连通性", FAIL, f"{type(exc).__name__}: {str(exc)[:160]}", critical=False)]

    elapsed = time.time() - started
    if text and text.strip():
        return [Check("LLM 连通性", OK, f"{elapsed:.1f}s，返回「{text.strip()[:30]}」", critical=False)]
    return [Check("LLM 连通性", FAIL, "调用成功但返回空内容，检查模型权限/额度", critical=False)]


# ---------------------------------------------------------------------------
# 5. 搜索源
# ---------------------------------------------------------------------------


def check_search(config: Any) -> List[Check]:
    try:
        from scripts.us_news_digest import build_search_service
    except Exception:  # noqa: BLE001 - 直接跑脚本时 scripts 不是包，退回本地构造
        try:
            sys.path.insert(0, str(ROOT_DIR / "scripts"))
            from us_news_digest import build_search_service  # type: ignore
        except Exception as exc:  # noqa: BLE001
            return [Check("搜索源连通性", SKIP, f"无法导入搜索构造器: {type(exc).__name__}: {exc}")]

    try:
        service = build_search_service(config)
    except Exception as exc:  # noqa: BLE001
        return [Check("搜索源连通性", FAIL, f"初始化失败: {type(exc).__name__}: {exc}")]

    if not getattr(service, "is_available", False):
        return [Check("搜索源连通性", FAIL, "未配置任何搜索源")]

    started = time.time()
    try:
        response = service.search_topic_news_bounded(
            topic="美股隔夜要闻",
            max_results=3,
            focus_keywords=["美股 隔夜 收盘 标普500 纳斯达克"],
            timeout_seconds=25.0,
        )
    except Exception as exc:  # noqa: BLE001
        return [Check("搜索源连通性", FAIL, f"{type(exc).__name__}: {str(exc)[:160]}")]

    elapsed = time.time() - started
    results = getattr(response, "results", None) or []
    provider = getattr(response, "provider", "?")
    if results:
        sample = str(getattr(results[0], "title", ""))[:40]
        return [Check("搜索源连通性", OK, f"{elapsed:.1f}s，provider={provider}，{len(results)} 条，例：{sample}")]
    err = getattr(response, "error_message", None)
    return [Check("搜索源连通性", FAIL, f"provider={provider} 无结果，error={err}")]


# ---------------------------------------------------------------------------
# 6. 飞书推送
# ---------------------------------------------------------------------------


def check_feishu(config: Any, region: str, stock_list: str) -> List[Check]:
    try:
        from src.notification_sender.feishu_sender import FeishuSender
    except Exception as exc:  # noqa: BLE001
        return [Check("飞书推送", SKIP, f"无法导入 FeishuSender: {type(exc).__name__}: {exc}")]

    now = datetime.now(REPORT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    content = (
        "# ✅ 配置自检通过\n\n"
        f"**时间**：{now}（北京时间）\n\n"
        f"**大盘区域**：{region}\n\n"
        f"**自选股**：{stock_list}\n\n"
        "> 这是一条来自 `scripts/preflight_check.py` 的自检消息。"
        "收到它说明飞书链路已打通，后续定时任务会往这个群推送分析报告。"
    )

    sender = FeishuSender(config)
    try:
        try:
            ok = sender.send_to_feishu(content, title="配置自检")
        except TypeError:
            ok = sender.send_to_feishu(content)
    except Exception as exc:  # noqa: BLE001
        return [Check("飞书推送", FAIL, f"{type(exc).__name__}: {str(exc)[:160]}", critical=True)]

    if ok:
        return [Check("飞书推送", OK, "已发送测试卡片，请到群里确认", critical=True)]

    # 失败时给一条能直接动手的线索，而不是只有"发送失败"
    hints = []
    if not (os.getenv("FEISHU_WEBHOOK_SECRET") or "").strip():
        hints.append("未配置 FEISHU_WEBHOOK_SECRET（机器人若启用签名校验会返回 code=19021）")
    if not (os.getenv("FEISHU_WEBHOOK_KEYWORD") or "").strip():
        hints.append("若启用的是关键词校验则需配 FEISHU_WEBHOOK_KEYWORD")
    detail = "发送失败，飞书未受理"
    if hints:
        detail += "：" + "；".join(hints)
    return [Check("飞书推送", FAIL, detail, critical=True)]


# ---------------------------------------------------------------------------
# 7. 汇总
# ---------------------------------------------------------------------------


def _display_width(text: str) -> int:
    """按终端显示宽度计算（中文/全角算 2 列）。"""
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def emit_annotations(results: Sequence[Check]) -> None:
    """把失败项推到 GitHub Actions 的注解区（Annotations）。

    默认只返回 exit code 1 的话，界面上只能看到「Process completed with
    exit code 1」，看不到到底哪一项没过。这里用 ``::error`` / ``::warning``
    工作流命令把每一条失败项变成一条独立注解，一眼就能定位。

    GitHub 每个 step 最多展示 10 条 error 注解，所以各截断到 8 条。
    """
    if os.getenv("GITHUB_ACTIONS") != "true":
        return

    errors = [item for item in results if item.status == FAIL and item.critical]
    warnings = [item for item in results if item.status in (FAIL, WARN) and not item.critical]

    for item in errors[:8]:
        print(f"::error title=自检失败·{item.name}::{item.detail}", flush=True)
    for item in warnings[:8]:
        print(f"::warning title=自检提醒·{item.name}::{item.detail}", flush=True)


def render(results: Sequence[Check]) -> None:
    width = max(_display_width(item.name) for item in results) + 2
    print("")
    print("=" * 72)
    print("📋 配置自检结果")
    print("=" * 72)
    for item in results:
        icon = _STATUS_ICON.get(item.status, "?")
        mark = " [关键]" if item.critical else ""
        print(f"{icon} {_pad(item.name, width)}{item.detail}{mark}")
    print("-" * 72)

    critical_fail = [item for item in results if item.critical and item.status == FAIL]
    warnings = [item for item in results if item.status in (WARN, FAIL) and not item.critical]

    if critical_fail:
        print(f"❌ 关键项失败 {len(critical_fail)} 个：{', '.join(i.name for i in critical_fail)}")
        print("   先修这些，否则定时任务跑了也收不到东西。")
    else:
        print("✅ 关键项全部通过（飞书推送 + 行情数据）")
    if warnings:
        print(f"⚠️  非关键问题 {len(warnings)} 个：{', '.join(i.name for i in warnings)}")
        print("   这些不阻塞推送，但会让报告内容变薄，建议补齐。")
    if not critical_fail and not warnings:
        print("🎉 全部检查通过，可以等定时任务了。")
    print("=" * 72)
    print("")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="配置自检：验证飞书 / LLM / 搜索源 / 行情数据是否可用",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--no-push", action="store_true", help="不发飞书测试消息")
    parser.add_argument("--quick", action="store_true", help="只查配置与飞书，跳过行情/LLM/搜索")
    parser.add_argument("--skip-data", action="store_true", help="跳过行情数据检查")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 LLM 检查")
    parser.add_argument("--skip-search", action="store_true", help="跳过搜索源检查")
    parser.add_argument("--max-tickers", type=int, default=20, help="最多探测多少只自选股（默认 20）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    config = get_config()
    region = (os.getenv("MARKET_REVIEW_REGION") or "cn").strip().lower()
    stock_list = (os.getenv("STOCK_LIST") or "").strip() or "AAPL,NVDA,MSFT,TSLA,GOOGL"

    results: List[Check] = []
    results.extend(check_env(config))
    # 解析校验很轻（纯本地，不联网），即使是 --quick 也跑
    results.extend(check_target_parse(stock_list))

    if not args.quick:
        if not args.skip_data:
            print("⏳ 正在探测行情数据（逐只，约 10~30 秒）…", file=sys.stderr)
            results.extend(check_data(stock_list, max_tickers=args.max_tickers))
        if not args.skip_llm:
            print("⏳ 正在测试 LLM 连通性…", file=sys.stderr)
            results.extend(check_llm(config))
        if not args.skip_search:
            print("⏳ 正在测试搜索源…", file=sys.stderr)
            results.extend(check_search(config))

    if not args.no_push:
        print("⏳ 正在发送飞书测试消息…", file=sys.stderr)
        results.extend(check_feishu(config, region, stock_list))

    if args.json:
        print(json.dumps([item.to_dict() for item in results], ensure_ascii=False, indent=2))
    else:
        # 先把失败项推到注解区，再打表格（注解不依赖日志滚动位置）
        emit_annotations(results)
        render(results)

    critical_fail = [item for item in results if item.critical and item.status == FAIL]
    return 1 if critical_fail else 0


if __name__ == "__main__":
    sys.exit(main())
