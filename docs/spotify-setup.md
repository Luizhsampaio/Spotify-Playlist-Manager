# Conectar seu aplicativo ao Spotify

## Pré-requisitos

- Conta Spotify Premium do proprietário do aplicativo.
- Projeto preparado conforme o [README](../README.md).
- Navegador e acesso à internet. Não é necessário pagar por hospedagem.

## Criar o aplicativo

1. Acesse o [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   e entre com sua conta Spotify.
2. Escolha **Create app**. Os nomes dos campos podem variar conforme o painel.
3. Use um nome descritivo, por exemplo **Curador Musical Pessoal**.
4. Descreva o uso: aplicação pessoal local para visualizar Músicas Curtidas e playlists.
5. Selecione **Web API** entre as APIs utilizadas.
6. Cadastre exatamente este **Redirect URI**:

   ```text
   http://127.0.0.1:8765/callback
   ```

   Use `127.0.0.1`, não `localhost`; não acrescente uma barra ao final.
   O HTTP é permitido para esse endereço de loopback local.
7. Leia os termos aplicáveis, conclua o cadastro e abra as configurações do aplicativo.
8. Copie apenas o **Client ID**. Não precisamos de Client Secret.
9. Se a conta usada no login não for a proprietária, verifique **Users Management**
   e sua inclusão na lista autorizada. O Development Mode possui limites de usuários
   e endpoints; esta entrega não solicita acesso público ampliado.

Se o painel exigir um Website, use um endereço válido seu conforme as regras do
Spotify. Não confunda esse campo com o Redirect URI local obrigatório.

## Configurar no computador

O script de preparação cria `.env`. Abra esse arquivo local no VS Code e preencha:

```dotenv
SPOTIFY_CLIENT_ID=cole_o_client_id_do_seu_aplicativo
SPOTIFY_AI_POLICY_MODE=blocked
```

O valor real do Client ID tem 32 caracteres hexadecimais. O texto acima é apenas
um marcador, não uma configuração executável.

Salve e reinicie o servidor. Não cole esses dados no chat; o agente não precisa
ler sua configuração. Embora o Client ID seja um identificador público, mantemos
a configuração da conta fora do Git. Nunca coloque Client Secret ou tokens ali.

## Autorizar

1. Execute o comando de inicialização do [README](../README.md).
2. Abra http://127.0.0.1:8765.
3. Clique em **Entrar** e depois em **Continuar com Spotify**.
4. Confira que o login está no domínio oficial do Spotify.
5. Autorize somente leitura: `playlist-read-private` e `user-library-read`.
6. Ao retornar ao painel, a coleção é carregada automaticamente.
   Abra **Minhas Músicas** no menu lateral para ver a lista já pronta.

Conexões de versões anteriores precisam incluir a nova permissão de biblioteca.
Se a tela indicar falta de permissão, use **Reconectar Spotify**, autorize novamente
e a aplicação carregará a coleção ao retornar ao painel. Essa leitura usa a coleção nativa, não uma playlist chamada
“Music Liked”. A lista fica somente em memória; **Atualizar músicas** consulta novamente.

A aplicação usa Authorization Code com PKCE e verifica o retorno da autorização.
O refresh token fica no Gerenciador de Credenciais do Windows, com serviço
`music-curator.spotify` e seu Client ID como identificador.

## Problemas comuns

| Situação | O que fazer |
|---|---|
| Client ID não configurado | Preencha `.env`, salve e reinicie. |
| Redirect URI inválido | Confira endereço, porta, caminho e ausência de barra final. |
| Acesso negado (403) | Verifique Premium, permissões, conta autorizada e restrições do aplicativo. |
| Autorização cancelada | Clique novamente em conectar quando quiser autorizar. |
| Limite de consultas (429) | Aguarde; não fique clicando repetidamente. |
| Conexão revogada ou expirada | Autorize novamente. |
| Falta de `user-library-read` | Use Reconectar Spotify em Minhas Músicas e autorize a leitura da biblioteca. |
| Armazenamento seguro indisponível | Verifique o Gerenciador de Credenciais do Windows; não substitua por arquivo de tokens. |
| Porta 8765 ocupada | Encerre a instância anterior com Ctrl+C no terminal correspondente. |

Não compartilhe mensagens brutas da API, URLs de retorno com parâmetros ou tokens.
As mensagens da interface foram pensadas para não expor esses valores.

## Fontes oficiais

- [OAuth PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Redirect URIs](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri)
- [Development Mode](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)
- [Listagem de playlists](https://developer.spotify.com/documentation/web-api/reference/get-a-list-of-current-users-playlists)
- [Músicas Curtidas](https://developer.spotify.com/documentation/web-api/reference/get-users-saved-tracks)
