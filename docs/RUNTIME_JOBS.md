# Runtime jobs

The integrated registry contains `pre_market`, `market_outlook`, `post_market`,
`technical_snapshot`, `broker_summary`, `broker_multi_day`,
`universe_selection`, `candidate_selection`, `final_watchlist`,
`final_decision`, `telegram_delivery`, and `job_status`.

Final Watchlist requires current, successful status records for market outlook,
post market/technical snapshot, broker summary, and broker multi-day. The
dependency validator rejects missing, stale trade dates, or mismatched config
versions.

