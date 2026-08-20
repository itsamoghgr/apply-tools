"""Job Board — career-page monitor.

A watchlist-driven counterpart to the lead-generation pipeline in
`agent_server/orchestrator/`. Where that pipeline DISCOVERS companies, this one
only ever visits career pages the user explicitly added: every 3 hours it
re-scrapes each watched board, keeps postings matching the user's target roles,
and stores the ones it has never seen before. Every 6 hours a digest email
reports what is new.

Design docs: docs/job_board_design.md, docs/job_board_backend.md.
"""
