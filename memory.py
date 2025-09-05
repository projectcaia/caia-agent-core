from datetime import datetime

class MemoryManager:
    def __init__(self):
        self.memories = []

    async def recall(self, query: str) -> dict:
        return {"memories": self.memories[-5:], "query": query}

    async def store(self, memory: dict) -> bool:
        memory["stored_at"] = datetime.now().isoformat()
        self.memories.append(memory)
        return True

    async def count(self) -> int:
        return len(self.memories)
