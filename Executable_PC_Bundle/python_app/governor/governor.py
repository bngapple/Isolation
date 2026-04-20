from __future__ import annotations

from datetime import datetime

from governor.risk_profile import RiskProfile
from utils.logger import get_logger


PROMPT_TEMPLATE = """You are a risk manager for a funded NQ futures trading account.

ACCOUNT STATE:
- Balance: $25,000 | EOD max loss: $1,000 | Killswitch: -$750
- Session P&L so far: ${current_pnl:.2f}
- Current mode: {current_mode}

TODAY'S ECONOMIC CALENDAR (USD, high/medium impact only):
{calendar_events}

LIVE NEWS (last 30 minutes):
{live_news_summary}

HISTORICAL PATTERN CONTEXT:
On days with similar news profiles (from our database, 2020-present):
{historical_context}

GOVERNOR TRACK RECORD (recent decisions scored):
{recent_scored_decisions}

MARKET CONDITIONS RIGHT NOW:
- Time (ET): {time}
- MNQ 30-min range: {range_30m} points
- ATR(14) on 15m: {atr} | 50-bar median: {atr_median}
- Regime: {regime}

SCORING CONTEXT:
A GOOD day is one with HIGH volatility — the RSI strategy thrives on large directional moves.
A BAD day is one with low volatility or consolidation — the strategy gets chopped.
Your job is to run FULL SIZE on high-volatility days and REDUCE or HALT on low-volatility days.

Output ONLY valid JSON:
{{
  "mode": "NORMAL" | "REDUCED" | "DEFENSIVE" | "HALTED",
  "size_multiplier": <0.33 to 1.0>,
  "reason": "<one sentence max>"
}}

Sizing guide: 0.33 = 1 contract, 0.67 = 2 contracts, 1.0 = 3 contracts
"""


class Governor:
    def __init__(self, db, bridge, claude_client):
        self.db = db
        self.bridge = bridge
        self.claude_client = claude_client
        self.logger = get_logger("governor")

    def run_premarket(self, calendar_events, current_pnl=0.0) -> RiskProfile:
        return self._run_decision("premarket", calendar_events, "No live news", current_pnl)

    def run_intraday_update(self, trigger: str, news_summary: str, current_pnl: float) -> RiskProfile:
        return self._run_decision(trigger, [], news_summary, current_pnl)

    def _run_decision(self, trigger: str, calendar_events, live_news_summary: str, current_pnl: float) -> RiskProfile:
        current_mode = self.bridge.current_profile.mode if self.bridge.current_profile else "NORMAL"
        prompt = PROMPT_TEMPLATE.format(
            current_pnl=current_pnl,
            current_mode=current_mode,
            calendar_events=self._format_events(calendar_events),
            live_news_summary=live_news_summary,
            historical_context=self._get_historical_context(calendar_events),
            recent_scored_decisions=self._get_recent_decisions(),
            time=datetime.now().strftime("%H:%M:%S"),
            range_30m="unknown",
            atr="unknown",
            atr_median="unknown",
            regime="UNKNOWN",
        )
        profile = self.claude_client.decide(prompt)
        self.db.execute(
            """
            INSERT INTO governor_decisions
            (decision_datetime, trigger, mode_decided, size_multiplier, reason, session_pnl_at_decision, claude_prompt, claude_response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(),
                trigger,
                profile.mode,
                profile.size_multiplier,
                profile.reason,
                current_pnl,
                prompt,
                profile.to_json_line().strip(),
            ),
        )
        self.bridge.push_profile(profile)
        self.logger.info("decision made", extra={"data": {"trigger": trigger, "mode": profile.mode, "size_multiplier": profile.size_multiplier}})
        return profile

    def _format_events(self, events: list) -> str:
        if not events:
            return "No qualifying events"
        return "\n".join(f"- {e.get('datetime')} | {e.get('impact')} | {e.get('name')}" for e in events)

    def _get_historical_context(self, events: list) -> str:
        rows = []
        for event in events:
            rows.extend(
                self.db.fetchall(
                    """
                    SELECT e.event_name, e.event_datetime, e.surprise_direction, e.surprise_magnitude,
                           r.session_total_range, r.volatility_class, r.post_event_range_2h,
                           s.strategy_net_pnl, s.volatility_class AS day_volatility
                    FROM economic_events e
                    JOIN market_reactions r ON r.event_id = e.id
                    JOIN session_summaries s ON s.session_date = date(e.event_datetime)
                    WHERE e.event_name LIKE ?
                    ORDER BY e.event_datetime DESC
                    LIMIT 5
                    """,
                    (f"%{event.get('name', '')}%",),
                )
            )
        if not rows:
            return "No similar historical events found"
        return "\n".join(
            f"- {row['event_datetime']}: {row['event_name']} | range={row['session_total_range']} | vol={row['day_volatility']} | pnl={row['strategy_net_pnl']}"
            for row in rows[:10]
        )

    def _get_recent_decisions(self, limit=10) -> str:
        rows = self.db.fetchall(
            """
            SELECT decision_datetime, mode_decided, reason, outcome_good, outcome_session_range, outcome_strategy_pnl
            FROM governor_decisions
            WHERE outcome_scored = 1
            ORDER BY decision_datetime DESC
            LIMIT ?
            """,
            (limit,),
        )
        if not rows:
            return "No scored decisions yet"
        return "\n".join(
            f"- {row['decision_datetime']}: {row['mode_decided']} | good={row['outcome_good']} | range={row['outcome_session_range']} | pnl={row['outcome_strategy_pnl']} | {row['reason']}"
            for row in rows
        )
