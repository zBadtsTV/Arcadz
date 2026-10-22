# Lavalink node for Arcadz on Render

Deploy this directory as a **separate** Render Web Service from the existing bot.
Select the same GitHub repo and branch main; Root Directory: lavalink-node; runtime Docker.
Set env vars on the Lavalink service:

- PORT=2333
- SERVER_PORT=2333
- LAVALINK_SERVER_PASSWORD=<long-random-secret>
- _JAVA_OPTIONS=-Xmx384m (adjust according to available memory)

The environment variable overrides the example YAML password.
Use a random password, not the example.

After deployment, on the **existing Arcadz bot** Render service set:

- LAVALINK_URI=https://<your-lavalink-service>.onrender.com
- LAVALINK_PASSWORD=<the-same-long-random-secret>

Redeploy the bot. Logs should show `[Lavalink] Node connected`.
Use Discord `/join` and `/play` to test. A free Render instance can sleep
and/or be short on memory, causing cold-start delays or restarts.

Rollback: remove LAVALINK_URI and LAVALINK_PASSWORD from the bot's environment and redeploy.
The bot will use yt-dlp + FFmpeg again.

YouTube-source plugin 1.18.2 is provided; playback is not guaranteed if
YouTube blocks the server's requests. Never commit credentials.
