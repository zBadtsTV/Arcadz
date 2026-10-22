import os
import asyncio
import time
import random
import re
import json
from urllib.request import Request, urlopen
from urllib.parse import quote
from dataclasses import dataclass
from typing import Optional, Any

from dotenv import load_dotenv

# Carrega o .env antes de importar a API, pois arcadz_api.py
# lê ARCADZ_API_TOKEN / ARCADZ_ORIGINS no import.
load_dotenv()

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

try:
    import wavelink
except ImportError:
    wavelink = None

LAVALINK_URI = os.getenv("LAVALINK_URI", "").strip()
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "").strip()
lavalink_ready = False

from backend.arcadz_api import ArcadzAPI, Bridge

# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN não configurado.")


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)



# ============================================================
# YOUTUBE / YT-DLP
# ============================================================

# ============================================================
# YOUTUBE COOKIES
# ============================================================

COOKIE_FILE = "/tmp/cookies.txt"
cookie_data = os.getenv("YOUTUBE_COOKIES", "").strip()

if cookie_data:
    try:
        # Salva o conteúdo completo da variável do Railway
        with open(COOKIE_FILE, "w", encoding="utf-8", newline="\n") as f:
            f.write(cookie_data + "\n")

        if os.path.getsize(COOKIE_FILE) > 0:
            print(
                f"[YouTube] Arquivo de cookies criado "
                f"({os.path.getsize(COOKIE_FILE)} bytes)."
            )
        else:
            print("[YouTube] ERRO: arquivo de cookies vazio.")

    except Exception as e:
        print(f"[YouTube] ERRO ao salvar cookies: {e}")
else:
    print(
        "[YouTube] AVISO: variável YOUTUBE_COOKIES não configurada. "
        "O bot tentará acessar vídeos públicos sem cookies."
    )


# ============================================================
# YT-DLP
# ============================================================

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": False,
    "cachedir": False,
    "socket_timeout": 10,
    "retries": 1,
    "fragment_retries": 1,

    # Provider HTTP do bgutil no mesmo container
    "extractor_args": {
        "youtubepot-bgutilhttp": {
            "base_url": [(os.getenv("BGUTIL_URL") or "http://127.0.0.1:4416").strip()]
        }
    },
}

# Só utiliza cookies se o arquivo foi criado com conteúdo
if cookie_data and os.path.isfile(COOKIE_FILE):
    YTDL_OPTIONS["cookiefile"] = COOKIE_FILE
    print("[YouTube] Uso de cookies habilitado.")
else:
    print("[YouTube] Uso de cookies desabilitado.")


# ============================================================
# FFMPEG
# ============================================================

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}


SPOTIFY_TRACK_RE = re.compile(
    r"https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([A-Za-z0-9]+)"
)

async def resolve_music_query(query: str) -> str:
    match = SPOTIFY_TRACK_RE.search(query)
    if not match:
        return query
    spotify_url = f"https://open.spotify.com/track/{match.group(1)}"
    def fetch_metadata():
        endpoint = "https://open.spotify.com/oembed?url=" + quote(spotify_url, safe="")
        req = Request(endpoint, headers={"User-Agent": "ArcadzBot/1.0"})
        with urlopen(req, timeout=8) as response:
            data = json.load(response)
        title = (data.get("title") or "").strip()
        if not title:
            raise ValueError("Spotify nao retornou o nome da faixa.")
        title = re.sub(r"\s*\|\s*Spotify\s*$", "", title, flags=re.I)
        title = re.sub(r"\s+-\s+song(?: and lyrics)? by\s+", " ", title, flags=re.I)
        return title
    try:
        title = await asyncio.to_thread(fetch_metadata)
    except Exception as exc:
        raise ValueError(
            "Nao foi possivel identificar esta faixa do Spotify. "
            "Tente pesquisar pelo nome e artista."
        ) from exc
    print(f"[ArcadZ] Spotify convertido em pesquisa: {title}")
    return title


# ============================================================
# MUSIC TRACK
# ============================================================

@dataclass
class Track:
    title: str
    url: str
    stream_url: str
    thumbnail: Optional[str]
    duration: Optional[int]
    requester: str
    channel: str = ""
    track_id: str = ""
    lavalink_playable: Any = None


# ============================================================
# API STATE
# ============================================================

arcadz_api: Optional[ArcadzAPI] = None
arcadz_api_task: Optional[asyncio.Task] = None


async def broadcast(guild_id: int, event: str, data: Optional[dict[str, Any]] = None):
    """Envia uma atualização para o painel ArcadZ, se a API estiver ativa."""
    if arcadz_api:
        try:
            await arcadz_api.broadcast(guild_id, event, data or {})
        except Exception as e:
            print(f"[ArcadZ] Erro no broadcast: {e}")


if wavelink is not None:
    class ArcadzLavalinkVoice(wavelink.Player):
        def is_connected(self):
            return self.connected

        def is_playing(self):
            return self.playing

        def is_paused(self):
            return self.paused
else:
    class ArcadzLavalinkVoice(discord.VoiceClient):
        pass


@bot.event
async def on_wavelink_track_end(payload):
    voice = payload.player
    if not voice or not voice.guild:
        return
    player = players.get(voice.guild.id)
    if not player or not player.using_lavalink:
        return
    if player._lavalink_suppress_end:
        player._lavalink_suppress_end = False
        return
    if str(payload.reason).lower() in ("replaced", "cleanup"):
        return
    await player.play_next()


@bot.event
async def on_wavelink_track_exception(payload):
    print(f"[Lavalink] Track exception: {getattr(payload, 'exception', 'unknown')}")


@bot.event
async def on_wavelink_track_stuck(payload):
    print("[Lavalink] Track stuck; skipping.")
    if payload.player:
        await payload.player.skip()


# ============================================================
# MUSIC PLAYER
# ============================================================

class MusicPlayer:

    def __init__(self, guild: discord.Guild):
        self.guild = guild

        self.queue: list[Track] = []
        self.current: Optional[Track] = None
        self.voice: Optional[discord.VoiceClient] = None

        self.volume = 0.5
        self.loop = False

        self.lock = asyncio.Lock()

        # Controle aproximado da posição da música.
        self.started_at: Optional[float] = None
        self.paused_at: Optional[float] = None
        self.paused_total: float = 0.0

        # Histórico das músicas tocadas nesta execução do bot.
        self.history: list[Track] = []
        self.using_lavalink = False
        self._lavalink_suppress_end = False

    # --------------------------------------------------------
    # POSITION
    # --------------------------------------------------------

    def position_seconds(self) -> int:
        if self.using_lavalink and self.voice and self.current:
            return max(0, int(self.voice.position / 1000))
        if not self.current or self.started_at is None:
            return 0

        now = asyncio.get_running_loop().time()

        if self.paused_at is not None:
            now = self.paused_at

        position = now - self.started_at - self.paused_total

        if self.current.duration:
            position = min(position, self.current.duration)

        return max(0, int(position))

    def _reset_position(self):
        self.started_at = asyncio.get_running_loop().time()
        self.paused_at = None
        self.paused_total = 0.0

    # --------------------------------------------------------
    # EXTRACT YOUTUBE
    # --------------------------------------------------------

    async def extract(self, query: str, requester: str):
        query = await resolve_music_query(query)
        if self.using_lavalink:
            started = time.monotonic()
            search = query if query.startswith(("https://", "http://")) else "ytmsearch:" + query
            try:
                matches = await asyncio.wait_for(wavelink.Playable.search(search), timeout=14)
            except Exception as exc:
                print(f"[Lavalink] Busca YouTube falhou: {exc}")
                matches = []
            if not matches and not query.startswith(("http://", "https://")):
                matches = await asyncio.wait_for(
                    wavelink.Playable.search("scsearch:" + query), timeout=12
                )
            if not matches:
                raise ValueError("Lavalink nao encontrou musica. Tente outro link ou pesquisa.")
            item = matches.tracks[0] if hasattr(matches, "tracks") else matches[0]
            print(f"[Lavalink] Faixa resolvida em {time.monotonic()-started:.2f}s")
            return Track(
                title=item.title, url=item.uri or query, stream_url="",
                thumbnail=getattr(item, "artwork", None),
                duration=round(item.length / 1000) if item.length else 0,
                requester=requester, channel=getattr(item, "author", "") or "",
                track_id=str(getattr(item, "identifier", "") or item.uri or query),
                lavalink_playable=item,
            )

        if not query.startswith(("http://", "https://", "ytsearch")):
            query = f"ytsearch1:{query}"
        loop = asyncio.get_running_loop()

        def run():
            last_error = None

            option_sets = [YTDL_OPTIONS]

            # Fallback simples caso um client específico do YouTube
            # falhe. Mantemos cookies e bgutil em todas as tentativas.
            fallback = dict(YTDL_OPTIONS)
            fallback["extractor_args"] = {
                "youtube": {
                    "player_client": ["web"],
                },
                "youtubepot-bgutilhttp": {
                    "base_url": [(os.getenv("BGUTIL_URL") or "http://127.0.0.1:4416").strip()],
                },
            }

            if fallback["extractor_args"] != YTDL_OPTIONS["extractor_args"]:
                option_sets.append(fallback)

            for options in option_sets:
                try:
                    with yt_dlp.YoutubeDL(options) as ydl:
                        info = ydl.extract_info(
                            query,
                            download=False
                        )

                    if "entries" in info:
                        entries = info.get("entries") or []

                        if not entries:
                            raise ValueError("Nenhum resultado encontrado.")

                        info = entries[0]

                    return info
                except Exception as e:
                    last_error = e

            raise last_error or ValueError("Não foi possível extrair a música.")

        started = time.monotonic()
        info = await loop.run_in_executor(None, run)
        print(f"[ArcadZ] Extracao concluida em {time.monotonic() - started:.1f}s")
        return Track(
            title=info.get(
                "title",
                "Desconhecido"
            ),
            url=info.get(
                "webpage_url",
                query
            ),
            stream_url=info["url"],
            thumbnail=info.get(
                "thumbnail"
            ),
            duration=info.get(
                "duration"
            ),
            requester=requester,
            channel=info.get("channel") or info.get("uploader") or "",
            track_id=str(info.get("id") or info.get("webpage_url") or query),
        )

    # --------------------------------------------------------
    # PLAY
    # --------------------------------------------------------

    async def play_next(self):

        if not self.voice:
            return

        if not self.voice.is_connected():
            return

        if self.loop and self.current:
            track = self.current
        else:
            if not self.queue:
                self.current = None
                self.started_at = None
                self.paused_at = None
                self.paused_total = 0.0

                await broadcast(
                    self.guild.id,
                    "queue_updated",
                    {"queue": []}
                )
                return

            track = self.queue.pop(0)
            self.current = track

            # Guarda histórico quando uma nova faixa da fila começa.
            self.history.insert(0, track)
            self.history = self.history[:50]

        self._reset_position()

        if self.using_lavalink:
            try:
                if track.lavalink_playable is None:
                    raise ValueError("Faixa sem dados Lavalink.")
                await self.voice.play(track.lavalink_playable, volume=round(self.volume * 100))
                print(f"[Lavalink] Tocando: {track.title}")
                await broadcast(self.guild.id, "track_started", self.api_current())
                await broadcast(self.guild.id, "queue_updated", {
                    "queue": [self.api_track(t) for t in self.queue]
                })
            except Exception as exc:
                print(f"[Lavalink] Erro ao iniciar {track.title}: {exc}")
                self.current = None
                await self.play_next()
            return

        try:
            # FFmpegPCMAudio starts an ffmpeg subprocess synchronously.
            # Keep subprocess startup off the Discord/FastAPI event loop.
            source = await asyncio.to_thread(
                discord.FFmpegPCMAudio,
                track.stream_url,
                **FFMPEG_OPTIONS
            )

            source = discord.PCMVolumeTransformer(
                source,
                volume=self.volume
            )
        except Exception as e:
            print(f"[ArcadZ] Erro ao criar áudio: {e}")
            return

        def after(error):
            if error:
                print(
                    f"Erro ao reproduzir: {error}"
                )

            try:
                asyncio.run_coroutine_threadsafe(
                    self.play_next(),
                    bot.loop
                )
            except Exception as e:
                print(f"[ArcadZ] Erro ao avançar fila: {e}")

        try:
            self.voice.play(
                source,
                after=after
            )
        except Exception as e:
            print(f"[ArcadZ] Erro ao iniciar reprodução: {e}")
            return

        print(
            f"Tocando: {track.title}"
        )

        await broadcast(
            self.guild.id,
            "track_started",
            self.api_current()
        )

        await broadcast(
            self.guild.id,
            "queue_updated",
            {
                "queue": [
                    self.api_track(t)
                    for t in self.queue
                ]
            }
        )

    # --------------------------------------------------------
    # SERIALIZATION
    # --------------------------------------------------------

    @staticmethod
    def api_track(track: Track) -> dict[str, Any]:
        return {
            "id": track.track_id or track.url,
            "title": track.title,
            "channel": track.channel,
            "thumbnail": track.thumbnail,
            "duration": track.duration or 0,
            "url": track.url,
            "requestedBy": track.requester,
        }

    def api_current(self) -> dict[str, Any]:
        return {
            "current": (
                self.api_track(self.current)
                if self.current
                else None
            ),
            "position": self.position_seconds(),
            "playing": bool(
                self.voice and self.voice.is_playing()
            ),
            "paused": bool(
                self.voice and self.voice.is_paused()
            ),
            "volume": int(self.volume * 100),
            "shuffle": False,
            "loop": self.loop,
            "connected": bool(
                self.voice and self.voice.is_connected()
            ),
        }

    # --------------------------------------------------------
    # ADD
    # --------------------------------------------------------

    async def add(
        self,
        query: str,
        requester: str
    ):

        track = await self.extract(
            query,
            requester
        )

        was_playing = (
            self.voice
            and self.voice.is_playing()
        )

        self.queue.append(track)

        if not was_playing and not (self.voice and self.voice.is_paused()):
            await self.play_next()

        else:
            await broadcast(
                self.guild.id,
                "queue_updated",
                {
                    "queue": [
                        self.api_track(t)
                        for t in self.queue
                    ]
                }
            )

        return track

    # --------------------------------------------------------
    # PAUSE
    # --------------------------------------------------------

    def pause(self):
        if self.using_lavalink:
            if self.voice and self.voice.is_playing():
                self.paused_at = asyncio.get_running_loop().time()
                asyncio.create_task(self.voice.pause(True))
            return

        if self.voice and self.voice.is_playing():
            self.paused_at = asyncio.get_running_loop().time()
            self.voice.pause()

    # --------------------------------------------------------
    # RESUME
    # --------------------------------------------------------

    def resume(self):
        if self.using_lavalink:
            if self.voice and self.voice.is_paused():
                now = asyncio.get_running_loop().time()
                if self.paused_at is not None:
                    self.paused_total += now - self.paused_at
                self.paused_at = None
                asyncio.create_task(self.voice.pause(False))
            return

        if self.voice and self.voice.is_paused():
            now = asyncio.get_running_loop().time()

            if self.paused_at is not None:
                self.paused_total += now - self.paused_at

            self.paused_at = None
            self.voice.resume()

    # --------------------------------------------------------
    # SKIP
    # --------------------------------------------------------

    def skip(self):
        if self.using_lavalink:
            if self.voice and self.current:
                asyncio.create_task(self.voice.skip())
            return

        if self.voice and (
            self.voice.is_playing()
            or self.voice.is_paused()
        ):
            self.voice.stop()

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    def stop(self):
        if self.using_lavalink:
            self._lavalink_suppress_end = True
            if self.voice and self.current:
                asyncio.create_task(self.voice.skip())

        self.queue.clear()

        self.current = None

        self.loop = False

        self.started_at = None
        self.paused_at = None
        self.paused_total = 0.0

        if self.voice and (
            self.voice.is_playing()
            or self.voice.is_paused()
        ):
            self.voice.stop()

    # --------------------------------------------------------
    # SHUFFLE
    # --------------------------------------------------------

    def shuffle(self):

        random.shuffle(self.queue)


# ============================================================
# PLAYER MANAGER
# ============================================================

players: dict[int, MusicPlayer] = {}


def get_player(
    guild: discord.Guild
):
    if guild.id not in players:
        players[guild.id] = MusicPlayer(
            guild
        )

    return players[guild.id]


# ============================================================
# ARCADZ BRIDGE
# ============================================================

class ArcadzBridge(Bridge):
    """
    Implementação da Bridge do ArcadZ usando o MusicPlayer
    real deste bot.
    """

    def get_guild(self, guild_id: int) -> discord.Guild:
        guild = bot.get_guild(guild_id)

        if not guild:
            raise ValueError("Servidor não encontrado ou bot não está nele.")

        return guild

    # --------------------------------------------------------
    # PLAYER
    # --------------------------------------------------------

    async def player(self, guild_id: int):
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        return player.api_current()

    # --------------------------------------------------------
    # QUEUE
    # --------------------------------------------------------

    async def queue(self, guild_id: int):
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        return [
            player.api_track(track)
            for track in player.queue
        ]

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    async def history(self, guild_id: int):
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        return [
            player.api_track(track)
            for track in player.history
        ]

    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    async def search(self, query: str):
        query = query.strip()
        if not query:
            return []
        query = await resolve_music_query(query)
        # Use Lavalink for site searches too. Previously only /play used
        # Lavalink, while /api/search still extracted via yt-dlp.
        if lavalink_ready and wavelink is not None:
            identifier = query if query.startswith(("http://", "https://")) else "ytmsearch:" + query
            started = time.monotonic()
            results = await asyncio.wait_for(wavelink.Playable.search(identifier), timeout=18)
            tracks = results.tracks if hasattr(results, "tracks") else results
            output = []
            for track in list(tracks or [])[:5]:
                output.append({
                    "id": str(getattr(track, "identifier", "") or ""),
                    "title": track.title,
                    "channel": getattr(track, "author", "") or "",
                    "thumbnail": getattr(track, "artwork", None),
                    "duration": round(track.length / 1000) if track.length else 0,
                    "url": track.uri or query,
                    "requestedBy": "",
                })
            print(f"[Lavalink] API search: {len(output)} resultados em {time.monotonic()-started:.2f}s")
            return output
        if query.startswith(("https://", "http://")):
            return await asyncio.wait_for(asyncio.to_thread(self._search_url, query), timeout=25)

        loop = asyncio.get_running_loop()

        def run():
            with yt_dlp.YoutubeDL({
                **YTDL_OPTIONS,
                "quiet": True,
                "skip_download": True,
                "extract_flat": "in_playlist",
                "socket_timeout": 8,
                "retries": 1,
                "fragment_retries": 1,
            }) as ydl:

                info = ydl.extract_info(
                    f"ytsearch5:{query}",
                    download=False
                )

                results = []

                for item in info.get("entries", []) or []:
                    if not item:
                        continue

                    results.append({
                        "id": str(item.get("id") or ""),
                        "title": item.get("title", "Desconhecido"),
                        "channel": (
                            item.get("channel")
                            or item.get("uploader")
                            or ""
                        ),
                        "thumbnail": item.get("thumbnail"),
                        "duration": item.get("duration") or 0,
                        "url": item.get("webpage_url") or (
                            f"https://www.youtube.com/watch?v={item['id']}"
                            if item.get("id") else ""
                        ),
                        "requestedBy": "",
                    })

                return results

        # The worker may finish later, but the HTTP request will not hang indefinitely.
        return await asyncio.wait_for(loop.run_in_executor(None, run), timeout=25)

    @staticmethod
    def _search_url(url: str):
        with yt_dlp.YoutubeDL({
            **YTDL_OPTIONS, "extract_flat": True, "skip_download": True,
            "socket_timeout": 8, "retries": 1,
        }) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return []
        if "entries" in info:
            info = next((entry for entry in info["entries"] if entry), None)
        if not info:
            return []
        return [{
            "id": str(info.get("id") or ""),
            "title": info.get("title") or "Desconhecido",
            "channel": info.get("channel") or info.get("uploader") or "",
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration") or 0,
            "url": info.get("webpage_url") or url,
            "requestedBy": "",
        }]

    # --------------------------------------------------------
    # PLAY
    # --------------------------------------------------------

    async def play(
        self,
        guild_id: int,
        url: str | None,
        query: str | None,
        next_: bool
    ) -> None:

        guild = self.get_guild(guild_id)
        player = get_player(guild)

        source_query = (url or query or "").strip()

        if not source_query:
            raise ValueError("URL ou pesquisa não informada.")

        if not player.voice or not player.voice.is_connected():
            raise ValueError(
                "O bot não está conectado a um canal de voz. "
                "Use /join no Discord primeiro."
            )

        # "next" coloca a música no começo da fila.
        if next_:
            track = await player.extract(
                source_query,
                "ArcadZ"
            )

            player.queue.insert(0, track)

            # Se nada estiver tocando, inicia imediatamente.
            if not player.voice.is_playing() and not player.voice.is_paused():
                await player.play_next()

        else:
            await player.add(
                source_query,
                "ArcadZ"
            )

        await broadcast(
            guild_id,
            "queue_updated",
            {
                "queue": [
                    player.api_track(t)
                    for t in player.queue
                ]
            }
        )

    # --------------------------------------------------------
    # PAUSE
    # --------------------------------------------------------

    async def pause(self, guild_id: int) -> None:
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        player.pause()

        await broadcast(
            guild_id,
            "pause",
            player.api_current()
        )

    # --------------------------------------------------------
    # RESUME
    # --------------------------------------------------------

    async def resume(self, guild_id: int) -> None:
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        player.resume()

        await broadcast(
            guild_id,
            "resume",
            player.api_current()
        )

    # --------------------------------------------------------
    # SKIP
    # --------------------------------------------------------

    async def skip(self, guild_id: int) -> None:
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        player.skip()

        await broadcast(
            guild_id,
            "skip",
            {}
        )

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    async def stop(self, guild_id: int) -> None:
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        player.stop()

        await broadcast(
            guild_id,
            "stop",
            player.api_current()
        )

        await broadcast(
            guild_id,
            "queue_updated",
            {"queue": []}
        )

    # --------------------------------------------------------
    # SHUFFLE
    # --------------------------------------------------------

    async def shuffle(self, guild_id: int) -> None:
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        player.shuffle()

        await broadcast(
            guild_id,
            "queue_updated",
            {
                "queue": [
                    player.api_track(t)
                    for t in player.queue
                ]
            }
        )

    # --------------------------------------------------------
    # LOOP
    # --------------------------------------------------------

    async def loop(self, guild_id: int, mode: str) -> None:
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        normalized = str(mode or "").lower()

        if normalized in ("on", "true", "1", "enabled"):
            player.loop = True
        elif normalized in ("off", "false", "0", "disabled"):
            player.loop = False
        else:
            # Mantém comportamento de toggle caso o painel mande
            # um valor não explícito.
            player.loop = not player.loop

        await broadcast(
            guild_id,
            "loop_changed",
            {"loop": player.loop}
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    async def volume(self, guild_id: int, value: int) -> None:
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        value = max(0, min(100, int(value)))
        player.volume = value / 100
        if player.using_lavalink and player.voice:
            await player.voice.set_volume(value)
        if (
            not player.using_lavalink and player.voice
            and player.voice.source
            and isinstance(
                player.voice.source,
                discord.PCMVolumeTransformer
            )
        ):
            player.voice.source.volume = value / 100

        await broadcast(
            guild_id,
            "volume_changed",
            {"volume": value}
        )

    # --------------------------------------------------------
    # REMOVE
    # --------------------------------------------------------

    async def remove(self, guild_id: int, index: int) -> None:
        guild = self.get_guild(guild_id)
        player = get_player(guild)

        if index < 0 or index >= len(player.queue):
            raise IndexError("Índice da fila inválido.")

        player.queue.pop(index)

        await broadcast(
            guild_id,
            "queue_updated",
            {
                "queue": [
                    player.api_track(t)
                    for t in player.queue
                ]
            }
        )

    # --------------------------------------------------------
    # REORDER
    # --------------------------------------------------------

    async def reorder(
        self,
        guild_id: int,
        frm: int,
        to: int
    ) -> None:

        guild = self.get_guild(guild_id)
        player = get_player(guild)

        if (
            frm < 0
            or frm >= len(player.queue)
            or to < 0
            or to >= len(player.queue)
        ):
            raise IndexError("Índice da fila inválido.")

        track = player.queue.pop(frm)
        player.queue.insert(to, track)

        await broadcast(
            guild_id,
            "queue_updated",
            {
                "queue": [
                    player.api_track(t)
                    for t in player.queue
                ]
            }
        )


# ============================================================
# ARCADZ API
# ============================================================

@bot.event
async def on_ready():

    global arcadz_api
    global arcadz_api_task

    print(
        f"Bot conectado como {bot.user}"
    )

    global lavalink_ready
    if not lavalink_ready and LAVALINK_URI and LAVALINK_PASSWORD and wavelink is not None:
        try:
            node = wavelink.Node(uri=LAVALINK_URI, password=LAVALINK_PASSWORD)
            await asyncio.wait_for(wavelink.Pool.connect(nodes=[node], client=bot), timeout=12)
            lavalink_ready = True
            print(f"[Lavalink] Node connected: {LAVALINK_URI}")
        except Exception as exc:
            print(f"[Lavalink] Node unavailable; using yt-dlp: {exc}")

    # Inicia a FastAPI apenas uma vez.
    if arcadz_api is None:
        try:
            arcadz_api = ArcadzAPI(
                bot,
                bridge=ArcadzBridge()
            )

            arcadz_api_task = arcadz_api.start()

            print(
                f"[ArcadZ] API iniciada na porta "
                f"{os.getenv('PORT', '8000')}"
            )
        except Exception as e:
            print(f"[ArcadZ] ERRO ao iniciar API: {e}")

    try:
        synced = await bot.tree.sync()

        print(
            f"{len(synced)} comandos sincronizados."
        )

    except Exception as e:

        print(
            f"Erro ao sincronizar comandos: {e}"
        )


# ============================================================
# /JOIN
# ============================================================

@bot.tree.command(
    name="join",
    description="Entra no seu canal de voz."
)
async def join(
    interaction: discord.Interaction
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    if not interaction.user.voice:

        await interaction.response.send_message(
            "Você precisa estar em um canal de voz.",
            ephemeral=True
        )

        return

    await interaction.response.defer()

    channel = interaction.user.voice.channel

    player = get_player(
        interaction.guild
    )

    if player.voice:

        await player.voice.move_to(
            channel
        )

    else:

        player.voice = await channel.connect(cls=ArcadzLavalinkVoice if lavalink_ready else discord.VoiceClient)
        player.using_lavalink = isinstance(player.voice, ArcadzLavalinkVoice) and lavalink_ready

    await broadcast(
        interaction.guild.id,
        "connected",
        {
            "channel": channel.name
        }
    )

    await interaction.followup.send(
        f"🎵 Entrei em **{channel.name}**!"
    )


# ============================================================
# /PLAY
# ============================================================

@bot.tree.command(
    name="play",
    description="Adiciona uma música à fila."
)
@app_commands.describe(
    query="URL do YouTube ou pesquisa"
)
async def play(
    interaction: discord.Interaction,
    query: str
):

    await interaction.response.defer()

    if not interaction.guild:
        await interaction.followup.send(
            "Este comando só pode ser usado em um servidor."
        )
        return

    if not interaction.user.voice:

        await interaction.followup.send(
            "Você precisa estar em um canal de voz."
        )

        return

    channel = interaction.user.voice.channel

    player = get_player(
        interaction.guild
    )

    if not player.voice:

        player.voice = await channel.connect(cls=ArcadzLavalinkVoice if lavalink_ready else discord.VoiceClient)
        player.using_lavalink = isinstance(player.voice, ArcadzLavalinkVoice) and lavalink_ready

    elif player.voice.channel != channel:

        await player.voice.move_to(
            channel
        )

    try:

        track = await player.add(
            query,
            str(interaction.user)
        )

        embed = discord.Embed(
            title="🎵 Música adicionada",
            description=track.title,
            color=discord.Color.blurple()
        )

        if track.thumbnail:

            embed.set_thumbnail(
                url=track.thumbnail
            )

        embed.add_field(
            name="Solicitada por",
            value=interaction.user.mention
        )

        if track.duration:

            minutes = track.duration // 60
            seconds = track.duration % 60

            embed.add_field(
                name="Duração",
                value=f"{minutes}:{seconds:02d}"
            )

        await interaction.followup.send(
            embed=embed
        )

    except Exception as e:

        print(e)

        await interaction.followup.send(
            f"❌ Não consegui carregar essa música.\n"
            f"`{str(e)[:500]}`"
        )


# ============================================================
# /PAUSE
# ============================================================

@bot.tree.command(
    name="pause",
    description="Pausa a música."
)
async def pause(
    interaction: discord.Interaction
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    player = get_player(
        interaction.guild
    )

    player.pause()

    await broadcast(
        interaction.guild.id,
        "pause",
        player.api_current()
    )

    await interaction.response.send_message(
        "⏸️ Música pausada."
    )


# ============================================================
# /RESUME
# ============================================================

@bot.tree.command(
    name="resume",
    description="Continua a música."
)
async def resume(
    interaction: discord.Interaction
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    player = get_player(
        interaction.guild
    )

    player.resume()

    await broadcast(
        interaction.guild.id,
        "resume",
        player.api_current()
    )

    await interaction.response.send_message(
        "▶️ Música retomada."
    )


# ============================================================
# /SKIP
# ============================================================

@bot.tree.command(
    name="skip",
    description="Pula a música atual."
)
async def skip(
    interaction: discord.Interaction
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    player = get_player(
        interaction.guild
    )

    player.skip()

    await broadcast(
        interaction.guild.id,
        "skip",
        {}
    )

    await interaction.response.send_message(
        "⏭️ Música pulada."
    )


# ============================================================
# /STOP
# ============================================================

@bot.tree.command(
    name="stop",
    description="Para a música e limpa a fila."
)
async def stop(
    interaction: discord.Interaction
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    player = get_player(
        interaction.guild
    )

    player.stop()

    await broadcast(
        interaction.guild.id,
        "stop",
        player.api_current()
    )

    await broadcast(
        interaction.guild.id,
        "queue_updated",
        {"queue": []}
    )

    await interaction.response.send_message(
        "⏹️ Reprodução parada e fila limpa."
    )


# ============================================================
# /QUEUE
# ============================================================

@bot.tree.command(
    name="queue",
    description="Mostra a fila."
)
async def queue(
    interaction: discord.Interaction
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    player = get_player(
        interaction.guild
    )

    if not player.current and not player.queue:

        await interaction.response.send_message(
            "A fila está vazia."
        )

        return

    embed = discord.Embed(
        title="🎵 Fila de músicas",
        color=discord.Color.blurple()
    )

    if player.current:

        embed.add_field(
            name="▶️ Tocando agora",
            value=player.current.title,
            inline=False
        )

    if player.queue:

        songs = []

        for index, track in enumerate(
            player.queue[:10],
            start=1
        ):

            songs.append(
                f"`{index}.` {track.title}"
            )

        embed.add_field(
            name="Próximas",
            value="\n".join(songs),
            inline=False
        )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# /NOWPLAYING
# ============================================================

@bot.tree.command(
    name="nowplaying",
    description="Mostra a música atual."
)
async def nowplaying(
    interaction: discord.Interaction
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    player = get_player(
        interaction.guild
    )

    if not player.current:

        await interaction.response.send_message(
            "Nenhuma música está tocando."
        )

        return

    track = player.current

    embed = discord.Embed(
        title="🎵 Tocando agora",
        description=track.title,
        color=discord.Color.blurple()
    )

    if track.thumbnail:

        embed.set_image(
            url=track.thumbnail
        )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# /VOLUME
# ============================================================

@bot.tree.command(
    name="volume",
    description="Altera o volume."
)
@app_commands.describe(
    value="Volume de 0 a 100"
)
async def volume(
    interaction: discord.Interaction,
    value: app_commands.Range[int, 0, 100]
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    player = get_player(
        interaction.guild
    )

    player.volume = value / 100
    if player.using_lavalink and player.voice:
        await player.voice.set_volume(value)

    if not player.using_lavalink and player.voice and player.voice.source:

        if isinstance(
            player.voice.source,
            discord.PCMVolumeTransformer
        ):

            player.voice.source.volume = (
                value / 100
            )

    await broadcast(
        interaction.guild.id,
        "volume_changed",
        {"volume": value}
    )

    await interaction.response.send_message(
        f"🔊 Volume definido para **{value}%**."
    )


# ============================================================
# /SHUFFLE
# ============================================================

@bot.tree.command(
    name="shuffle",
    description="Embaralha a fila."
)
async def shuffle(
    interaction: discord.Interaction
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    player = get_player(
        interaction.guild
    )

    player.shuffle()

    await broadcast(
        interaction.guild.id,
        "queue_updated",
        {
            "queue": [
                player.api_track(t)
                for t in player.queue
            ]
        }
    )

    await interaction.response.send_message(
        "🔀 Fila embaralhada."
    )


# ============================================================
# /LOOP
# ============================================================

@bot.tree.command(
    name="loop",
    description="Ativa ou desativa o loop."
)
async def loop(
    interaction: discord.Interaction
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    player = get_player(
        interaction.guild
    )

    player.loop = not player.loop

    status = (
        "ativado 🔁"
        if player.loop
        else "desativado"
    )

    await broadcast(
        interaction.guild.id,
        "loop_changed",
        {"loop": player.loop}
    )

    await interaction.response.send_message(
        f"Loop {status}."
    )


# ============================================================
# /LEAVE
# ============================================================

@bot.tree.command(
    name="leave",
    description="Sai do canal de voz."
)
async def leave(
    interaction: discord.Interaction
):

    if not interaction.guild:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um servidor.",
            ephemeral=True
        )
        return

    player = get_player(
        interaction.guild
    )

    if player.voice:

        await player.voice.disconnect()

        player.voice = None

    player.stop()

    await broadcast(
        interaction.guild.id,
        "disconnected",
        {}
    )

    await interaction.response.send_message(
        "👋 Saí do canal de voz."
    )


# ============================================================
# START
# ============================================================

bot.run(TOKEN)
