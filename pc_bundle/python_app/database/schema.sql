CREATE TABLE IF NOT EXISTS economic_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name TEXT NOT NULL,
    event_datetime TEXT NOT NULL,
    currency TEXT,
    impact TEXT,
    actual REAL,
    forecast REAL,
    previous REAL,
    surprise_magnitude REAL,
    surprise_direction TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER REFERENCES economic_events(id),
    pre_event_range_30m REAL,
    post_event_range_30m REAL,
    post_event_range_2h REAL,
    initial_direction TEXT,
    max_adverse_move_2h REAL,
    max_favorable_move_2h REAL,
    session_total_range REAL,
    mnq_atr_at_event REAL,
    volatility_class TEXT
);

CREATE TABLE IF NOT EXISTS governor_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_datetime TEXT NOT NULL,
    trigger TEXT,
    mode_decided TEXT NOT NULL,
    size_multiplier REAL,
    reason TEXT,
    session_pnl_at_decision REAL,
    outcome_scored INTEGER DEFAULT 0,
    outcome_good INTEGER,
    outcome_session_range REAL,
    outcome_strategy_pnl REAL,
    outcome_notes TEXT,
    claude_prompt TEXT,
    claude_response TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS live_news_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_datetime TEXT NOT NULL,
    headline TEXT,
    source TEXT,
    classified_impact TEXT,
    classified_direction TEXT,
    action_taken TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date TEXT UNIQUE NOT NULL,
    session_range_points REAL,
    session_atr REAL,
    volatility_class TEXT,
    strategy_gross_pnl REAL,
    strategy_net_pnl REAL,
    trade_count INTEGER,
    win_count INTEGER,
    governor_mode_distribution TEXT,
    news_events_count INTEGER,
    killswitch_triggered INTEGER DEFAULT 0,
    eod_backtest_run INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS config_values (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
