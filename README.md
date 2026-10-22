# Curador Musical

Uma aplicação pessoal, em português, que roda no seu computador com **interface
inspirada em SaaS**: Landing Page, entrada pelo Spotify e painel do produto.
Nesta etapa, o painel apresenta somente a estrutura visual: não carrega playlists
nem executa automações ou modifica sua conta.

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
3. Confira a autorização no site oficial. A permissão existente é somente de leitura
   de playlists; o painel não usa essa permissão para consultar sua biblioteca.
4. Após autorizar, você chega ao **Meu espaço**, ainda sem recursos de playlists
   ou automações.
5. Use **Desconectar Spotify** para apagar a conexão salva e voltar à Landing Page.

Se já houver uma conexão salva, **Abrir painel** leva diretamente ao seu espaço.
Isso indica apenas a presença da credencial local, não uma validação atual da
autorização no Spotify. Não consultamos perfil, nome, email ou biblioteca ao entrar
no painel. A conexão é lembrada no armazenamento seguro do Windows, e não em
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
3. **Meu espaço:** painel com navegação lateral, boas-vindas e estrutura visual,
   sem listas ou contagens de playlists.
4. **Sair da ferramenta:** encerra a sessão simulada e volta para a Home.

No modo demo, o botão do Spotify **apenas simula a entrada**; não abre uma autorização real.
Não há base de usuários, consulta à API, acesso ao cofre do Windows ou persistência
da biblioteca. Somente o estado de navegação fica temporariamente na memória,
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
- Não lê faixas, músicas curtidas, perfil ou email.
- Não cria, renomeia, adiciona nem remove conteúdo.
- Não classifica gêneros nem envia conteúdo Spotify a uma IA.
- Não salva a biblioteca ou listas em disco.
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
2. Verificar que o painel não carrega biblioteca nem oferece ações de automação.
3. Encerrar o servidor, iniciar novamente e usar **Abrir painel** com a conexão salva.
4. Usar **Desconectar Spotify** e verificar o retorno à Landing Page; o painel deve
   voltar a encaminhar para a entrada.
5. Cancelar uma autorização e verificar a mensagem na tela de entrada, sem conexão falsa.

Esse teste real deve ser realizado por você, sem compartilhar respostas, tokens,
capturas da biblioteca ou dados privados com o agente. Testes sintéticos não
comprovam que seu Client ID já está autorizado pelo Spotify.
