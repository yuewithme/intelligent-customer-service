import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["API_AUTH_ENABLED"] = "false"
os.environ["EMBEDDING_PROVIDER"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["QDRANT_URL"] = ""
os.environ["RAG_KNOWLEDGE_ENABLED"] = "false"
os.environ["CHAT_LOG_ENABLED"] = "false"
