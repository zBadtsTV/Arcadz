import os
import asyncio
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

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

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": False,
    "no_warnings": False,
    "noplaylist": True,

    "extractor_args": {
        "youtubepot-bgutilhttp": {
            "base_url": "http://127.0.0.1:4416"
        }
    }
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}


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


    # --------------------------------------------------------
    # EXTRACT YOUTUBE
    # --------------------------------------------------------

    async def extract(self, query: str, requester: str):

        loop = asyncio.get_running_loop()

        def run():

            with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:

                info = ydl.extract_info(
                    query,
                    download=False
                )

                if "entries" in info:

                    info = info["entries"][0]

                return info

        info = await loop.run_in_executor(
            None,
            run
        )

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

            requester=requester
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
                return

            track = self.queue.pop(0)

            self.current = track


        source = discord.FFmpegPCMAudio(
            track.stream_url,
            **FFMPEG_OPTIONS
        )

        source = discord.PCMVolumeTransformer(
            source,
            volume=self.volume
        )


        def after(error):

            if error:
                print(
                    f"Erro ao reproduzir: {error}"
                )

            asyncio.run_coroutine_threadsafe(
                self.play_next(),
                bot.loop
            )


        self.voice.play(
            source,
            after=after
        )


        print(
            f"Tocando: {track.title}"
        )


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

        if not was_playing:
            await self.play_next()

        return track


    # --------------------------------------------------------
    # PAUSE
    # --------------------------------------------------------

    def pause(self):

        if self.voice and self.voice.is_playing():
            self.voice.pause()


    # --------------------------------------------------------
    # RESUME
    # --------------------------------------------------------

    def resume(self):

        if self.voice and self.voice.is_paused():
            self.voice.resume()


    # --------------------------------------------------------
    # SKIP
    # --------------------------------------------------------

    def skip(self):

        if self.voice and self.voice.is_playing():
            self.voice.stop()


    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    def stop(self):

        self.queue.clear()

        self.current = None

        self.loop = False

        if self.voice and self.voice.is_playing():
            self.voice.stop()


    # --------------------------------------------------------
    # SHUFFLE
    # --------------------------------------------------------

    def shuffle(self):

        import random

        random.shuffle(
            self.queue
        )


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
# READY
# ============================================================

@bot.event
async def on_ready():

    print(
        f"Bot conectado como {bot.user}"
    )

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

    if not interaction.user.voice:

        await interaction.response.send_message(
            "Você precisa estar em um canal de voz.",
            ephemeral=True
        )

        return


    channel = interaction.user.voice.channel

    player = get_player(
        interaction.guild
    )


    if player.voice:

        await player.voice.move_to(
            channel
        )

    else:

        player.voice = await channel.connect()


    await interaction.response.send_message(
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

        player.voice = await channel.connect()

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

    player = get_player(
        interaction.guild
    )

    player.pause()

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

    player = get_player(
        interaction.guild
    )

    player.resume()

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

    player = get_player(
        interaction.guild
    )

    player.skip()

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

    player = get_player(
        interaction.guild
    )

    player.stop()

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

    player = get_player(
        interaction.guild
    )

    player.volume = value / 100


    if player.voice and player.voice.source:

        if isinstance(
            player.voice.source,
            discord.PCMVolumeTransformer
        ):

            player.voice.source.volume = (
                value / 100
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

    player = get_player(
        interaction.guild
    )

    player.shuffle()

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

    player = get_player(
        interaction.guild
    )

    player.loop = not player.loop


    status = (
        "ativado 🔁"
        if player.loop
        else "desativado"
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

    player = get_player(
        interaction.guild
    )


    if player.voice:

        await player.voice.disconnect()

        player.voice = None


    player.stop()


    await interaction.response.send_message(
        "👋 Saí do canal de voz."
    )


# ============================================================
# START
# ============================================================

bot.run(TOKEN)
