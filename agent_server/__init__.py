"""Lead-generation agent service.

A standalone uvicorn process (port 8001) that discovers startups by reading the
open web, researches funding + founder, verifies, and pushes clean verified leads
to the platform backend. Stops after 50 verified leads.

Agency lives in the leaves (discovery, research). Determinism in the trunk
(orchestrator loop, dedup, verification, delivery).
"""

__version__ = "0.1.0"
