"""Data models for the news trading application."""

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class TradeResult(str, Enum):
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    EXPIRED = "EXPIRED"
    PENDING = "PENDING"


class ScanType(str, Enum):
    EUROPE = "europe"
    US = "us"


class NewsItem(BaseModel):
    """A single news headline collected from a source."""
    title: str
    source: str
    url: str = ""
    published: datetime | None = None
    related_tickers: list[str] = Field(default_factory=list)


class ScoredNews(BaseModel):
    """A news item after LLM scoring."""
    news: NewsItem
    surprise: int = Field(ge=0, le=100)
    freshness: int = Field(ge=0, le=100)
    directional_clarity: int = Field(ge=0, le=100)
    direction: Direction = Direction.NEUTRAL
    impacted_tickers: list[str] = Field(default_factory=list)
    reasoning: str = ""
    news_category: str = "other"  # earnings, macro, geopolitical, regulatory, m_a, sector, commodity, other

    @property
    def total_score(self) -> float:
        return self.surprise * 0.4 + self.freshness * 0.3 + self.directional_clarity * 0.3


class TradeRecommendation(BaseModel):
    """The single trade output of a scan."""
    scan_type: ScanType
    timestamp: datetime
    ticker: str
    asset_name: str
    category: str
    direction: Direction
    news_headline: str = ""  # Original news title that triggered the trade
    news_category: str = "other"  # Type of news: earnings, macro, geopolitical, etc.
    catalyst: str
    entry_price: float
    target_price: float
    stop_price: float
    target_pct: float
    stop_pct: float
    risk_reward: float
    confidence: int = Field(ge=0, le=100)
    time_window: str
    news_sources: list[str] = Field(default_factory=list)
    result: TradeResult = TradeResult.PENDING
    exit_price: float | None = None
    pnl_pct: float | None = None
    closed_at: datetime | None = None


class ScanResult(BaseModel):
    """Full result of a scan — either a trade or a pass."""
    scan_type: ScanType
    timestamp: datetime
    has_trade: bool
    recommendation: TradeRecommendation | None = None
    reason_no_trade: str = ""
    news_analyzed: int = 0


class JournalEntry(BaseModel):
    """Daily journal entry for a single trade — generated at 22:00 CET."""
    date: str  # YYYY-MM-DD
    scan_type: ScanType
    news_title: str
    news_source: str
    news_category: str = "other"  # Type of news event
    reasoning: str
    score: float
    ticker: str
    asset_name: str
    asset_category: str = ""  # actions_europe, forex, commodities, etc.
    direction: Direction
    entry_time: datetime
    entry_price: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    result: TradeResult = TradeResult.PENDING
    pnl_pct: float | None = None
    review: str = ""  # Post-trade analysis


class PerformanceStats(BaseModel):
    """Aggregated performance metrics."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    expired: int = 0
    pending: int = 0
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    total_pnl_pct: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    avg_confidence: float = 0.0
    by_category: dict[str, dict] = Field(default_factory=dict)
    by_scan_type: dict[str, dict] = Field(default_factory=dict)
