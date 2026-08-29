"""
ArcadZ Music — ponte HTTP/WebSocket entre o bot (discord.py) e o painel web.

Como usar (resumo):
    from arcadz_api import ArcadzAPI, Bridge

    api = ArcadzAPI(bot, bridge=MinhaBridge(bot))
    api.start()          # sobe o FastAPI numa task junto do bot

Requisitos:
    pip install fastapi uvicorn

Variáveis de ambiente:
    ARCADZ_API_TOKEN   token compartilhado com o painel (recomendado)
    ARCADZ_ORIGINS     origens permitidas no CORS, separadas por vírgula
    PORT               porta HTTP (o Railway injeta automaticamente)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

API_TOKEN = os.getenv("ARCADZ_API_TOKEN", "")
ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ARCADZ_ORIGINS",
        "https://arcadz-hub.lovable.app,http://localhost:8080",
    ).split(",")
    if o.strip()
]


# --------------------------------------------------------------------------
# Bridge: você implementa estes métodos ligando-os ao seu cog de música.
# --------------------------------------------------------------------------
class Bridge:
    """Adapte cada método ao seu bot. Todos são async."""

    async def player(self, guild_id: int) -> Dict[str, Any]:
        """{current, position, playing, volume, shuffle, loop, connected}"""
        raise NotImplementedError

    async def queue(self, guild_id: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def history(self, guild_id: int) -> List[Dict[str, Any]]:
        return []

    async def search(self, query: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def play(self, guild_id: int, url: str | None, query: str | None, next_: bool) -> None:
        raise NotImplementedError

    async def pause(self, guild_id: int) -> None: ...
    async def resume(self, guild_id: int) -> None: ...
    async def skip(self, guild_id: int) -> None: ...
    async def stop(self, guild_id: int) -> None: ...
    async def shuffle(self, guild_id: int) -> None: ...
    async def loop(self, guild_id: int, mode: str) -> None: ...
    async def volume(self, guild_id: int, value: int) -> None: ...
    async def remove(self, guild_id: int, index: int) -> None: ...
    async def reorder(self, guild_id: int, frm: int, to: int) -> None: ...


# --------------------------------------------------------------------------
# Formatos que o painel espera
# --------------------------------------------------------------------------
def track(
    id: str,
    title: str,
    channel: str,
    thumbnail: str,
    duration: int,
    url: str,
    requested_by: str = "",
) -> Dict[str, Any]:
    return {
        "id": id,
        "title": title,
        "channel": channel,
        "thumbnail": thumbnail,
        "duration": duration,
        "url": url,
        "requestedBy": requested_by,
    }


class ArcadzAPI:
    def __init__(self, bot, bridge: Bridge):
        self.bot = bot
        self.bridge = bridge
        self.sockets: Dict[str, List[WebSocket]] = {}
        self.app = FastAPI(title="ArcadZ Music API")
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._routes()

    # ---------------- auth ----------------
    def _auth(self, request: Request) -> None:
        if API_TOKEN and request.headers.get("x-arcadz-token") != API_TOKEN:
            raise HTTPException(status_code=401, detail="invalid token")

    # ---------------- broadcast ----------------
    async def broadcast(self, guild_id: int | str, event: str, data: Dict[str, Any] | None = None):
        """Chame do bot quando algo mudar (track started, pause, volume...)."""
        payload = {"event": event, "data": data or {}}
        for ws in list(self.sockets.get(str(guild_id), [])):
            try:
                await ws.send_json(payload)
            except Exception:
                self.sockets[str(guild_id)].remove(ws)

    # ---------------- routes ----------------
    def _routes(self):
        app, br = self.app, self.bridge

        @app.get("/api/health")
        async def health():
            return {"ok": True, "bot": str(self.bot.user) if self.bot.user else None}

        @app.get("/api/me")
        async def me(request: Request):
            self._auth(request)
            u = self.bot.user
            return {
                "id": str(u.id),
                "username": u.name,
                "displayName": u.display_name,
                "avatar": u.display_avatar.url if u else None,
            }

        @app.get("/api/guilds")
        async def guilds(request: Request):
            self._auth(request)
            out = []
            for g in self.bot.guilds:
                vc = g.voice_client
                out.append(
                    {
                        "id": str(g.id),
                        "name": g.name,
                        "icon": g.icon.url if g.icon else None,
                        "color": "#7C5CFF",
                        "botConnected": bool(vc and vc.is_connected()),
                        "voiceChannel": vc.channel.name if vc and vc.channel else None,
                        "listeners": [
                            {
                                "id": str(m.id),
                                "name": m.display_name,
                                "avatar": m.display_avatar.url,
                                "speaking": False,
                            }
                            for m in (vc.channel.members if vc and vc.channel else [])
                            if not m.bot
                        ],
                    }
                )
            return out

        @app.get("/api/guilds/{gid}/player")
        async def get_player(gid: int, request: Request):
            self._auth(request)
            return await br.player(gid)

        @app.get("/api/guilds/{gid}/queue")
        async def get_queue(gid: int, request: Request):
            self._auth(request)
            return await br.queue(gid)

        @app.get("/api/guilds/{gid}/history")
        async def get_history(gid: int, request: Request):
            self._auth(request)
            return await br.history(gid)

        @app.get("/api/search")
        async def search(q: str, request: Request):
            self._auth(request)
            return await br.search(q)

        @app.post("/api/guilds/{gid}/play")
        async def play(gid: int, request: Request):
            self._auth(request)
            body = await request.json()
            await br.play(gid, body.get("url"), body.get("query"), bool(body.get("next")))
            return {"ok": True}

        @app.post("/api/guilds/{gid}/{action}")
        async def simple(gid: int, action: str, request: Request):
            self._auth(request)
            body: Dict[str, Any] = {}
            try:
                body = await request.json()
            except Exception:
                pass
            if action == "pause":
                await br.pause(gid)
            elif action == "resume":
                await br.resume(gid)
            elif action == "skip":
                await br.skip(gid)
            elif action == "stop":
                await br.stop(gid)
            elif action == "shuffle":
                await br.shuffle(gid)
            elif action == "loop":
                await br.loop(gid, body.get("mode", "off"))
            elif action == "volume":
                await br.volume(gid, int(body.get("volume", 50)))
            else:
                raise HTTPException(status_code=404, detail="unknown action")
            return {"ok": True}

        @app.delete("/api/guilds/{gid}/queue/{index}")
        async def remove(gid: int, index: int, request: Request):
            self._auth(request)
            await br.remove(gid, index)
            return {"ok": True}

        @app.post("/api/guilds/{gid}/queue/reorder")
        async def reorder(gid: int, request: Request):
            self._auth(request)
            body = await request.json()
            await br.reorder(gid, int(body["from"]), int(body["to"]))
            return {"ok": True}

        @app.websocket("/ws/guild/{gid}")
        async def ws_guild(ws: WebSocket, gid: str, token: Optional[str] = None):
            if API_TOKEN and token != API_TOKEN:
                await ws.close(code=4401)
                return
            await ws.accept()
            self.sockets.setdefault(gid, []).append(ws)
            try:
                while True:
                    await ws.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                if ws in self.sockets.get(gid, []):
                    self.sockets[gid].remove(ws)

    # ---------------- runner ----------------
    def start(self) -> asyncio.Task:
        import uvicorn

        config = uvicorn.Config(
            self.app,
            host="0.0.0.0",
            port=int(os.getenv("PORT", "8000")),
            log_level="info",
        )
        server = uvicorn.Server(config)
        return asyncio.create_task(server.serve())
