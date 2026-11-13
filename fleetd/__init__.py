"""fleetd: per-machine agent that exposes this Mac's Claude Code sessions."""

# Bumped whenever fleetd gains endpoints other machines rely on, so the dashboard
# can tell that a peer runs an older Fleet (1-2 sessions view/control, 3 keep-awake,
# 4 handoff, 5 api_level itself, 6 app support: hello, join, gossip, settings, update).
API_LEVEL = 8
