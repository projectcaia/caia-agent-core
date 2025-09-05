class DecisionEngine:
    def __init__(self):
        self.capabilities = ["analyze_intent", "determine_priority", "suggest_workflow"]

    async def decide(self, message: str, context: dict, trigger_type: str, metadata: dict) -> dict:
        return {
            "intent": "general",
            "priority": 2,
            "suggested_workflow": "general-handler",
            "actions": [{"type": "process", "intent": "general"}],
            "confidence": 0.8
        }

    def get_capabilities(self):
        return self.capabilities
