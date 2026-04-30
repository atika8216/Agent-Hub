from pathlib import Path

app_name = "scgp-agent-hub"
app_entrypoint = "scgp_agent_hub.backend.app:app"
app_slug = "scgp_agent_hub"
api_prefix = "/api/v1"
dist_dir = Path(__file__).parent / "__dist__"