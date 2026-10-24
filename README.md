# Curador Musical

Uma aplicação pessoal, em português, que roda no seu computador com **interface
inspirada em SaaS**: Landing Page, entrada pelo Spotify e painel do produto.
O menu lateral dá acesso a **Minhas Músicas**, que importa e lista suas
**Músicas Curtidas** do Spotify, somente para leitura e sem salvar a biblioteca
em disco. Não executa automações nem modifica sua conta.

Não há integração com IA, hospedagem, banco de dados ou necessidade de Client Secret.
A senha é digitada somente no site oficial do Spotify.

## 1. Preparar

Você precisa do Windows, Python 3.12 ou superior e uma conta Spotify Premium.
Abra o terminal **PowerShell** no VS Code, na pasta deste projeto, e execute:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

Esse comando cria um ambiente Python isolado, instala as dependências e cria sua
configuração local. Não altera permanentemente a política de execução do PowerShell.

## 2. Criar o aplicativo no Spotify

Siga o [guia de configuração](docs/spotify-setup.md). Você vai cadastrar um aplicativo
no painel do Spotify e copiar seu **Client ID** para o arquivo local `.env`.
Nunca envie senhas ou tokens no chat.

## 3. Abrir a aplicação

```powershell
.\.venv\Scripts\python.exe -m music_curator serve
```

Abra **http://127.0.0.1:8765** no navegador:

1. Na Landing Page, clique em **Entrar** ou **Entrar com Spotify**.
2. Na tela de entrada, clique em **Continuar com Spotify**.
3. Confira as permissões no site oficial. Além das permissões de leitura
   `playlist-read-private` e `user-library-read`, a sincronização ativa solicita
   `playlist-modify-public`, `user-library-modify` e `user-read-private` para
   validar a conta proprietária do registro local.
4. Após autorizar, o aplicativo carrega automaticamente suas Músicas Curtidas
   antes de abrir o painel, consultando todas as páginas da coleção acessível.
5. No **Meu espaço**, clique em **Minhas Músicas** no menu lateral: a lista
   já estará disponível, sem precisar clicar em um botão para importar.
6. A tabela mostra **Música, Artista, Álbum, Data de Lançamento e Duração**;
   a música inclui uma miniatura da capa do álbum. Capas ausentes ou com falha
   mantêm o ícone com aviso; faixas indisponíveis são sinalizadas.
   A data é a de lançamento do álbum
   informada pelo Spotify, preservando ano ou mês quando não houver dia exato.
7. Use a **pesquisa por palavra-chave** para procurar em músicas, artistas e álbuns,
   sem diferenciar maiúsculas ou acentos. Várias palavras precisam aparecer na
   combinação desses campos.
8. Combine os filtros de **Artista, Álbum e Ano de lançamento** e clique em
   **Aplicar filtros**. O contador mostra quantas músicas da coleção estão visíveis.
   **Limpar filtros e pesquisa** restaura a lista completa.
   Cada seletor inclui uma pesquisa para reduzir suas opções enquanto você digita,
   sem diferenciar acentos ou maiúsculas. Escolha uma opção antes de aplicar;
   digitar nessa pesquisa não altera a seleção nem consulta o Spotify.
9. Use **Atualizar músicas** para buscar mudanças no Spotify. Pesquisar e filtrar
   usam somente a lista já importada, sem novas consultas ou alteração dos itens.
10. Use **Desconectar Spotify** para apagar a conexão salva e voltar à Landing Page.

**Já usava a versão anterior?** A conexão pode não incluir `user-library-read`.
Se a importação pedir uma nova autorização, use **Reconectar Spotify**, autorize
no site oficial. A coleção será carregada ao retornar ao painel.
Não é necessário Client Secret.

As **Músicas Curtidas** são uma coleção nativa do Spotify, não uma playlist comum
chamada “Music Liked”. Não procuramos playlists por nome.

Se já houver uma conexão salva, **Abrir painel** leva ao seu espaço e carrega a
coleção na primeira entrada dessa sessão do navegador. A Landing Page não faz
essa consulta. A presença da credencial local, por si só, não comprova validade
da autorização; falhas na carga são informadas com opção de tentar novamente ou
reconectar. Não consultamos perfil, nome ou email. A conexão é lembrada no armazenamento seguro do Windows, e não em
arquivos de texto. Para encerrar o servidor, pressione
**Ctrl+C** no terminal. Fechar a aba não encerra o servidor.

O serviço escuta somente neste computador, não na rede local.
Se a porta estiver ocupada, encerre a instância anterior; não altere o endereço de
retorno sem atualizar também a configuração da aplicação e o painel Spotify.

## Experimentar sem conectar sua conta

```powershell
.\.venv\Scripts\python.exe -m music_curator serve --demo
```

Abra **http://127.0.0.1:8765** para explorar o **protótipo com aparência SaaS**:

1. **Home:** apresentação do produto, explicação da proposta e botão **Entrar**.
2. **Entrar:** botão **Continuar com Spotify**, sem formulário de cadastro ou senha.
3. **Meu espaço:** painel com menu lateral e acesso a **Minhas Músicas**.
4. **Minhas Músicas:** já abre com seis músicas fictícias carregadas na entrada,
   com datas de lançamento, pesquisa e filtros. **Atualizar músicas** recarrega
   os exemplos, sem acessar Spotify, rede ou cofre de credenciais.
5. **Sair da ferramenta:** encerra a sessão simulada e descarta a lista.

No modo demo, o botão do Spotify **apenas simula a entrada**; não abre uma autorização real.
Não há base de usuários, consulta à API, acesso ao cofre do Windows ou persistência
da biblioteca em disco. Navegação e lista importada ficam temporariamente na memória,
separado por sessão do navegador. Curadoria aparece como **Em breve**, sem ação.

Essa é uma proposta visual para uma futura experiência de entrada com Spotify:
**não é uma aplicação SaaS hospedada nem uma implementação de autenticação
multiusuário**. O modo real, sem `--demo`, usa o mesmo visual com autorização
Spotify real e ação **Desconectar Spotify**. A conexão pertence à instalação local,
não a usuários isolados por navegador.
Encerre a demonstração antes de iniciar a versão real na mesma porta.

## Playlists Gerenciadas — configuração por artista

No menu lateral, **Playlists Gerenciadas** abre as opções de organização.
Clique em **Por artista** para abrir uma janela de configuração:

Ao abrir pelo cartão, a aplicação já relê as Músicas Curtidas em background,
mantendo o pop-up aberto e mostrando o andamento. Essa leitura não salva a
configuração nem altera playlists; os botões de envio aguardam sua conclusão.

1. Pesquise os artistas encontrados na coleção importada de Músicas Curtidas.
   A seleção já salva para a conta é marcada ao abrir; uma configuração nova
   começa sem artistas selecionados. O ID da playlist vinculada aparece ao lado
   do artista, com rótulo e valor na mesma linha; quando ainda não existe vínculo,
   isso também é indicado. Em telas pequenas, a referência pode ficar abaixo do
   nome para preservar a leitura.
2. Selecione um ou mais artistas. **Selecionar todos** inclui todos os artistas,
   inclusive os ocultos pela pesquisa; **Limpar seleção** desmarca todos.
   **Atualizar**, entre Cancelar e Configurar, relê todas as Músicas Curtidas em
   background e atualiza os artistas e suas contagens no próprio pop-up, sem
   recarregar a página. Preserva a pesquisa e as marcações disponíveis; artistas
   novos entram desmarcados. Se algum marcado sair da coleção, um aviso pede
   revisão. Essa ação não salva a configuração nem altera playlists no Spotify.
   Se falhar, a lista anterior permanece com um aviso de erro.
3. Clique em **Configurar**. No modo demo, isso guarda apenas a seleção nesta sessão.
   No modo real, primeiro verifica permissões e todos os vínculos envolvidos.
   Se houver conflito impeditivo, o envio inteiro é interrompido antes de alterar
   playlists ou persistir a nova seleção. O próprio pop-up é reaberto com os avisos
   por artista e as marcações enviadas preservadas para edição.
   Sem conflitos, salva a seleção por conta e cria ou sincroniza as playlists públicas
   selecionadas. A sincronização substitui o conteúdo para refletir exatamente as
   músicas elegíveis das Músicas Curtidas; faixas manuais ou removidas da coleção
   também saem da playlist.
4. Clique novamente no cartão **Por artista** para reabrir e editar a configuração.
   Cancelar ou fechar a janela descarta apenas as alterações ainda não salvas.
   A seleção real persiste entre sessões para a mesma conta. Cada salvamento
   aplica a seleção: artistas desmarcados saem de **Sua Biblioteca** e, após
   confirmação, seus IDs são apagados do registro local; a playlist não é excluída
   e pode continuar pública ou acessível por link. Se selecionado novamente,
   uma nova playlist será criada. Uma seleção vazia remove todas as playlists
   gerenciadas da biblioteca.

As colaborações entram no agrupamento de cada artista participante selecionado.
Uma mesma faixa não se repete dentro da playlist do artista. Artistas com IDs
distintos não são mesclados mesmo que tenham o mesmo nome; sem ID, a configuração
informa que o agrupamento foi feito por nome. Itens sem identificação de faixa
ou artista utilizável são desconsiderados com aviso.

Os filtros de Minhas Músicas não limitam a sincronização: ela usa a coleção completa
da sessão. Atualizar a coleção não descarta a seleção; dados antigos enviados por
outra aba são recusados. O registro local
`.local-data/spotify/managed-playlists.json` guarda seleção, IDs e estados por
conta/artista, sem conteúdo de músicas, e permanece ao desconectar.
O caminho é ancorado na raiz do projeto, não no diretório de onde o processo foi
iniciado, para que reiniciar a aplicação não crie outro registro por engano.
O modo demo oferece somente a configuração local com músicas e artistas fictícios;
ele nunca chama endpoints Spotify de escrita.

Falhas parciais são mostradas por artista com ID/link quando disponível. Um timeout
durante a criação bloqueia uma nova criação automática até resolução manual; o app
não repete mutações incertas. A conclusão só é exibida após leitura de volta e
comparação das faixas.

### Playlists removidas manualmente e conflitos

O **Configurar** verifica se os IDs mapeados continuam em **Sua Biblioteca**, além de
validar proprietário, visibilidade pública, ausência de colaboração e acesso às
faixas que serão sincronizadas. Abrir a configuração apenas mostra os IDs locais;
a verificação remota ocorre no salvamento, sem polling.

Os problemas aparecem no próprio pop-up, com destaque nos artistas afetados e o ID
antigo preservado. No rodapé ficam **Cancelar**, **Atualizar** e **Confirmar e Salvar**.
Para reconectar, cancele e use a opção de desconexão no painel antes de conectar
novamente, sem uma tela de conflitos separada.

Quando a ausência na biblioteca é confirmada, desmarcar o artista e clicar em
**Confirmar e Salvar** limpa o vínculo local sem recriação ou remoção remota. Se quiser
continuar gerenciando o artista, mantenha-o marcado e use o mesmo **Confirmar e Salvar**.
A confirmação considera as marcações atuais do pop-up, cria substitutas apenas
para os ausentes selecionados e também aplica o restante da seleção. Outros
conflitos impeditivos continuam bloqueando o envio inteiro.
A playlist antiga pode continuar existindo e acessível por link. Cancelar ou fechar
o pop-up de conflitos retorna à configuração persistida, sem gravar as edições.
Atualizar invalida confirmações anteriores e exige uma nova validação antes de
aplicar a configuração. Após atualizar, cancelar descarta somente as marcações
em edição; a coleção recém-importada continua disponível em Minhas Músicas.

Timeout, acesso negado, limite de consultas, respostas inválidas e falhas do Spotify
não comprovam ausência: preservam o ID e não liberam substituição. Um erro 404 nos
detalhes, isoladamente, também não autoriza criar outra. Alterações de propriedade,
privacidade ou colaboração não são revertidas automaticamente.

A confirmação é de uso único, expira em dez minutos e exige nova validação da conta,
coleção e vínculos. Cancelar, atualizar a coleção ou mudar os vínculos invalida a
confirmação. A validação não é uma transação no Spotify: mudanças externas ou falhas
depois do início das mutações ainda podem gerar resultados parciais por artista.

## Limites desta entrega

- O painel não navega nem lista todas as playlists da conta. Só consulta os detalhes
  e itens dos IDs registrados localmente para verificar propriedade e sincronização.
- Lê Músicas Curtidas automaticamente ao entrar no painel, uma vez por sessão,
  e novamente ao clicar em **Atualizar músicas**. Não lê o conteúdo de outras
  playlists nem consulta email ou nome de perfil; a sincronização consulta somente
  o ID imutável da conta. Não há sincronização contínua.
- Navegação, pesquisa e filtros reutilizam a coleção carregada. Uma coleção vazia
  também conta como carga concluída. Falhas não são repetidas a cada navegação:
  use **Atualizar músicas** ou **Reconectar Spotify**, conforme a mensagem.
- A configuração por artista pode criar/renomear playlists públicas e substituir
  suas faixas; ao desmarcar, remove a playlist apenas de **Sua Biblioteca**, sem
  exclusão permanente. Não há sincronização contínua.
- Não classifica gêneros nem envia conteúdo Spotify a uma IA.
- As capas usam os endereços já recebidos na importação, sem novas permissões.
  No modo real, o navegador baixa as miniaturas diretamente do CDN oficial
  `https://i.scdn.co/image/`, sem tokens Spotify ou envio da URL local como
  referência. Isso compartilha o IP do navegador com o CDN; o navegador pode
  armazenar imagens em cache. O aplicativo não grava cópias das capas em disco.
  No demo, as capas são ilustrações fictícias locais, sem acesso ao CDN.
- A importação percorre todas as páginas retornadas, sem truncar em 50 músicas.
  Se qualquer página falhar, não publica uma lista parcial; em falhas transitórias,
  mantém a última importação concluída com aviso. Falhas que exigem reconexão
  descartam a lista exibida.
- A coleção pode mudar durante a paginação; a API não garante uma fotografia
  imutável enquanto a importação está em andamento.
- Não salva a biblioteca em disco. A lista pertence à sessão do navegador e fica
  indisponível ao expirar, sair, desconectar ou reiniciar o servidor. Reautorizar
  limpa as importações de todas as sessões desta instalação.
- Pesquisa e filtros também ficam na memória da sessão, sem incluir nomes ou
  termos pesquisados na URL. Datas de lançamento ausentes aparecem como
  **Não informada**; não são substituídas pela data em que a música foi curtida.
- Desconectar é local. Para revogar o acesso no Spotify, use
  [Aplicativos da sua conta](https://www.spotify.com/account/apps/).

Veja [privacidade e operação](docs/privacy.md) e [decisões técnicas](docs/architecture.md).

## Validar o projeto

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe .\scripts\check_no_private_data.py
.\.venv\Scripts\python.exe .\scripts\check_secrets.py
.\.venv\Scripts\python.exe -m build
```

Os testes usam exclusivamente dados sintéticos e não precisam de conta Spotify.
O CI executa essas verificações e detect-secrets, sem publicar dados privados como artifacts.
O scanner é local e não verifica credenciais em serviços externos.
O verificador de caminhos privados verifica somente arquivos **já rastreados**:
antes de cada commit, confira também `git status` e o conteúdo preparado.

Para ativar o scanner de segredos antes dos commits, caso tenha o comando `pre-commit`
instalado, execute `pre-commit install`. A configuração está incluída no projeto.
O CI também executa o scanner independentemente desse hook local.

## Atualizações de dependências

A configuração de atualizações de versões do Dependabot foi removida desta
branch. A remoção só altera o agendamento do GitHub quando chegar à branch padrão
do repositório (`main`).

Revise atualizações de dependências Python e GitHub Actions e os resultados do CI
antes de fazer merge. O projeto usa intervalos de versões sem lockfile; novas
instalações podem obter versões mais recentes dentro desses intervalos.

Os **Dependabot alerts** e **Dependabot security updates** são recursos separados,
controlados pelas configurações de segurança do repositório no GitHub. A remoção
do arquivo de agendamento não os desabilita.

## Validação manual com sua conta

Após configurar seu aplicativo, confira:

1. Entrar pela Landing Page, autorizar no Spotify e chegar ao painel.
2. Abrir **Minhas Músicas** e conferir que a lista já está pronta, sem clique de importação.
3. Conferir a coleção e atualizar para buscar mudanças feitas no Spotify. Se solicitado, reconectar para
   autorizar `user-library-read`.
   Conferir as cinco colunas, combinar pesquisa e filtros e limpar para recuperar
   a lista completa sem nova importação.
4. Encerrar o servidor, iniciar novamente e usar **Abrir painel** com a conexão salva;
   uma nova coleção deve ser carregada automaticamente, sem reaproveitar dados
   do processo anterior.
5. Usar **Desconectar Spotify** e verificar o retorno à Landing Page; o painel deve
   voltar a encaminhar para a entrada.
6. Cancelar uma autorização e verificar a mensagem na tela de entrada, sem conexão falsa.

Esse teste real deve ser realizado por você, sem compartilhar respostas, tokens,
capturas da biblioteca ou dados privados com o agente. Testes sintéticos não
comprovam que seu Client ID já está autorizado pelo Spotify.
