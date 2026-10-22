# Privacidade e operação

## O que é armazenado

- `.env`: Client ID e modo de política, ignorado pelo Git.
- Gerenciador de Credenciais do Windows: refresh token, separado por Client ID.
- Memória do processo: access token, verificador PKCE temporário, sessão e respostas
  de playlists durante a consulta.
- Navegador: cookie de sessão opaco, HttpOnly e SameSite=Lax, com duração de oito
  horas. Não contém tokens Spotify.

A Landing Page, entrada e painel não consultam perfil nem biblioteca. O painel
mostra apenas a estrutura visual e o estado da conexão local, sem nome, email,
listas ou contagens de playlists. A presença da credencial salva não comprova
que a autorização continua válida no Spotify; não fazemos uma consulta remota
para verificar esse indicador. A operação de leitura preexistente permanece no
backend, sem botão ou link no painel; se acionada diretamente, pode apresentar
nomes e links de playlists, sem salvar a biblioteca em disco.

Não há cache da biblioteca em disco, telemetria, scripts externos, CDN, imagens
remotas, chamada de IA ou acesso ao perfil. A resposta HTML é marcada `no-store`.
Arquivos temporários e memória do navegador/sistema estão sujeitos às proteções
do próprio Windows; isso não é isolamento contra malware ou outros processos do
mesmo usuário.

## Desconectar e revogar

**Desconectar Spotify** remove a credencial segura local, invalida as sessões e
autorizações pendentes da aplicação e retorna à Landing Page.
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
playlists e tokens não podem entrar no contexto do modelo.

## Política de IA

`SPOTIFY_AI_POLICY_MODE=blocked` é o único modo aceito. O produto não possui
integração com modelos. Classificação futura requer fontes permitidas e controles
de proveniência; permitir conteúdo Spotify em IA exigirá decisão arquitetural
documentada com fundamento nas políticas aplicáveis.

As restrições são uma interpretação técnica conservadora, não um parecer jurídico.
Consulte as [políticas](https://developer.spotify.com/policy) e os
[termos](https://developer.spotify.com/terms) do Spotify.
