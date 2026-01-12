"""TradingView idea puller implementation.

This module implements the IdeaPullerBase to fetch trading ideas from TradingView.
It scrapes the TradingView ideas page for a given ticker, extracts individual chart URLs,
and fetches each chart page to get trade details.

URL Pattern: https://www.tradingview.com/symbols/NASDAQ-{ticker}/ideas/
Chart URL Pattern: https://www.tradingview.com/chart/{ticker}/{chart-name}/

The implementation:
1. Fetches the ideas listing page for a ticker
2. Extracts individual chart URLs from idea cards
3. Fetches each chart page individually
4. Returns list of SimpleIdea objects (currently empty, just prints chart URLs)

Example:
    from common.di_container import container
    from common.models.idea_params import IdeaParams
    
    idea_puller = container.tradingview_idea_puller()
    params = IdeaParams(ticker="AAPL")
    ideas = await idea_puller.pull_ideas(params)
"""
import asyncio
import json
import logging
from typing import Optional

import httpx
from lxml import html

from common.models.idea_params import IdeaParams
from common.models.simple_idea import SimpleIdea
from common.user_agent import UserAgentManager
from pullers.ideas.abstracts.idea_puller_base import IdeaPullerBase

logger: logging.Logger = logging.getLogger(__name__)


class TradingViewIdeaPuller(IdeaPullerBase):
    """Pulls trade ideas from TradingView using web scraping."""

    BASE_URL: str = "https://www.tradingview.com/symbols/{ticker}/ideas/"
    IDEA_CARD_XPATH: str = '//article[@class="card-exterior-Us1ZHpvJ card-AyE8q7_6 stretch-link-title-AyE8q7_6 idea-card-R05xWTMw js-userlink-popup-anchor"]'

    def __init__(self, http_client: httpx.AsyncClient):
        """Initialize the TradingView idea puller with HTTP client."""
        super().__init__(http_client)

    async def  pull_ideas(self, params: IdeaParams) -> list[SimpleIdea]:
        """Fetch trade ideas from TradingView for the specified ticker.
        
        Args:
            params: Parameters containing the ticker symbol.
            
        Returns:
            List of SimpleIdea objects extracted from TradingView.
        """
        if not params.ticker:
            logger.warning("No ticker provided in params")
            return []

        url: str = self.BASE_URL.format(ticker=params.ticker.upper())
        logger.info(f"Fetching TradingView ideas from: {url}")

        # Fetch the ideas listing page
        html_content: str = await self._fetch_html(url)
        
        # Extract chart URLs from idea cards
        chart_urls: list[str] = self._extract_chart_urls(html_content)
        # chart_urls: list[str] = ["https://www.tradingview.com/chart/A/puRcw1wj-HERE-IS-YOUR-NEXT-LONG-OPPORTUNITY/"]
        logger.info(f"Found {len(chart_urls)} chart URLs for {params.ticker}")
        
        if not chart_urls:
            return []
        
        # Fetch all chart pages concurrently
        chart_htmls: list[str] = await asyncio.gather(
            *[self._fetch_html(chart_url) for chart_url in chart_urls],
            return_exceptions=True
        )
        
        # Parse JSON from each chart page
        ideas: list[SimpleIdea] = []
        for idx, (chart_url, chart_html) in enumerate(zip(chart_urls, chart_htmls)):
            if isinstance(chart_html, Exception):
                logger.error(f"Error fetching {chart_url}: {chart_html}")
                continue
            
            # Extract and parse the JSON from the script tag
            json_data: Optional[dict] = self._extract_json_from_script(chart_html)
            
            if json_data:
                # Extract all LineToolPriceRange objects
                line_tool_objects = json_data
                if line_tool_objects:
                    print(f"     Trade #{params.ticker}:")
                    print(f"       Entry:        ${(m := sorted(line_tool_objects)[1]):.2f}")
                    print(f"       Take Profit:  ${(d := max(line_tool_objects)):.2f}")
                    print(f"       Stop Loss:    ${(s := min(line_tool_objects)):.2f}")
                    print(f"       Risk/Reward:  {s}/{d}")
                    print()
                    idea = SimpleIdea(ticker=params.ticker.upper(), entry_price=m, take_profit=d, stop_loss=s)
                    ideas.append(idea)
                else:
                    logger.debug(f"No LineToolPriceRange objects found in {chart_url}")
            else:
                logger.warning(f"No JSON data found in {chart_url}")
        
        return ideas

    async def _fetch_html(self, url: str) -> str:
        """Fetch HTML content from the given URL.
        
        Args:
            url: The URL to fetch.
            
        Returns:
            Raw HTML content as a string.
        """
        headers: dict[str, str] = {
            "User-Agent": UserAgentManager.get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        response: httpx.Response = await self._http_client.get(
            url,
            headers=headers,
            follow_redirects=True,
            timeout=30.0
        )
        response.raise_for_status()
        
        return response.text

    def _extract_chart_urls(self, html_content: str) -> list[str]:
        """Extract chart URLs from the ideas listing page.
        
        Args:
            html_content: Raw HTML content from the ideas page.
            
        Returns:
            List of chart URLs to fetch.
        """
        tree = html.fromstring(html_content)
        
        # Extract idea cards using the specified XPath
        idea_cards = tree.xpath(self.IDEA_CARD_XPATH)
        
        if not idea_cards:
            logger.warning("No idea cards found")
            return []

        logger.debug(f"Found {len(idea_cards)} idea cards")

        chart_urls: list[str] = []
        for card in idea_cards:
            # Look for <a> tags with class "image-link-gDIex6UB"
            links = card.xpath('.//a[contains(@class, "image-link-gDIex6UB")]')
            
            for link in links:
                href: str = link.get('href', '')
                
                # TradingView chart URLs can be full URLs or relative paths
                if href.startswith('https://www.tradingview.com/chart/'):
                    chart_urls.append(href)
                    logger.debug(f"Found chart URL: {href}")
                    break
                elif href.startswith('/chart/'):
                    full_url: str = f"https://www.tradingview.com{href}"
                    chart_urls.append(full_url)
                    logger.debug(f"Found chart URL: {full_url}")
                    break

        return chart_urls

    def _extract_json_from_script(self, html_content: str) -> Optional[dict]:
        """Extract and parse JSON from all script tags, looking for 'indexes' data.
        
        Args:
            html_content: Raw HTML content from a chart page.
            
        Returns:
            Parsed JSON data as a dictionary, or None if not found.
        """
        try:
            tree = html.fromstring(html_content)
            
            # Find ALL script tags
            all_script_tags = tree.xpath('//script[@type="application/prs.init-data+json"]')
            
            logger.debug(f"Found {len(all_script_tags)} script tags total")
            
            # Try each script tag
            for idx, script_tag in enumerate(all_script_tags):
                script_content: str = script_tag.text_content().strip()
                
                if not script_content or len(script_content) < 10:
                    continue
                
                # Check if this looks like JSON (starts with { or [)
                if script_content.startswith(('{', '[')) and ("LineToolRiskRewardLong" in script_content or "LineToolPriceRange" in script_content):
                    try:
                        json_data: dict = json.loads(script_content)
                        
                        # Check if this JSON contains "indexes"
                        if (p := self._find_indexes_in_json(json_data)):
                            logger.info(f"Found 'indexes' in script tag #{idx}")
                            return p
                            
                    except json.JSONDecodeError:
                        continue
            
            logger.warning("No script tag containing 'indexes' found")
            return None
        except Exception as e:
            return None

    def _find_indexes_in_json(self, data: dict) -> Optional[list]:
        """Recursively search for 'indexes' key in the JSON structure.
        
        Also handles 'content' keys that contain JSON strings that need parsing.
        
        Args:
            data: The JSON data to search through.
            
        Returns:
            The 'indexes' array if found, or None if not found.
        """
        # Direct key check
        p = data[list(data.keys())[0]]
        data = p[list(p.keys())[0]]
        if isinstance(p, dict):
            if "indexes" in data:
                return data["indexes"]
            
            # Check if there's a 'content' key with JSON string
            if "content" in data and isinstance(data["content"], str):
                try:
                    # Parse the JSON string
                    content_json = json.loads(data["content"])
                    # Recursively search the parsed content
                    sources = []
                    if "charts" in content_json:
                        sources = content_json["charts"][0]["panes"][0]["sources"]
                    elif "panes" in content_json:
                        sources = content_json["panes"][0]["sources"]
                    else :
                        return []
                    result = list(filter(lambda x: x["type"] == "LineToolHorzRay", sources))
                    if result is not None:
                        dat =  [d["points"][0]["price"] for d in result]
                        return dat
                except (json.JSONDecodeError, TypeError):
                    pass
            
            # Recursively search through all values
            for value in data.values():
                if isinstance(value, dict):
                    result = self._find_indexes_in_json(value)
                    if result is not None:
                        return result
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            result = self._find_indexes_in_json(item)
                            if result is not None:
                                return result
        
        return None

    def _extract_line_tool_objects(self, data: dict) -> list[dict]:
        """Extract all LineToolPriceRange objects from the JSON structure.
        
        Args:
            data: The JSON data to search through.
            
        Returns:
            List of LineToolPriceRange objects found.
        """
        results: list[dict] = []
        
        def search_recursively(obj: any) -> None:
            if isinstance(obj, dict):
                # Check if this is a LineToolPriceRange object
                if (obj.get("type") == "LineToolPriceRange" or obj.get("type")=="LineToolRiskRewardLong") and "indexes" in obj:
                    results.append(obj)
                
                # Continue searching in nested structures
                for value in obj.values():
                    search_recursively(value)
            elif isinstance(obj, list):
                for item in obj:
                    search_recursively(item)
        
        search_recursively(data)
        return results

    def _extract_trade_levels(self, line_tool_obj: dict) -> Optional[dict]:
        """Extract take profit and stop loss from a LineToolPriceRange object.
        
        Args:
            line_tool_obj: A LineToolPriceRange object with indexes.
            
        Returns:
            Dict with 'entry', 'take_profit', 'stop_loss', and 'risk_reward', or None if invalid.
        """
        indexes = line_tool_obj.get("indexes", [])
        
        # Skip if not exactly 2 price levels
        if len(indexes) != 2:
            return None
        
        # Extract prices from the two indexes
        prices = [idx.get("price") for idx in indexes if "price" in idx]
        
        if len(prices) != 2:
            return None
        
        # Biggest price is take_profit, smallest is stop_loss
        take_profit = max(prices)
        stop_loss = min(prices)
        
        # Entry is the midpoint between take profit and stop loss
        entry = (take_profit + stop_loss) / 2
        
        # Calculate risk/reward ratio
        risk = entry - stop_loss
        reward = take_profit - entry
        risk_reward = reward / risk if risk > 0 else 0
        
        return {
            "entry": entry,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "risk_reward": risk_reward
        }

    def _extract_price(self, text: str, pattern: str) -> Optional[float]:
        """Extract a price value from text using a regex pattern.
        
        Args:
            text: Text to search.
            pattern: Regex pattern to match.
            
        Returns:
            Extracted price as float, or None if not found.
        """
        match = re.search(pattern, text.lower())
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                return None
        return None
