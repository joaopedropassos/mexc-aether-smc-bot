"""
state_manager.py - Optional more advanced state persistence layer (future extension).
Current main.py uses simple JSON. You can expand this.
"""
import json
import os
from typing import Any, Dict


class StateManager:
    def __init__(self, path: str = "state/bot_state.json"):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def save(self, data: Dict[str, Any]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def load(self) -> Dict[str, Any]:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
