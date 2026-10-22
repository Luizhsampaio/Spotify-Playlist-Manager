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
demonstração visual é isolada e não acessa transporte HTTP ou credenciais.
Os testes da integração real usam transporte HTTP simulado e credenciais fictícias.
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
- `demo.py`: playlists fictícias do protótipo e utilitários sintéticos de testes.

Um lock no cliente serializa operações de token/leitura/desconexão na instância.
Não são permitidas instâncias concorrentes na mesma porta. Os dados da API são
validados antes de exibir, templates escapam nomes e URLs de playlist são
construídas a partir de IDs validados.

## Incremento visual — Interface SaaS local com OAuth

Os modos real e `serve --demo` compartilham os templates e CSS da Landing Page
(`/`), entrada (`/entrar`) e painel (`/app`), com textos e ações específicos.
O painel apresenta somente a estrutura visual: não lista playlists, não consulta
perfil ou biblioteca, nem oferece automações. Não há frontend separado.

No modo real, `/connect` inicia o OAuth existente. Um callback bem-sucedido
rotaciona o token CSRF e leva ao painel; cancelamentos e falhas levam à entrada
com mensagem explícita. O callback continua temporário, vinculado à sessão e de
uso único. Navegações de documento retornando do Spotify podem chegar às páginas
do produto; requisições externas que não são navegação continuam bloqueadas.

Sem credencial salva, `/app` encaminha à entrada. A presença de uma credencial no
cofre permite abrir o painel novamente, mas não comprova validade atual da
autorização e não causa renovação remota. A interface distingue conexão salva
de autorização validada, sem nome ou email inventados. **Desconectar Spotify**
apaga a credencial local e invalida todas as sessões e autorizações pendentes;
falhas de exclusão aparecem no painel sem anunciar sucesso.

A capacidade de leitura existente no cliente e no POST `/playlists` é preservada,
com seus testes, mas não tem acionador na nova interface. Não há consulta de
playlists em nenhuma navegação ou callback.

No demo, os POSTs `/demo/entrar` e `/demo/sair` apenas mudam a sessão temporária daquele
navegador, com validação de origem e CSRF. Não existe cadastro ou base de usuários.
O painel usa a mesma estrutura visual, sem listas, cofre ou chamadas externas.
As ilustrações públicas usam exemplos fictícios, não dados da biblioteca.

As rotas de OAuth e leitura reais não são registradas no modo de protótipo.
As rotas de simulação também não são registradas no modo real. O visual SaaS não
habilita hospedagem nem isolamento de contas reais: autenticação, ciclo de vida
de tokens por usuário e revisão para execução hospedada ficam para outra entrega.
