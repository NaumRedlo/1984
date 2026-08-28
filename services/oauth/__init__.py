"""osu! OAuth: the link flow's HTTP server, and the tokens it leaves behind.

Deliberately empty. Importing the server here would mean that asking for a
token — `services.oauth.token_manager`, which needs nothing but the database —
also builds the callback server, and through it the miniapp and the bot
handlers the miniapp reads settings from. Those handlers ask for
`has_oauth` on the way up, so the package would be halfway through its own
import when they got there. Reach for `services.oauth.server` and
`services.oauth.token_manager` by name; every caller already does.
"""
