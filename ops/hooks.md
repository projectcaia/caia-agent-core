# Auto Ops Hooks

## pre-commit
- run tests
- ensure README env/health sections

## post-merge
- ping endpoints: /health, /mcp, /docs
- append summary to logs/auto_ops.log