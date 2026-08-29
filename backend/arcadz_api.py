"""
ArcadZ Music API
Ponte HTTP/WebSocket entre o bot Discord e o painel web.

Arquivo:
    backend/arcadz_api.py

Requisitos:
    fastapi
    uvicorn[standard]

Variáveis de ambiente:
    ARCADZ_API_TOKEN
    ARCADZ_ORIGINS
    PORT
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware


# ============================================================
# CONFIGURAÇÃO
# ============================================================

API_TOKEN = os.getenv("ARCADZ_API_TOKEN", "")

ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ARCADZ_ORIGINS",
        "https://arcadz-hub.lovable.app,http://localhost:8080",
    ).split(",")
    if origin.strip()
]


# ============================================================
# BRIDGE
# ============================================================

class Bridge:
    """
    Interface entre a API e o sistema de música do bot.

    O main.py vai criar uma implementação dessa classe
    usando o MusicPlayer real do bot.
    """

    async def player(self, guild_id: int) -> Dict[str, Any]:
        raise NotImplementedError

    async def queue(self, guild_id: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def history(self, guild_id: int) -> List[Dict[str, Any]]:
        return []

    async def search(self, query: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def play(
        self,
        guild_id: int,
        url: str | None,
        query: str | None,
        next_: bool,
    ) -> None:
        raise NotImplementedError

    async def pause(self, guild_id: int) -> None:
        raise NotImplementedError

    async def resume(self, guild_id: int) -> None:
        raise NotImplementedError

    async def skip(self, guild_id: int) -> None:
        raise NotImplementedError

    async def stop(self, guild_id: int) -> None:
        raise NotImplementedError

    async def shuffle(self, guild_id: int) -> None:
        raise NotImplementedError

    async def loop(self, guild_id: int, mode: str) -> None:
        raise NotImplementedError

    async def volume(self, guild_id: int, value: int) -> None:
        raise NotImplementedError

    async def remove(self, guild_id: int, index: int) -> None:
        raise NotImplementedError

    async def reorder(
        self,
        guild_id: int,
        frm: int,
        to: int,
    ) -> None:
        raise NotImplementedError


# ============================================================
# FORMATO DE TRACK
# ============================================================

def track(
    id: str,
    title: str,
    channel: str,
    thumbnail: str | None,
    duration: int | None,
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


# ============================================================
# API
# ============================================================

class ArcadzAPI:

    def __init__(self, bot, bridge: Bridge):

        self.bot = bot
        self.bridge = bridge

        # Guild ID -> WebSockets conectados
        self.sockets: Dict[str, List[WebSocket]] = {}

        self.app = FastAPI(
            title="ArcadZ Music API",
            version="1.0.0",
        )

        # ----------------------------------------------------
        # CORS
        # ----------------------------------------------------

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self._routes()

    # ========================================================
    # AUTENTICAÇÃO
    # ========================================================

    def _auth(self, request: Request) -> None:

        # Se nenhum token foi configurado,
        # a API fica sem autenticação.
        if not API_TOKEN:
            return

        received_token = request.headers.get(
            "x-arcadz-token"
        )

        if received_token != API_TOKEN:

            raise HTTPException(
                status_code=401,
                detail="invalid token",
            )

    # ========================================================
    # WEBSOCKET BROADCAST
    # ========================================================

    async def broadcast(
        self,
        guild_id: int | str,
        event: str,
        data: Dict[str, Any] | None = None,
    ):

        """
        Envia um evento em tempo real para o painel.

        Exemplos:

            await api.broadcast(
                guild.id,
                "track_started",
                {
                    "title": "Minha música"
                }
            )

        """

        guild_key = str(guild_id)

        payload = {
            "event": event,
            "data": data or {},
        }

        sockets = list(
            self.sockets.get(
                guild_key,
                [],
            )
        )

        for websocket in sockets:

            try:

                await websocket.send_json(
                    payload
                )

            except Exception:

                try:

                    self.sockets[
                        guild_key
                    ].remove(websocket)

                except ValueError:
                    pass

    # ========================================================
    # ROTAS
    # ========================================================

    def _routes(self):

        app = self.app
        bridge = self.bridge

        # ====================================================
        # HEALTH
        # ====================================================

        @app.get("/api/health")
        async def health():

            return {
                "ok": True,
                "bot": (
                    str(self.bot.user)
                    if self.bot.user
                    else None
                ),
            }

        # ====================================================
        # ME
        # ====================================================

        @app.get("/api/me")
        async def me(
            request: Request,
        ):

            self._auth(request)

            user = self.bot.user

            if not user:

                raise HTTPException(
                    status_code=503,
                    detail="Bot ainda não está conectado.",
                )

            return {
                "id": str(user.id),
                "username": user.name,
                "displayName": user.display_name,
                "avatar": (
                    user.display_avatar.url
                    if user.display_avatar
                    else None
                ),
            }

        # ====================================================
        # GUILDS
        # ====================================================

        @app.get("/api/guilds")
        async def guilds(
            request: Request,
        ):

            self._auth(request)

            result = []

            for guild in self.bot.guilds:

                voice = guild.voice_client

                connected = bool(
                    voice
                    and voice.is_connected()
                )

                voice_channel = (
                    voice.channel
                    if voice
                    and voice.channel
                    else None
                )

                listeners = []

                if voice_channel:

                    for member in voice_channel.members:

                        if member.bot:
                            continue

                        listeners.append(
                            {
                                "id": str(member.id),
                                "name": member.display_name,
                                "avatar": (
                                    member.display_avatar.url
                                ),
                                "speaking": False,
                            }
                        )

                result.append(
                    {
                        "id": str(guild.id),
                        "name": guild.name,
                        "icon": (
                            guild.icon.url
                            if guild.icon
                            else None
                        ),
                        "color": "#7C5CFF",
                        "botConnected": connected,
                        "voiceChannel": (
                            voice_channel.name
                            if voice_channel
                            else None
                        ),
                        "listeners": listeners,
                    }
                )

            return result

        # ====================================================
        # PLAYER
        # ====================================================

        @app.get("/api/guilds/{gid}/player")
        async def get_player(
            gid: int,
            request: Request,
        ):

            self._auth(request)

            return await bridge.player(gid)

        # ====================================================
        # QUEUE
        # ====================================================

        @app.get("/api/guilds/{gid}/queue")
        async def get_queue(
            gid: int,
            request: Request,
        ):

            self._auth(request)

            return await bridge.queue(gid)

        # ====================================================
        # HISTORY
        # ====================================================

        @app.get("/api/guilds/{gid}/history")
        async def get_history(
            gid: int,
            request: Request,
        ):

            self._auth(request)

            return await bridge.history(gid)

        # ====================================================
        # SEARCH
        # ====================================================

        @app.get("/api/search")
        async def search(
            q: str,
            request: Request,
        ):

            self._auth(request)

            return await bridge.search(q)

        # ====================================================
        # PLAY
        # ====================================================

        @app.post("/api/guilds/{gid}/play")
        async def play(
            gid: int,
            request: Request,
        ):

            self._auth(request)

            try:

                body = await request.json()

            except Exception:

                body = {}

            await bridge.play(
                gid,
                body.get("url"),
                body.get("query"),
                bool(
                    body.get("next")
                ),
            )

            return {
                "ok": True
            }

        # ====================================================
        # AÇÕES
        # ====================================================

        @app.post(
            "/api/guilds/{gid}/{action}"
        )
        async def simple_action(
            gid: int,
            action: str,
            request: Request,
        ):

            self._auth(request)

            try:

                body = await request.json()

            except Exception:

                body = {}

            # -----------------------------------------------
            # PAUSE
            # -----------------------------------------------

            if action == "pause":

                await bridge.pause(gid)

            # -----------------------------------------------
            # RESUME
            # -----------------------------------------------

            elif action == "resume":

                await bridge.resume(gid)

            # -----------------------------------------------
            # SKIP
            # -----------------------------------------------

            elif action == "skip":

                await bridge.skip(gid)

            # -----------------------------------------------
            # STOP
            # -----------------------------------------------

            elif action == "stop":

                await bridge.stop(gid)

            # -----------------------------------------------
            # SHUFFLE
            # -----------------------------------------------

            elif action == "shuffle":

                await bridge.shuffle(gid)

            # -----------------------------------------------
            # LOOP
            # -----------------------------------------------

            elif action == "loop":

                await bridge.loop(
                    gid,
                    body.get(
                        "mode",
                        "off",
                    ),
                )

            # -----------------------------------------------
            # VOLUME
            # -----------------------------------------------

            elif action == "volume":

                value = int(
                    body.get(
                        "volume",
                        50,
                    )
                )

                value = max(
                    0,
                    min(
                        100,
                        value,
                    ),
                )

                await bridge.volume(
                    gid,
                    value,
                )

            # -----------------------------------------------
            # AÇÃO DESCONHECIDA
            # -----------------------------------------------

            else:

                raise HTTPException(
                    status_code=404,
                    detail="unknown action",
                )

            return {
                "ok": True
            }

        # ====================================================
        # REMOVE DA FILA
        # ====================================================

        @app.delete(
            "/api/guilds/{gid}/queue/{index}"
        )
        async def remove_from_queue(
            gid: int,
            index: int,
            request: Request,
        ):

            self._auth(request)

            await bridge.remove(
                gid,
                index,
            )

            return {
                "ok": True
            }

        # ====================================================
        # REORDENAR FILA
        # ====================================================

        @app.post(
            "/api/guilds/{gid}/queue/reorder"
        )
        async def reorder_queue(
            gid: int,
            request: Request,
        ):

            self._auth(request)

            body = await request.json()

            if "from" not in body:
                raise HTTPException(
                    status_code=400,
                    detail="missing 'from'",
                )

            if "to" not in body:
                raise HTTPException(
                    status_code=400,
                    detail="missing 'to'",
                )

            await bridge.reorder(
                gid,
                int(body["from"]),
                int(body["to"]),
            )

            return {
                "ok": True
            }

        # ====================================================
        # WEBSOCKET
        # ====================================================

        @app.websocket(
            "/ws/guild/{gid}"
        )
        async def websocket_guild(
            websocket: WebSocket,
            gid: str,
            token: Optional[str] = None,
        ):

            # ------------------------------------------------
            # AUTENTICAÇÃO DO WEBSOCKET
            # ------------------------------------------------

            if API_TOKEN:

                if token != API_TOKEN:

                    await websocket.close(
                        code=4401
                    )

                    return

            # ------------------------------------------------
            # ACEITAR CONEXÃO
            # ------------------------------------------------

            await websocket.accept()

            self.sockets.setdefault(
                gid,
                [],
            ).append(
                websocket
            )

            print(
                f"[API] WebSocket conectado: guild {gid}"
            )

            try:

                while True:

                    # Mantém a conexão viva.
                    # O painel pode mandar mensagens
                    # de heartbeat por aqui.

                    await websocket.receive_text()

            except WebSocketDisconnect:

                pass

            except Exception as error:

                print(
                    f"[API] WebSocket erro: {error}"
                )

            finally:

                if websocket in self.sockets.get(
                    gid,
                    [],
                ):

                    self.sockets[gid].remove(
                        websocket
                    )

                print(
                    f"[API] WebSocket desconectado: guild {gid}"
                )

    # ========================================================
    # INICIAR SERVIDOR
    # ========================================================

    def start(self) -> asyncio.Task:

        import uvicorn

        port = int(
            os.getenv(
                "PORT",
                "8000",
            )
        )

        config = uvicorn.Config(
            self.app,
            host="0.0.0.0",
            port=port,
            log_level="info",
        )

        server = uvicorn.Server(
            config
        )

        print(
            f"[API] Iniciando FastAPI na porta {port}"
        )

        return asyncio.create_task(
            server.serve()
        )
