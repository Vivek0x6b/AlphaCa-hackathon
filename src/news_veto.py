"""
News-aware veto layer on top of the deterministic breakout signal.

Design: the breakout signal (signals.py) is the only thing that can
originate a trade - this module can only veto one, never create one. That
keeps the strategy's core testable and backtestable (nothing here changes
what the 2-year backtest already validated), while adding real-world
context a pure price/volume breakout can't see (an earnings miss, an
executive departure, a lawsuit) alongside a technically clean breakout.

The LLM call is a live, non-backtestable layer, same as the Hermes daily
narration - validated by watching it work live, not by replaying history
(there's no historical news feed to backtest against anyway).

Fails open on purpose: if the news fetch or the LLM call errors out, the
trade proceeds as if nothing was found. A missing news check should never
be the reason a validated signal gets skipped - that would make the whole
strategy's uptime depend on a third-party news feed and an LLM API,
neither of which the backtested edge relies on.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from src.market_data import ENV_PATH, load_credentials

NEWS_LOOKBACK_DAYS = 5
MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


def _load_nim_key() -> str:
    load_dotenv(ENV_PATH)
    return os.environ["NVIDIA_NIM_API_KEY"]


def _fetch_recent_news(ticker: str) -> list[dict]:
    api_key, secret_key = load_credentials()
    client = NewsClient(api_key, secret_key)
    request = NewsRequest(
        symbols=ticker,
        start=datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS),
        limit=10,
    )
    news_set = client.get_news(request)
    return news_set.data.get("news", [])


def check_news_veto(ticker: str, direction: str) -> tuple[bool, str]:
    """
    Returns (vetoed, reasoning). vetoed=True means skip the trade.

    Only ever called after a breakout signal already fired - this can
    only block that trade, never suggest one on its own.
    """
    try:
        articles = _fetch_recent_news(ticker)
    except Exception as exc:
        return False, f"News fetch failed ({exc}), proceeding without a news check."

    if not articles:
        return False, "No recent news found for this ticker."

    headlines = "\n".join(
        f"- {a.headline}: {a.summary or ''}" for a in articles[:10]
    )

    thesis = "bullish (a call debit spread)" if direction == "call" else "bearish (a put debit spread)"

    prompt = f"""A momentum breakout signal just fired for {ticker}, and the plan is to open a {thesis} trade on it.

Recent news headlines for {ticker} (last {NEWS_LOOKBACK_DAYS} days):
{headlines}

Does this news contain a clear, material red flag that would undermine this specific thesis (e.g. an earnings miss, a guidance cut, an executive departure, a lawsuit, a regulatory action, a product recall)? Routine news (opinion pieces, general market commentary, unrelated announcements) is not a red flag.

Respond with ONLY a JSON object, no other text: {{"veto": true or false, "reasoning": "one sentence"}}"""

    try:
        from openai import OpenAI

        client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=_load_nim_key())
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            # Nemotron is a reasoning model - it spends tokens on chain-of-
            # thought before emitting the JSON answer, so a small budget
            # here just gets cut off mid-thought with no answer at all.
            max_tokens=1500,
            temperature=0,
        )
        content = response.choices[0].message.content.strip()

        # Reasoning models sometimes wrap the answer in prose or a code
        # fence despite the "ONLY a JSON object" instruction - pull out
        # the first {...} block rather than requiring an exact match.
        start = content.index("{")
        end = content.rindex("}") + 1
        parsed = json.loads(content[start:end])

        return bool(parsed["veto"]), parsed["reasoning"]
    except Exception as exc:
        return False, f"News veto check failed ({exc}), proceeding without it."
