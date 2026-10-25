"""Host the user's own installed Claude Code (`claude -p`, stream-json) as a Mises agent."""
from .detect import (AuthStatus, ClaudeInfo, LoginHandle, auth_status, find_claude, logout,
                     start_login)
from .session import MODEL_ALIASES, SOURCE, ClaudeCodeSession, model_alias

__all__ = ["AuthStatus", "ClaudeInfo", "ClaudeCodeSession", "LoginHandle", "MODEL_ALIASES",
           "SOURCE", "auth_status", "find_claude", "logout", "model_alias", "start_login"]
