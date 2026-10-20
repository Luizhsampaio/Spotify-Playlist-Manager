# Decisões da primeira entrega

## ADR 0001 — Agente neutro e interface local

**Decisão:** Python 3.12+, FastAPI/Uvicorn, templates Jinja2, CSS local, HTTPX,
Pydantic e Typer para iniciar o servidor. Não há SDK nem chamada a provedor de IA.

**Motivo:** o usuário não é desenvolvedor e escolheu uma tela simples. Templates
evitam um projeto frontend separado, Node e uma API de UI desnecessária.
Isso complementa a stack recomendada na especificação, sem acoplar o domínio à web.

**Consequência:** o comando `serve` inicia a aplicação; os demais comandos e
camadas previstas para curadoria não são implementados antecipadamente.

## ADR 0002 — Credenciais seguras, sem catálogo nesta etapa

**Decisão:** refresh token no Windows Credential Manager via keyring; access token
em memória. Nenhuma persistência de biblioteca, banco de dados ou catálogo.
Recusar backends alternativos/inseguros em vez de fazer fallback para texto.

**Motivo:** lembrar a conexão foi uma escolha expressa do usuário.

**Consequência:** a execução real desta entrega é suportada no Windows. A
demonstração é isolada, usa transporte HTTP simulado e credenciais em memória.
O modelo de estado em arquivos do produto será desenvolvido somente com o catálogo.

## ADR 0003 — Somente leitura e controle humano

**Decisão:** solicitar somente `playlist-read-private` e usar exclusivamente
`GET /me/playlists`, além dos endpoints oficiais de OAuth.

**Consequência:** não há escrita remota nem necessidade de criar agora modelos de
proposta/aprovação. Futuras mutações exigem uma nova entrega com aprovação explícita.

## ADR 0004 — Gate Spotify/IA fechado

**Decisão:** aceitar somente `SPOTIFY_AI_POLICY_MODE=blocked`. Não transmitir
conteúdo Spotify ao agente ou a modelos, inclusive durante testes visuais.

**Motivo:** preservar a restrição conservadora da especificação enquanto a
conformidade da curadoria semântica é esclarecida. O fluxo desta entrega é apenas
leitura determinística para apresentação direta ao usuário.

**Consequência:** testes e demonstração são sintéticos. Não implementamos uma
consulta formal de suporte automaticamente nem alegamos aprovação do Spotify.
A fonte independente de metadados e o fluxo completo de curadoria continuam fora
do escopo e sujeitos a análise própria antes da implementação.

## Responsabilidades

- `config.py`: configuração local validada e gate fechado.
- `credentials.py`: contrato de credenciais e backend Windows.
- `models.py`: contratos mínimos de dados e erros seguros para a interface.
- `spotify.py`: OAuth, renovação, paginação, timeout e limites de requisições.
- `web.py`: sessão local, callback, CSRF e apresentação.
- `cli.py`: inicialização estritamente loopback, sem access logs sensíveis.
- `demo.py`: transporte e credenciais sintéticos, ativados explicitamente.

Um lock no cliente serializa operações de token/leitura/desconexão na instância.
Não são permitidas instâncias concorrentes na mesma porta. Os dados da API são
validados antes de exibir, templates escapam nomes e URLs de playlist são
construídas a partir de IDs validados.
