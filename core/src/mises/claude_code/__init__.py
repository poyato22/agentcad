"""Host the user's own installed Claude Code (`claude -p`, stream-json) as a Mises agent."""
from .detect import AuthStatus, ClaudeInfo, LoginHandle, auth_status, find_claude, start_login
from .session import ClaudeCodeSession

__all__ = ["AuthStatus", "ClaudeInfo", "ClaudeCodeSession", "LoginHandle", "auth_status",
           "find_claude", "start_login"]
