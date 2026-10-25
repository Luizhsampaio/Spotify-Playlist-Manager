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
O único JSON operacional guarda metadados mínimos das playlists gerenciadas, não
catálogo ou conteúdo da biblioteca.

## ADR 0003 — Somente leitura e controle humano

**Decisão:** solicitar somente `playlist-read-private` e usar exclusivamente
`GET /me/playlists`, além dos endpoints oficiais de OAuth.

**Histórico:** essa foi a decisão da versão inicial, sem escrita remota. A extensão
da POC #17 abaixo acrescenta mutações exclusivamente ao envio manual da configuração
ativa por artista; não habilita execução em segundo plano.

**Extensão — Minhas Músicas:** a autorização também solicita `user-library-read`
para consultar `GET /me/tracks`, na entrada no painel e na atualização manual.
`playlist-read-private` é preservado para a capacidade preexistente. Nenhum
escopo de escrita era solicitado nessa extensão inicial. Conexões antigas podem
exigir nova autorização.

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
- `library.py`: pesquisa e filtros determinísticos da coleção já importada.
- `playlist_preview.py`: agrupamento determinístico para prévias por artista.
- `spotify.py`: OAuth, renovação, paginação, timeout e limites de requisições.
- `web.py`: sessão local, callback, CSRF e apresentação.
- `cli.py`: inicialização estritamente loopback, sem access logs sensíveis.
- `demo.py`: músicas e playlists fictícias e utilitários sintéticos de testes.

Um lock no cliente serializa operações de token/leitura/desconexão na instância.
Não são permitidas instâncias concorrentes na mesma porta. Os dados da API são
validados antes de exibir, templates escapam nomes e URLs de playlist são
construídas a partir de IDs validados.

## Incremento visual — Interface SaaS local com OAuth

Os modos real e `serve --demo` compartilham os templates e CSS da Landing Page
(`/`), entrada (`/entrar`) e painel (`/app`), com textos e ações específicos.
O painel não lista playlists nem oferece automações. A funcionalidade
**Minhas Músicas**, descrita abaixo, faz leitura explícita de Músicas Curtidas.
Não há consulta de perfil nem frontend separado.

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
O painel usa a mesma estrutura visual, sem cofre ou chamadas externas.
Minhas Músicas permite importar uma lista exclusivamente fictícia.
As ilustrações públicas usam exemplos fictícios, não dados da biblioteca.

As rotas de OAuth e leitura reais não são registradas no modo de protótipo.
As rotas de simulação também não são registradas no modo real. O visual SaaS não
habilita hospedagem nem isolamento de contas reais: autenticação, ciclo de vida
de tokens por usuário e revisão para execução hospedada ficam para outra entrega.

## Incremento — Minhas Músicas

`app_shell.html` compartilha menu lateral, sessão e avisos entre Visão geral e
Minhas Músicas (`GET /app/musicas`). A primeira entrada no painel (`/app` ou
`/app/musicas`) de uma sessão com conexão salva carrega a coleção automaticamente,
inclusive após o callback OAuth ou a entrada simulada. As próximas navegações
reutilizam a lista. A Landing Page não inicia importação.
`POST /app/musicas/importar` mantém a atualização manual, com sessão, origem local
e CSRF. Ambos os fluxos reutilizam o mesmo carregamento, que no modo real
consulta todas as páginas de `GET /v1/me/tracks?limit=50`, reaproveitando
renovação de token, limites de retry e validação estrita de paginação.
Não há busca por uma playlist chamada “Music Liked”: são os itens salvos da
coleção nativa Músicas Curtidas.

O leitor paginado compartilhado com playlists valida host, HTTPS, caminho exato,
parâmetros numéricos e ciclos antes de enviar credenciais. A lista só substitui
a importação anterior após todas as páginas serem validadas. Falhas transitórias
mantêm a última lista completa com aviso; falta de permissão ou revogação limpa
a lista da sessão e oferece nova autorização.

Modelos tipados preservam músicas indisponíveis (`track: null`) como linhas
explicitamente identificadas. Nomes são escapados pelo template. Links são
construídos a partir de IDs validados; não há carregamento de áudio.
Não é aplicado um limite artificial ao número de músicas. Como a biblioteca pode
mudar durante a paginação, não se promete uma fotografia imutável da conta.

A lista permanece em memória em `BrowserSession`, isolada por sessão de navegador,
sem persistência e sem importação em segundo plano. Sair do demo, desconectar ou
reiniciar descarta as listas; expirar a sessão impede novo acesso. Reautorizar
limpa as importações de todas as sessões por se tratar de uma única conexão local.
Não há armazenamento de catálogo, correções, classificação ou IA. A escrita Spotify
fica restrita à configuração manual descrita na POC #17.

O painel aguarda a primeira carga antes de responder, sem tarefas em segundo plano
ou JavaScript de importação. Um lock por sessão impede que abas simultâneas dupliquem
a carga inicial. O estado distingue carga não tentada, coleção vazia, sucesso e
falha: falhas são exibidas na tela de músicas e não provocam retries em loop na
navegação. O botão de atualização permite tentar novamente sem novo login.
Falhas de permissão oferecem reconexão. A reautorização limpa a lista, os filtros
e o estado de carga, de modo que a próxima entrada carregue a conexão atual.
Resultados que terminam após expiração, desconexão ou troca da conexão não são
publicados na sessão antiga. Não há polling ou sincronização contínua.

### Colunas, lançamentos e filtros locais

A tabela possui exatamente Música, Artista, Álbum, Data de Lançamento e Duração.
O modelo de álbum conserva `release_date` e `release_date_precision`, valida datas
e preserva a precisão recebida: ano, mês/ano ou dia/mês/ano. Sem data, a interface
indica **Não informada**. A data de adição às Músicas Curtidas não é usada como
lançamento; o dado disponível é o lançamento do álbum, não necessariamente o
lançamento original da gravação.

`library.py` produz uma visão filtrada sem modificar a lista da sessão.
A busca combina todas as palavras, sem diferenciar maiúsculas ou acentos, nos
nomes de música, artistas e álbum. Os seletores usam correspondência exata e
podem ser combinados com a busca. Colaborações são encontradas por qualquer um
dos artistas; as opções vêm sempre da coleção completa. Faixas sem metadados
continuam na visão sem filtros, mas não recebem artistas, datas ou anos inventados.

O script local `searchable-selects.js` aprimora os três seletores com pesquisa
de opções sem acentos ou distinção de maiúsculas, seleção única e navegação por
teclado (setas, Enter e Escape). A busca de opções não tem nome no formulário:
somente o valor escolhido no select original é enviado ao aplicar filtros.
Sem JavaScript, os selects nativos continuam disponíveis. O aprimoramento não
faz requisições, não persiste pesquisas e constrói textos com `textContent`.
O painel pesquisável abre para baixo em posicionamento absoluto, sobreposto aos
resultados, sem participar do grid ou deslocar campos e tabela. O cartão não
recorta o menu; apenas a tabela mantém sua rolagem horizontal interna.
O foco não rola a página ao abrir, e as setas rolam somente a lista de opções.

`POST /app/musicas/filtrar` valida sessão, origem, CSRF, quantidade e tamanho dos
campos, preservando os filtros anteriores quando a entrada é inválida.
Os critérios ficam em `BrowserSession`; o redirecionamento para a página da
coleção não leva metadados em parâmetros da URL. O POST de limpeza usa os mesmos
controles. Nenhuma dessas operações consulta o Spotify ou altera a importação.
Atualizar músicas preserva os filtros; sair, desconectar ou reautorizar os limpa.
Resultados vazios de pesquisa são distintos de uma coleção importada vazia.
Em telas pequenas, a tabela permite rolagem horizontal interna, sem esconder colunas.
Filtros, resultados e estados vazios compartilham um único cartão com borda e
fundo contínuos, preservando a rolagem interna e o foco visível da tabela.

### Miniaturas de capas

O modelo de álbum conserva `images` e seleciona a menor imagem confiável na ordem
de tamanhos retornada pelo Spotify. Somente URLs HTTPS com host exato `i.scdn.co`
e caminho `/image/` seguido de um identificador alfanumérico são exibidas, sem
credenciais, porta, query ou fragmento. Ausência de capa confiável mantém um ícone
e a indicação **Capa indisponível**, sem impedir a leitura da coleção.

As miniaturas são exibidas integralmente, sem recorte, ao lado do título, inclusive
em telas pequenas, com link para a faixa no Spotify quando disponível. O navegador
carrega as imagens diretamente do CDN com lazy loading, sem token ou referrer
local. A CSP permite apenas esse caminho de imagens no modo real e scripts locais;
o script `album-covers.js` trata falhas com ícone e aviso, inclusive imagens que
falharam antes da inicialização do script. Não há proxy, download no backend ou
persistência de arquivos de capas. A política de IA permanece bloqueada.

No demo, o template ignora URLs de imagens reais e usa somente a arte sintética
local `demo-album-cover.svg`; a CSP continua bloqueando imagens externas.

## Incremento — Playlists Gerenciadas, prévia por artista

`GET /app/playlists` compartilha o shell e o carregamento inicial da coleção.
O menu substitui a opção Playlists desabilitada por **Playlists Gerenciadas**.
O botão **Por artista** abre um `dialog` nativo, com pesquisa, checkboxes,
seleção/limpeza de todos, navegação por teclado e retorno de foco ao fechar.
Ele ocupa um cartão quadrado de até 160 px, sem se estender pela página.
O cartão usa uma ilustração local de palco/artista ao fundo, com gradiente para
contraste do título, subtítulo e ação **Configurar**, sem ícone musical.
Novos cartões são distribuídos lado a lado, com quebra de linha conforme a tela.
Cancelar ou pressionar Escape não submete a configuração.

`playlist_preview.py` usa a coleção completa, independente dos filtros de músicas.
O agrupamento inclui todos os artistas participantes, usa ID de artista quando
disponível e, na ausência dele, nome com aviso explícito. Chaves de formulário
são hashes determinísticos da identidade; não representam uma nova autenticação.
O ID da faixa impede duplicatas dentro de cada grupo; faixas sem ID ou artistas
utilizáveis são excluídas com contagem. A ordem das músicas segue a importação.

`POST /app/playlists/por-artista/salvar` exige sessão, origem local, CSRF, revisão
atual da coleção e artistas pertencentes à lista calculada pelo servidor.
Não aceita nomes arbitrários de playlists. Uma configuração nova começa sem
artistas selecionados; a configuração salva é carregada por conta.
A revisão é rotacionada após
cada importação completa; o lock de importação serializa a validação da seleção.
Formulários desatualizados são recusados, sem anunciar sucesso.

A seleção é reaberta pelo cartão **Por artista**, sem seção de prévia externa.
Cancelar não altera a configuração anterior; **Salvar** valida o envio inteiro e
persiste a seleção para a conta antes de reconciliar playlists. Não há interruptor global: cada salvamento
aplica a seleção atual, inclusive uma seleção vazia. A configuração persistente
está no registro `.local-data/spotify/managed-playlists.json`, particionado pelo
`account_id` retornado por `GET /v1/me`; contém as chaves de artistas selecionados
e IDs/estados das playlists, nunca faixas da biblioteca. A seleção sobrevive a
sessões e navegadores da mesma conta. Atualizar músicas não a invalida. Busca e
seleção não chamam a API. O demo não acessa transporte externo ou credenciais e
mantém a seleção somente na sessão. O modal exibe ao lado de cada artista o ID
da playlist registrada que continua vinculada; artistas sem registro ativo são
marcados como sem playlist vinculada.
O caminho do registro é resolvido a partir da localização do aplicativo, não do
diretório de trabalho do processo, para manter a persistência entre reinicializações.

No modo real, **Salvar** usa `playlist-modify-public`,
`user-library-modify`, `user-library-read` e `user-read-private`. Antes da criação, persiste um estado
pendente; após a resposta, registra imediatamente o ID e proprietário. Playlists
existentes só são alteradas após validar ID, proprietário registrado, visibilidade
pública e ausência de colaboração. O conteúdo é substituído por URIs elegíveis das
Músicas Curtidas (primeiro lote de até 100 via PUT, lotes restantes via POST), lido
de volta e comparado exatamente antes de informar conclusão.

`APP_NAME` define o nome de apresentação harmonia playlists, compartilhado pelos
templates e CLI. O nome Harmonia Playlist Management (HPM) permanece na descrição
padrão das playlists. Os nomes técnicos de pacote/comando e os identificadores do
cofre e do registro não são alterados.

A criação inclui descrição padrão não nula, identificando Harmonia Playlist
Management (HPM) e orientando evitar ajustes manuais. O texto é determinístico e limitado a
300 caracteres por teste e validação do cliente. No salvamento explícito, nomes
divergentes e descrições ausentes/vazias/"Null" de destinos registrados são
atualizados em um único PUT de detalhes e lidos de
volta antes de alterar faixas. Timeout não repete o PUT: a leitura pode comprovar
aplicação ou produzir resultado parcial. Descrição nula/omitida na criação não
descarta o ID nem provoca outra criação; o ID é registrado antes da reconciliação.
Somente registro da conta e propriedade validada autorizam a escrita, nunca a
presença da marca Harmonia na descrição. Textos personalizados não vazios são
preservados; ao atualizar somente o nome, o PUT não envia o campo de descrição.
A descrição padrão anterior, reconhecida pelo texto completo exato, também é
migrada no salvamento explícito, sem confundi-la com personalização do usuário.

Desmarcar um artista remove a playlist apenas de Sua Biblioteca; após confirmação,
o registro local do ID é apagado. A playlist pública não é excluída e pode ficar
sem vínculo na biblioteca. Se o artista for selecionado novamente, uma playlist
nova é criada. Registros legados no estado `removed` também são descartados antes
de criar. Resultados incertos de remoção preservam o ID local e bloqueiam nova
criação automática; estados incertos de criação também bloqueiam retry. Falhas e
resultados parciais aparecem por artista, sem sucesso integral não verificado.
Um lock global serializa envios.
`SPOTIFY_AI_POLICY_MODE=blocked` permanece inalterado.

### Validação prévia e escolha em conflitos

Antes de persistir a seleção ou executar mutações, o POST verifica permissões,
estados locais incertos, identidade do artista, propriedade da conta, duplicidade
de destinos e nomes válidos. Consulta `GET /v1/me/library/contains` em lotes de até
40 URIs para verificar presença, além dos detalhes dos destinos presentes e das
faixas dos selecionados. As respostas devem ser listas de booleanos estritos com
o tamanho exato do lote. Um problema impeditivo interrompe o envio inteiro e
renderiza `prototype_managed_playlists.html` com o dialog aberto, avisos dentro do
formulário e seleção enviada preservada apenas para edição. Os IDs vêm do snapshot
da conta já verificado, sem novas consultas para renderizar o erro; quando não
foram carregados, a interface informa que o vínculo não foi verificado.

Somente presença `false` confirmada permite oferecer substituição, sem descartar
o ID durante a verificação. Timeout, 401/403, 429, 5xx, payload inválido e 404 de
detalhes não são convertidos em exclusão. Propriedade, privacidade e colaboração
incompatíveis exigem resolução pelo usuário; não são alteradas silenciosamente.

O botão inicial é **Configurar**. Em conflitos, o rodapé oferece somente
**Cancelar**, **Atualizar** e **Confirmar e Salvar**, tanto para recriar ausentes selecionados
quanto para limpar ausentes desmarcados. Para reconectar, o usuário cancela e
usa o controle de desconexão do painel antes de conectar novamente. A confirmação
explícita de substituição fica em `BrowserSession`, vinculada a token de uso único,
conta, revisão da coleção, seleção anterior e snapshot dos
registros. Expira em dez minutos. A confirmação revalida os vínculos sob o lock
global; mudanças no contexto ou nos destinos ausentes interrompem a aplicação.
O formulário de confirmação envia as marcações atuais, não uma seleção congelada
da tentativa anterior. Ausentes selecionados exigem essa confirmação para recriar
pelo fluxo normal, com proteção de criação incerta. Ausentes desmarcados podem
ser limpos sem mutação remota, após revalidação e somente se não houver outros
impedimentos. O submit de configuração recebe nome/valor de confirmação somente
quando há autorização pendente por ausência comprovada. Nos demais conflitos,
o botão de confirmação apenas reenvia a seleção para nova validação, sem autorizar recriação.
Cancelar/fechar o dialog de conflitos sem refresh assíncrono retorna por GET à
configuração salva, invalidando a confirmação pendente. O submit preserva o nome/valor do botão de
confirmação antes de desabilitar os botões para evitar envio duplicado.

O Spotify não oferece uma transação para o lote inteiro. A fase prévia evita
iniciar operações com conflitos conhecidos, mas não impede mudanças externas ou
falhas após o início. Resultados parciais continuam explícitos e não são revertidos
por mutações compensatórias automáticas.

### Atualização manual no modal

O botão Atualizar usa `POST /app/playlists/por-artista/atualizar`, com os mesmos
controles de sessão, origem, CSRF, revisão e validação de artistas do formulário.
Reutiliza `refresh_tracks` e o lock de importação para ler todas as páginas, sem
persistir seleção ou executar mutações de playlists. Sucesso troca a coleção e
revisão; falha transitória mantém a última importação completa.
O clique no cartão Por artista abre o dialog imediatamente e aciona essa mesma
leitura assíncrona. O botão Atualizar continua disponível para repetir a consulta.
Não disparar leitura ao reabrir automaticamente o modal de conflitos após um
salvamento: isso invalidaria a confirmação recém-apresentada e poderia causar loops.

O JavaScript envia o formulário via fetch local com Accept application/json. A
resposta contém mensagem, sucesso explícito, revisão, seleção persistida e HTML
renderizado pelo template existente. Atualiza somente as opções do modal via
DOMParser/replaceChildren, preservando pesquisa, rolagem, foco e as marcações mais
recentes, inclusive edições feitas enquanto a leitura estava em andamento.
A CSP permite connect-src somente self; o navegador não consulta a API Spotify.
Sem JavaScript, a rota também aceita envio normal com resposta HTML.

Não há navegação/reload na atualização assíncrona. Os controles de envio são
bloqueados durante a consulta para evitar sobreposição. Fechar descarta edições,
mas deixa a leitura finita concluir; reabrir aguarda essa conclusão antes da próxima
consulta para não enviar revisão obsoleta nem sobrepor paginações. Sair da página
aborta a espera do navegador. A configuração persistida não é alterada, e os defaults dos
checkboxes são restaurados para essa seleção ao cancelar/reabrir.
Artistas marcados que deixam a coleção são avisados explicitamente; seus registros
e IDs não são removidos pelo refresh. Confirmações de substituição são invalidadas,
e a ação seguinte volta à validação normal com a nova revisão. Não há polling,
atualização automática na navegação, timestamp ou nova regra de playlists vazias.
