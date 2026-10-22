# Lavalink isolated experiment (no production changes)

This test starts a local Lavalink v4 server and benchmarks **track lookup** using SoundCloud.
It does **not** connect to the Arcadz Discord voice client, play audio, or modify the live bot.

## Run locally
1. Install Docker Desktop and ensure Docker is running.
2. From the repository root, run `cd lavalink-test`.
3. Run `docker compose up -d`.
4. Wait for startup: `docker compose logs -f lavalink`.
5. In a second terminal: `python benchmark.py "lofi hip hop"`.
6. Shut down: `docker compose down`.

Lavalink's built-in YouTube source is intentionally disabled: YouTube extraction requires a maintained
plugin and may encounter the same 403 restrictions as yt-dlp. The test uses SoundCloud only,
so it does not establish YouTube compatibility.

The password and port binding are **for local testing only**. Never expose this server to the
public Internet with the example password. If lookup is promising, a separate integration
can be built with Wavelink and tested on a second Discord bot before production migration.

## Interpretation
A fast lookup proves only that the Lavalink server can resolve search results.
It does not measure Discord voice connection time, audio buffering, or playback stability.
