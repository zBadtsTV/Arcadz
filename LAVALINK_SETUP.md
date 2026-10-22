# Lavalink no Arcadz

O motor Lavalink foi integrado ao bot Python por Wavelink. Se nao for configurado, o Arcadz continua com yt-dlp.

## Render > Environment

Configure LAVALINK_URI com a URL de um servidor Lavalink v4 confiavel, por exemplo https://seu-node.exemplo, e LAVALINK_PASSWORD com a senha desse servidor. Nao coloque a senha no Lovable.

Reinicie o bot e procure por [Lavalink] Node connected nos logs. Entre no canal usando /join e teste /play, /pause, /resume, /skip e volume.

Sem a conexao configurada, o bot usa yt-dlp. Lavalink roda em um servidor Java separado, que voce precisa hospedar. O exemplo em lavalink-test pode ajudar a iniciar um node local, mas nao e um servidor publico e nao oferece YouTube por padrao.

Para voltar ao motor anterior, remova essas duas variaveis e reinicie o Render. Links do Spotify continuam convertidos em uma busca pelo nome.

Atencao: Lavalink nao garante eliminar erros 403 do YouTube; depende dos plugins e da fonte. Para YouTube e YouTube Music, instale o plugin youtube-source compativel no node.
