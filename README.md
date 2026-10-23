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
3. Confira a autorização no site oficial. As permissões `playlist-read-private`
   e `user-library-read` permitem apenas leitura.
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

## Limites desta entrega

- O painel não consulta nem lista playlists nesta etapa. A capacidade de leitura
  preexistente permanece coberta por testes, sem botão ou link na nova interface.
- Lê Músicas Curtidas automaticamente ao entrar no painel, uma vez por sessão,
  e novamente ao clicar em **Atualizar músicas**. Não lê o conteúdo de outras
  playlists, perfil ou email. Não há sincronização contínua.
- Navegação, pesquisa e filtros reutilizam a coleção carregada. Uma coleção vazia
  também conta como carga concluída. Falhas não são repetidas a cada navegação:
  use **Atualizar músicas** ou **Reconectar Spotify**, conforme a mensagem.
- Não cria, renomeia, adiciona nem remove conteúdo.
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
