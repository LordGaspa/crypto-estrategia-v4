# Deploy do radar de sinais (app_v4.py) pra acessar do celular

O repositório git local já está pronto (`git log` mostra o commit inicial). Faltam só os passos
que precisam da sua conta — eu não tenho como fazer login por você.

## 1. Criar o repositório no GitHub

Recomendo **privado** — o repo contém os parâmetros da sua estratégia e os resultados do
holdout, não é algo que eu deixaria público por padrão.

```bash
gh auth login
gh repo create SEU-USUARIO/crypto-estrategia-v4 --private --source=. --remote=origin --push
```

Se preferir sem o `gh` CLI: crie o repo vazio em github.com/new (marcado como **Private**), depois:

```bash
git remote add origin https://github.com/SEU-USUARIO/crypto-estrategia-v4.git
git branch -M main
git push -u origin main
```

## 2. Conectar no Streamlit Community Cloud

1. Acesse [share.streamlit.io](https://share.streamlit.io) e faça login com a mesma conta GitHub.
2. "New app" → autorize o Streamlit a acessar repositórios privados quando pedir.
3. Selecione o repositório `crypto-estrategia-v4`, branch `main`, arquivo principal `app_v4.py`.
4. Deploy. A primeira execução vai demorar um pouco mais (baixa ~8 anos de candles de 22 ativos
   direto da Binance pra montar o cache — depois disso fica rápido, o cache dura 24h).

## 3. Restringir quem pode ver (opcional, recomendado)

No painel do app no Streamlit Cloud: Settings → Sharing → "Only specific people can view this
app" → adicione só o seu e-mail. Sem isso, mesmo com repo privado, qualquer um com o link do app
consegue abrir.

## O que já está preparado

- `.gitignore` exclui `cache_dados/`, `.venv/`, CSVs grandes de otimização (não usados em tempo
  de execução) e logs — o repo ficou só com código + os 5 CSVs pequenos que o app realmente lê.
- `requirements.txt` com as versões exatas já testadas localmente.
- Testei local um clone limpo + venv novo + `pip install -r requirements.txt`, sem
  `cache_dados/` nem nada pré-existente, pra confirmar que o app sobe do zero igual vai subir no
  Streamlit Cloud.

## Se algo der errado no deploy

O log de erro fica em "Manage app" → "Logs", no próprio site do Streamlit Cloud. Os erros mais
prováveis: alguma versão de pacote incompatível com o Python do Streamlit Cloud (nesse caso, é
só relaxar a versão fixa de um pacote específico no `requirements.txt`), ou timeout na primeira
carga por causa do download inicial de todos os candles.
