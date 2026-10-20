# Curador Musical

Uma aplicação pessoal, em português, que roda no seu computador. Esta primeira versão
**conecta ao Spotify e lista suas playlists**, sem ler músicas nem modificar sua conta.

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

1. Clique em **Conectar ao Spotify**.
2. Confira a autorização no site oficial e aceite a leitura de playlists.
3. De volta à tela, clique em **Carregar playlists**.
4. Use **Desconectar** para apagar a conexão salva neste computador.

O acesso será renovado ao carregar playlists. A conexão é lembrada no armazenamento
seguro do Windows, e não em arquivos de texto. Para encerrar o servidor, pressione
**Ctrl+C** no terminal. Fechar a aba não encerra o servidor.

O serviço escuta somente neste computador, não na rede local.
Se a porta estiver ocupada, encerre a instância anterior; não altere o endereço de
retorno sem atualizar também a configuração da aplicação e o painel Spotify.

## Experimentar sem conectar sua conta

```powershell
.\.venv\Scripts\python.exe -m music_curator serve --demo
```

Abra o mesmo endereço e use os mesmos botões. Um aviso identifica a demonstração:
todos os dados são fictícios, não há chamadas de rede ao Spotify nem credenciais
salvas no Windows. Encerre a demonstração antes de iniciar a versão real.

## Limites desta entrega

- A lista reflete as playlists que a API disponibiliza à conta e ao aplicativo,
  não uma garantia de acesso a todo conteúdo do Spotify.
- Não lê faixas, músicas curtidas, perfil ou email.
- Não cria, renomeia, adiciona nem remove conteúdo.
- Não classifica gêneros nem envia conteúdo Spotify a uma IA.
- Não salva a biblioteca ou a lista em disco; uma nova consulta atualiza a tela.
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

1. Conectar, autorizar e carregar as playlists.
2. Encerrar o servidor, iniciar novamente e carregar sem novo login.
3. Desconectar e verificar que os botões voltam ao estado desconectado.
4. Cancelar uma autorização e verificar a mensagem, sem conexão falsa.

Esse teste real deve ser realizado por você, sem compartilhar respostas, tokens,
capturas da biblioteca ou dados privados com o agente. Testes sintéticos não
comprovam que seu Client ID já está autorizado pelo Spotify.
