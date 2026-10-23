# Privacidade e operação

## O que é armazenado

- `.env`: Client ID e modo de política, ignorado pelo Git.
- Gerenciador de Credenciais do Windows: refresh token, separado por Client ID.
- Memória do processo: access token, verificador PKCE temporário, sessão e respostas
  de playlists durante a consulta; Músicas Curtidas importadas na sessão do navegador.
- Navegador: cookie de sessão opaco, HttpOnly e SameSite=Lax, com duração de oito
  horas. Não contém tokens Spotify.

A Landing Page não consulta a biblioteca. Ao entrar no painel após autorização,
ou na primeira abertura do painel com uma conexão salva por sessão de navegador,
a aplicação carrega automaticamente a coleção nativa de Músicas Curtidas
(`GET /v1/me/tracks`, scope `user-library-read`), sem
procurar playlists por nome. A tela recebe nomes das músicas, artistas, álbuns,
duração, data de lançamento do álbum e links construídos a partir de IDs validados.
A data de adição recebida na importação não é exibida na tabela.
No modo real, a tela exibe capas dos álbuns usando somente URLs HTTPS no caminho
`https://i.scdn.co/image/` recebidas na importação. O navegador acessa o CDN oficial
diretamente, revelando seu IP ao CDN, sem enviar tokens Spotify nem a URL local
como referência (`referrerpolicy="no-referrer"`). Não há download de áudio.
A presença da credencial salva não comprova
que a autorização continua válida no Spotify; a leitura pode exigir uma nova
autorização, informada na tela. A operação de leitura de playlists preexistente permanece no
backend, sem botão ou link no painel; se acionada diretamente, pode apresentar
nomes e links de playlists, sem salvar a biblioteca em disco.

A lista importada fica somente na memória da sessão, nunca em arquivos ou banco.
Fica indisponível ao expirar a sessão e é descartada ao sair do demo, desconectar
ou reiniciar o processo. Uma nova autorização limpa as listas de todas as sessões,
evitando reapresentar músicas de uma conexão anterior. Uma falha transitória de
atualização mantém a lista anterior com aviso; nunca mescla resultados parciais.
Se a autorização precisar ser renovada, a lista daquela sessão é descartada.
Após a carga inicial, navegação e filtros usam a lista da sessão. Não há polling
ou sincronização contínua: o botão **Atualizar músicas** consulta novamente.
Falhas na carga automática permanecem explícitas e não provocam novas consultas
a cada navegação. Não existe exportação, edição ou envio da lista a IA.

Pesquisa por palavra-chave e filtros de artista, álbum e ano consultam somente
essa lista em memória, sem chamadas ao Spotify e sem alterar os itens importados.
Os critérios ficam na sessão do navegador e são enviados por POST com CSRF e
validação de origem, não por parâmetros na URL. Sair, desconectar ou reautorizar
também limpa os critérios. Não há logs de termos pesquisados ou de dados da biblioteca.

**Playlists Gerenciadas** usa a mesma coleção em memória para identificar artistas
e apresentar prévias locais por artista selecionado. O modal exibe nomes e contagens
e a prévia exibe as músicas planejadas; nenhum desses dados é enviado a outro
serviço ou a IA. Seleções são enviadas por POST com CSRF, sem metadados na URL,
e ficam somente na sessão. Atualizar músicas, reautorizar, sair, desconectar ou
reiniciar invalida as seleções. Esta prévia não cria nem altera playlists no
Spotify e não solicita novas permissões. Não há histórico persistente de decisões,
aprovação de escrita, exportação ou sincronização de playlists nesta etapa.

Não há cache da biblioteca em disco, telemetria, scripts externos, chamada de IA
ou acesso ao perfil. A resposta HTML é marcada `no-store`. O aplicativo não grava
capas em disco; o cache de imagens do navegador depende dos cabeçalhos do CDN.
Capas não disponíveis ou com falha de download mantêm o ícone e um aviso explícito.
O modo demo usa exclusivamente uma ilustração SVG fictícia local, com acesso a
imagens remotas bloqueado pela política de conteúdo. Um script local trata apenas
falhas de imagem, sem rede, armazenamento ou telemetria.
Arquivos temporários e memória do navegador/sistema estão sujeitos às proteções
do próprio Windows; isso não é isolamento contra malware ou outros processos do
mesmo usuário.

## Desconectar e revogar

**Desconectar Spotify** remove a credencial segura local, invalida as sessões e
autorizações pendentes e listas importadas da aplicação e retorna à Landing Page.
Se a exclusão falhar, a interface informa que a desconexão não foi concluída.
Isso não revoga a autorização no servidor Spotify; use
[Aplicativos da sua conta](https://www.spotify.com/account/apps/) para isso.

Se trocar o Client ID antes de desconectar, a credencial anterior continuará no
cofre Windows. Desconecte antes da troca ou exclua a entrada correspondente no
Gerenciador de Credenciais. Não tente ler seu conteúdo no terminal ou no chat.

## Limites de acesso local

O servidor escuta apenas em `127.0.0.1:8765`. Não deve ser exposto por proxy,
túnel, rede local ou hospedagem. POSTs exigem origem local e token anti-CSRF.
O callback usa state aleatório, vinculado ao navegador, expira e é de uso único.
Não habilitamos CORS. Cookies usam HTTP porque a aplicação é estritamente loopback;
uma eventual versão hospedada exigiria uma revisão de segurança e HTTPS.

A conexão pertence à instalação local e é lembrada no cofre do Windows.
Navegadores não representam usuários Spotify isolados: este produto não é um
SaaS hospedado nem uma autenticação multiusuário. O modo `--demo` mantém somente
sessões simuladas por navegador e nunca acessa a conexão real.

Não abra uma sessão real usando ferramentas de navegador do agente. Use
`--demo` para qualquer inspeção automatizada. Conteúdo privado da API, nomes de
playlists, músicas e tokens não podem entrar no contexto do modelo.

## Política de IA

`SPOTIFY_AI_POLICY_MODE=blocked` é o único modo aceito. O produto não possui
integração com modelos. Classificação futura requer fontes permitidas e controles
de proveniência; permitir conteúdo Spotify em IA exigirá decisão arquitetural
documentada com fundamento nas políticas aplicáveis.

As restrições são uma interpretação técnica conservadora, não um parecer jurídico.
Consulte as [políticas](https://developer.spotify.com/policy) e os
[termos](https://developer.spotify.com/terms) do Spotify.
