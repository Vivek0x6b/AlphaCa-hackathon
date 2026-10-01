"""
Daily market news brief, written by Nemotron for the whole watchlist.

The news veto (news_veto.py) only reads news for a ticker whose breakout
signal already fired, so on a day with no signals the LLM read nothing at
all. This runs every trading day regardless: it pulls each watchlist
ticker's recent headlines plus its price position relative to the
breakout rules, and asks Nemotron for a market read - overall tone, any
material event per ticker, and which names are worth watching.

Same boundary as every other LLM use in this project: the brief is
analysis only. It never places, blocks or changes a trade - trades still
come only from the backtested breakout rules (signals.py) and pass the
news veto as before. Fails soft: any error is logged and the rest of the
daily run carries on.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from config.watchlist import WATCHLIST, BREAKOUT_LOOKBACK_DAYS, TREND_MA_DAYS
from src.journal import log_entry
from src.market_data import fetch_bars, load_credentials
from src.news_veto import MODEL, _load_nim_key

# Monday's brief covers the weekend; every other day covers the last day.
LOOKBACK_HOURS_WEEKDAY = 24
LOOKBACK_HOURS_MONDAY = 72
HEADLINES_PER_TICKER = 8
BRIEF_DIR = Path(__file__).resolve().parent.parent / "logs" / "news_briefs"


def _fetch_headlines(ticker: str, since: datetime) -> list[str]:
    api_key, secret_key = load_credentials()
    client = NewsClient(api_key, secret_key)
    news = client.get_news(NewsRequest(symbols=ticker, start=since, limit=HEADLINES_PER_TICKER))
    return [
        f"{a.headline}" + (f": {a.summary[:200]}" if a.summary else "")
        for a in news.data.get("news", [])
    ]


def _price_context(bars) -> dict:
    """Where each ticker stands against the breakout rules, from daily bars."""
    context = {}
    for ticker, df in bars.items():
        if len(df) < TREND_MA_DAYS + 1:
            continue
        close = float(df["close"].iloc[-1])
        prior = df.iloc[:-1]
        high = float(prior["high"].tail(BREAKOUT_LOOKBACK_DAYS).max())
        context[ticker] = {
            "close": round(close, 2),
            "day_change_pct": round((close / float(prior["close"].iloc[-1]) - 1) * 100, 2),
            "pct_below_breakout": round((high / close - 1) * 100, 2),
            "above_trend_ma": close > float(prior["close"].tail(TREND_MA_DAYS).mean()),
        }
    return context


def _ask_nemotron(prompt: str) -> dict:
    from openai import OpenAI

    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=_load_nim_key())
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        # Reasoning model: it thinks before answering, and this answer
        # covers every ticker, so it needs a much bigger budget than the
        # single-ticker veto check.
        max_tokens=8000,
        temperature=0,
    )
    content = response.choices[0].message.content.strip()
    return json.loads(content[content.index("{"): content.rindex("}") + 1])


def build_news_brief(run_date) -> dict:
    hours = LOOKBACK_HOURS_MONDAY if run_date.weekday() == 0 else LOOKBACK_HOURS_WEEKDAY
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    prices = _price_context(fetch_bars(WATCHLIST, lookback_days=120))
    headlines = {}
    for ticker in WATCHLIST:
        try:
            headlines[ticker] = _fetch_headlines(ticker, since)
        except Exception as exc:
            headlines[ticker] = [f"(news fetch failed: {exc})"]

    sections = []
    for ticker in WATCHLIST:
        p = prices.get(ticker)
        price_line = (
            f"close {p['close']}, {p['day_change_pct']:+.2f}% today, "
            f"{p['pct_below_breakout']:.2f}% below its {BREAKOUT_LOOKBACK_DAYS}-day high, "
            f"{'above' if p['above_trend_ma'] else 'below'} its {TREND_MA_DAYS}-day average"
            if p else "price data unavailable"
        )
        news_lines = "\n".join(f"  - {h}" for h in headlines[ticker]) or "  - (no news)"
        sections.append(f"{ticker} ({price_line})\n{news_lines}")

    prompt = f"""You are the market analyst for an options trading bot. The bot buys call debit spreads when a stock closes above its {BREAKOUT_LOOKBACK_DAYS}-day high, above its {TREND_MA_DAYS}-day average, on above-average volume. You do not decide trades; you write the daily read of the market for the people running it.

News from the last {hours} hours, and where each watchlist ticker stands:

{chr(10).join(sections)}

Write the daily brief. Be specific and factual - only use what is in the headlines and numbers above, and say so when there is no meaningful news. Respond with ONLY a JSON object, no other text:
{{"market_summary": "2-3 sentences on the overall market tone and the main themes driving it",
 "tickers": [{{"ticker": "AAPL", "sentiment": "positive" or "neutral" or "negative", "material_event": true or false, "note": "one sentence"}}],
 "watch": [{{"ticker": "MSFT", "reason": "one sentence on why it is worth watching next session, e.g. news catalyst or close to a breakout"}}],
 "risks": ["one sentence per market-wide risk named in the news"]}}
Include every ticker listed above in "tickers"."""

    analysis = _ask_nemotron(prompt)
    return {
        "date": run_date.isoformat(),
        "lookback_hours": hours,
        "prices": prices,
        "headline_counts": {t: len(h) for t, h in headlines.items()},
        "analysis": analysis,
    }


def _to_markdown(brief: dict) -> str:
    a = brief["analysis"]
    lines = [f"# Market brief - {brief['date']}", "", a.get("market_summary", ""), ""]
    if a.get("risks"):
        lines += ["## Risks", *[f"- {r}" for r in a["risks"]], ""]
    if a.get("watch"):
        lines += ["## Worth watching", *[f"- **{w['ticker']}**: {w['reason']}" for w in a["watch"]], ""]
    lines += [
        "## Watchlist",
        "",
        "| Ticker | Close | Today | Below breakout | Trend | News | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in a.get("tickers", []):
        p = brief["prices"].get(t["ticker"], {})
        flag = " (material)" if t.get("material_event") else ""
        lines.append(
            f"| {t['ticker']} | {p.get('close', '-')} | {p.get('day_change_pct', 0):+.2f}% "
            f"| {p.get('pct_below_breakout', 0):.2f}% | {'above' if p.get('above_trend_ma') else 'below'} "
            f"| {t.get('sentiment', '-')}{flag} | {t.get('note', '')} |"
        )
    return "\n".join(lines) + "\n"


def run_news_brief(run_date) -> dict:
    """Build the brief, journal it, and save a readable copy. Returns it."""
    brief = build_news_brief(run_date)
    log_entry("news_brief", brief)

    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEF_DIR / f"{brief['date']}.md"
    path.write_text(_to_markdown(brief), encoding="utf-8")
    print(f"News brief saved to {path}")
    print(brief["analysis"].get("market_summary", ""))
    return brief
