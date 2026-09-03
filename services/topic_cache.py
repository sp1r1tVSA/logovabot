"""
TopicCache: Thread-safe high-performance in-memory cache for Telegram forum topics.
Enforces multi-group isolation by indexing on (group_chat_id, message_thread_id)
and providing reverse lookups on (division_id, topic_type).
"""

import threading
import logging
import database

logger = logging.getLogger(__name__)


class TopicCache:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
                cls._instance._by_topic = {}      # (group_chat_id, message_thread_id) -> binding dict
                cls._instance._by_division = {}   # (division_id, topic_type) -> binding dict
            return cls._instance

    def _ensure_loaded(self):
        if not self._initialized:
            self.reload_cache()

    def reload_cache(self) -> None:
        """Reload all division topic bindings from SQLite database."""
        with self._lock:
            self._by_topic.clear()
            self._by_division.clear()
            try:
                rows = database.get_all_division_topics()
                for r in rows:
                    chat_id = r.get("group_chat_id")
                    thread_id = r["message_thread_id"]
                    div_id = r["division_id"]
                    top_type = database.normalize_topic_type(r["topic_type"])
                    
                    binding = {
                        "id": r.get("id"),
                        "division_id": div_id,
                        "division_name": r.get("division_name") or f"Дивизион {div_id}",
                        "division_code": r.get("division_code") or f"DIV_{div_id}",
                        "topic_type": top_type,
                        "group_chat_id": chat_id,
                        "message_thread_id": thread_id
                    }
                    
                    # 1. Forward topic lookup
                    self._by_topic[(chat_id, thread_id)] = binding
                    if chat_id is not None:
                        # Also allow fallback if chat_id isn't provided during read
                        self._by_topic.setdefault((None, thread_id), binding)
                    
                    # 2. Reverse lookup by (division_id, topic_type)
                    self._by_division[(div_id, top_type)] = {
                        "group_chat_id": chat_id,
                        "message_thread_id": thread_id,
                        "division_name": binding["division_name"],
                        "division_code": binding["division_code"]
                    }
                self._initialized = True
                logger.info(f"TopicCache reloaded successfully: {len(self._by_topic)} bindings loaded.")
            except Exception as e:
                logger.exception(f"Failed to reload TopicCache: {e}")

    def get_by_topic(self, group_chat_id: int | None, message_thread_id: int) -> dict | None:
        """
        Lookup division and topic type by (group_chat_id, message_thread_id).
        Returns copy of binding dict or None.
        """
        with self._lock:
            self._ensure_loaded()
            if group_chat_id is not None:
                res = self._by_topic.get((group_chat_id, message_thread_id))
                if res:
                    return dict(res)
            # Fallback to chat_id = None if not found or not provided
            res = self._by_topic.get((None, message_thread_id))
            return dict(res) if res else None

    def get_by_division(self, division_id: int, topic_type: str) -> dict | None:
        """
        Reverse lookup Telegram topic by (division_id, topic_type).
        Returns {'group_chat_id': ..., 'message_thread_id': ...} or None.
        """
        with self._lock:
            self._ensure_loaded()
            norm_type = database.normalize_topic_type(topic_type)
            res = self._by_division.get((division_id, norm_type))
            return dict(res) if res else None

    def set_topic(
        self, 
        division_id: int, 
        group_chat_id: int, 
        message_thread_id: int, 
        topic_type: str,
        division_name: str = "",
        division_code: str = ""
    ) -> None:
        """Point update cache after successful database transaction."""
        with self._lock:
            self._ensure_loaded()
            norm_type = database.normalize_topic_type(topic_type)
            
            # Remove any previous topic bound to this (division_id, norm_type)
            old_div_entry = self._by_division.get((division_id, norm_type))
            if old_div_entry:
                old_chat = old_div_entry.get("group_chat_id")
                old_thread = old_div_entry.get("message_thread_id")
                self._by_topic.pop((old_chat, old_thread), None)
                if old_chat is not None:
                    self._by_topic.pop((None, old_thread), None)

            # Remove any previous binding on this (group_chat_id, message_thread_id)
            old_topic_entry = self._by_topic.get((group_chat_id, message_thread_id))
            if old_topic_entry:
                self._by_division.pop((old_topic_entry["division_id"], old_topic_entry["topic_type"]), None)

            binding = {
                "division_id": division_id,
                "division_name": division_name or f"Дивизион {division_id}",
                "division_code": division_code,
                "topic_type": norm_type,
                "group_chat_id": group_chat_id,
                "message_thread_id": message_thread_id
            }

            self._by_topic[(group_chat_id, message_thread_id)] = binding
            self._by_topic[(None, message_thread_id)] = binding
            self._by_division[(division_id, norm_type)] = {
                "group_chat_id": group_chat_id,
                "message_thread_id": message_thread_id,
                "division_name": binding["division_name"],
                "division_code": binding["division_code"]
            }

    def remove_topic(self, group_chat_id: int, message_thread_id: int) -> None:
        """Point remove topic after database deletion."""
        with self._lock:
            self._ensure_loaded()
            entry = self._by_topic.pop((group_chat_id, message_thread_id), None)
            self._by_topic.pop((None, message_thread_id), None)
            if entry:
                div_id = entry["division_id"]
                top_type = entry["topic_type"]
                self._by_division.pop((div_id, top_type), None)

    def get_division_topics_summary(self, division_id: int) -> dict[str, dict]:
        """Return dict of {topic_type: {group_chat_id, message_thread_id}} for given division."""
        with self._lock:
            self._ensure_loaded()
            summary = {}
            for t in database.PRIMARY_DIVISION_TOPICS:
                entry = self._by_division.get((division_id, t))
                if entry:
                    summary[t] = dict(entry)
            return summary


# Global singleton instance
topic_cache = TopicCache()
