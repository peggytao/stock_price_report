from __future__ import annotations

import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

HOST = "0.0.0.0"
PORT = 8000

POPULAR_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD",
    "NFLX", "JPM", "BAC", "INTC", "ORCL", "CRM", "UBER", "PLTR",
]


def normalize_symbol(raw_symbol: str) -> str:
    return raw_symbol.strip().upper()


def format_number(num: Any) -> str:
    if num in [None, "N/A"]:
        return "N/A"
    if num >= 1_000_000_000_000:
        return f"${num/1_000_000_000_000:.2f}T"
    if num >= 1_000_000_000:
        return f"${num/1_000_000_000:.2f}B"
    if num >= 1_000_000:
        return f"${num/1_000_000:.2f}M"
    return f"${num}"


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def get_symbol_suggestions(partial_symbol: str) -> list[str]:
    partial = normalize_symbol(partial_symbol)
    if not partial:
        return POPULAR_SYMBOLS[:8]
    starts_with = [s for s in POPULAR_SYMBOLS if s.startswith(partial)]
    contains = [s for s in POPULAR_SYMBOLS if partial in s and s not in starts_with]
    return (starts_with + contains)[:8]


def get_expert_recommendation(symbol: str) -> tuple[str, str, str]:
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{quote(symbol)}?modules=financialData"
    data = fetch_json(url)
    result = data.get("quoteSummary", {}).get("result") or []
    if not result:
        return "HOLD", "hold", "Insufficient analyst data; using neutral stance."

    financial_data = result[0].get("financialData", {})
    rec_key = str(financial_data.get("recommendationKey", "hold")).lower()

    if rec_key in {"strong_buy", "buy"}:
        return "BUY", "buy", "Analyst consensus leans bullish."
    if rec_key in {"underperform", "sell", "strong_sell"}:
        return "SELL", "sell", "Analyst consensus leans bearish."
    return "HOLD", "hold", "Analyst sentiment is mixed."


def get_stock_snapshot(symbol: str) -> dict[str, str]:
    quote_url = f"https://query1.finance.yahoo.com/v7/finance/quote?{urlencode({'symbols': symbol})}"
    data = fetch_json(quote_url)
    result = data.get("quoteResponse", {}).get("result") or []
    if not result:
        raise RuntimeError(f"No quote data returned for {symbol}")

    quote_data = result[0]
    recommendation, rec_class, reason = get_expert_recommendation(symbol)

    return {
        "symbol": symbol,
        "price": f"${float(quote_data.get('regularMarketPrice')):.2f}" if quote_data.get("regularMarketPrice") else "N/A",
        "pe_ratio": quote_data.get("trailingPE", "N/A"),
        "eps": quote_data.get("epsTrailingTwelveMonths", "N/A"),
        "market_cap": format_number(quote_data.get("marketCap", "N/A")),
        "recommendation": recommendation,
        "rec_class": rec_class,
        "reason": reason,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }


def get_headlines(symbol: str) -> list[str]:
    rss_url = f"https://finance.yahoo.com/rss/headline?s={quote(symbol)}"
    request = Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=20) as response:
        xml_data = response.read()

    root = ET.fromstring(xml_data)
    titles: list[str] = []
    for item in root.findall(".//item/title")[:5]:
        titles.append(item.text or "")
    return titles


class AppHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict[str, Any], code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self) -> None:
        index_path = Path("index.html")
        content = index_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/":
            self._send_index()
            return

        if parsed.path == "/api/symbol-suggestions":
            partial = params.get("q", [""])[0]
            self._send_json({"suggestions": get_symbol_suggestions(partial)})
            return

        if parsed.path == "/api/report":
            symbol = normalize_symbol(params.get("symbol", ["NVDA"])[0])
            if not symbol:
                self._send_json({"error": "A stock symbol is required."}, 400)
                return
            try:
                snapshot = get_stock_snapshot(symbol)
            except Exception as exc:
                self._send_json({"error": f"Stock data fetch failed for {symbol}: {exc}"}, 502)
                return

            try:
                headlines = get_headlines(symbol)
            except Exception:
                headlines = []

            self._send_json({
                "snapshot": snapshot,
                "headlines": headlines,
                "suggestions": get_symbol_suggestions(symbol),
            })
            return

        self._send_json({"error": "Not found"}, 404)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Server running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
