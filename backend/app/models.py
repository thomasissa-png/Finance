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
    source_weight: float = 0.75  # (#6) reliability weight of source
    description: str = ""  # RSS summary/description — gives Claude more context


class ChainReaction(BaseModel):
    """A second-order impact detected from chain reaction analysis."""
    ticker: str
    direction: Direction
    reason: str
    source_ticker: str  # The primary ticker that triggered this chain


class ScoredNews(BaseModel):
    """A news item after LLM scoring."""
    news: NewsItem
    surprise: int = Field(ge=0, le=100)
    freshness: int = Field(ge=0, le=100)
    directional_clarity: int = Field(ge=0, le=100)
    transmission_delay: int = Field(default=50, ge=0, le=100)  # 0=already priced, 100=nobody saw it
    market_awareness: int = Field(default=50, ge=0, le=100)    # 0=nobody, 100=everyone
    direction: Direction = Direction.NEUTRAL
    impacted_tickers: list[str] = Field(default_factory=list)
    reasoning: str = ""
    news_category: str = "other"
    category_score_mult: float = 1.0  # Edge-priority multiplier from config
    chain_reactions: list[ChainReaction] = Field(default_factory=list)

    @property
    def total_score(self) -> float:
        """Edge-weighted score: surprise * clarity * edge_factor * source_weight * category_mult.

        La formule privilegie les news NON ENCORE PRICEES :
        - transmission_delay eleve = le marche n'a pas encore integre -> boost
        - market_awareness faible = peu de monde a vu -> boost
        - category_score_mult penalise les earnings/macro, booste commodity/weather

        edge_factor = transmission_delay/100 * (1 - market_awareness/100)
        Un earnings (delay=5, awareness=95) -> edge_factor = 0.05 * 0.05 = 0.0025
        Un rapport meteo (delay=80, awareness=10) -> edge_factor = 0.8 * 0.9 = 0.72

        NOTE: freshness n'est PAS dans la formule car Claude voit deja l'age
        de la news ("[il y a X.Xh]") et ajuste surprise/transmission_delay
        en consequence. L'inclure causerait une double penalisation.
        freshness reste stocke pour le journal/tracing.
        """
        clarity_factor = self.directional_clarity / 100

        # Edge factor: how much room is left for the market to price this?
        delay_factor = self.transmission_delay / 100
        awareness_discount = 1 - (self.market_awareness / 100)
        edge_factor = delay_factor * awareness_discount

        # Floor at 0.05 — enough to crush zero-edge news (earnings/macro)
        # but high enough that mid-range signals aren't obliterated
        edge_factor = max(edge_factor, 0.05)

        score = self.surprise * clarity_factor * edge_factor
        # Apply source reliability weight (#6)
        score *= self.news.source_weight
        # Apply category edge-priority multiplier
        score *= self.category_score_mult
        return round(score, 2)


class TradeRecommendation(BaseModel):
    """The single trade output of a scan."""
    schema_version: int = 3  # (#42) v3: ML learning fields
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
    # (#4) News déjà pricée detection
    pre_move_pct: float | None = None  # Movement before scan (vs prev close)
    # (#13) Gap buffer applied
    gap_buffer_applied: bool = False
    # (#24) Binary event warning
    binary_event_warning: str | None = None
    # (#10) Volume confirmation
    volume_confirmed: bool | None = None  # None = no data, True/False = confirmed
    # Edge-detection metrics
    transmission_delay: int | None = None  # 0=already priced, 100=nobody saw it
    market_awareness: int | None = None    # 0=nobody, 100=everyone
    edge_score: float | None = None        # edge_factor used in scoring
    chain_reactions: list[dict] | None = None  # Second-order impacts detected
    # ── P0-#4: Raw score decomposition (diagnose Claude vs formula vs learning)
    raw_claude_score: float | None = None      # Score brut avant learning adjustments
    learning_multiplier: float | None = None   # Multiplicateur learning appliqué
    # ── P1-#13: Contextual features at time of trade
    vix_at_trade: float | None = None
    market_regime: str | None = None           # calm/normal/elevated/stress
    day_of_week: int | None = None             # 0=Monday ... 6=Sunday
    volume_ratio: float | None = None          # Today's volume / 20d avg
    # ── P1-#6: Transmission delay tracking
    predicted_transmission_delay: int | None = None  # Claude's estimate at scoring time
    actual_pricing_time_hours: float | None = None   # Measured: hours from trade to TP/SL
    delay_accuracy: float | None = None              # Difference predicted vs actual


class ScanResult(BaseModel):
    """Full result of a scan — either a trade or a pass."""
    scan_type: ScanType
    timestamp: datetime
    has_trade: bool
    recommendation: TradeRecommendation | None = None
    reason_no_trade: str = ""
    news_analyzed: int = 0
    # (#5) Market context included in scoring
    market_context: dict | None = None
    # ── Journal enrichment: full decision trace
    all_scored_news: list[dict] | None = None        # All news scored by Claude with scores
    rejection_log: list[dict] | None = None           # Why each candidate was rejected
    decision_summary: str | None = None               # Why this trade was chosen over others
    learning_state: dict | None = None                # Learning adjustments at time of scan


class ScanHistoryEntry(BaseModel):
    """Persisted record of a single scan — all scored events + decision trace.

    Stored in data/scan_history.json after each scan. Keeps a full audit trail
    of what was evaluated, what was rejected, and why — even when no trade was taken.
    """
    timestamp: datetime
    scan_type: ScanType
    has_trade: bool
    news_analyzed: int = 0
    reason_no_trade: str = ""
    # Trade recommendation (if any)
    recommendation: dict | None = None
    # Full decision trace
    all_scored_news: list[dict] = Field(default_factory=list)
    rejection_log: list[dict] = Field(default_factory=list)
    decision_summary: str | None = None
    learning_state: dict | None = None
    market_context: dict | None = None


class JournalEntry(BaseModel):
    """Daily journal entry for a single trade — generated at 22:00 CET."""
    schema_version: int = 3  # (#42) v3: ML learning fields
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
    binary_event_warning: str | None = None  # (#24)
    # ── v3: Full decision trace
    raw_claude_score: float | None = None       # Score brut Claude avant learning
    learning_multiplier: float | None = None    # Multiplicateur learning appliqué
    all_scored_news: list[dict] | None = None   # Toutes les news scorées (Claude reasoning)
    rejection_log: list[dict] | None = None     # Pourquoi les autres candidats ont été rejetés
    decision_summary: str | None = None         # Résumé de la décision de sélection
    learning_state: dict | None = None          # État du learning au moment du scan
    # ── P1-#13: Contextual features
    vix_at_trade: float | None = None
    market_regime: str | None = None
    # ── P1-#6: Transmission delay accuracy
    predicted_transmission_delay: int | None = None
    actual_pricing_time_hours: float | None = None
    delay_accuracy: float | None = None


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
