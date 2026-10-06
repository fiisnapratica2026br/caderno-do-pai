# Painel da casa

O painel usa os mesmos registros e a mesma identidade do bot Python existente.

## Acesso

No Telegram, envie `/painel` e toque no botão **Abrir meu painel**. A página valida `Telegram.WebApp.initData` no servidor, com assinatura HMAC e validade de uma hora. Fora do Telegram, ela mostra apenas instruções de acesso.

## Disponível

- Mês selecionado: compras, contas pagas e pendentes separadas.
- Categorias: somente compras e contas pagas.
- Todas as contas pendentes: até 100 registros, por vencimento.
- Busca por descrição ou categoria.
- Exclusão com confirmação e verificação de propriedade no banco.
- Confirmação da data de pagamento, sem duplicar a conta.

O painel não adiciona autenticação por email nem altera tabelas, rascunhos ou OCR. A API aceita apenas ações explícitas e obtém a identidade da assinatura do Telegram, nunca de um `user_id` enviado pelo navegador. O navegador não recebe chaves do Telegram, OCR ou Supabase.

## Hospedagem

O Render continua executando `python bot.py`. O servidor HTTP atende o webhook existente em `/telegram`, o painel em `/painel`, os arquivos estáticos e `/api/panel`. `/health` identifica a versão do painel.

Não é necessário criar outro serviço para testar o painel. Caso o frontend seja hospedado na Vercel, configure no Render:

- `PANEL_URL`: URL HTTPS final do frontend.
- `PANEL_ORIGIN`: origem exata do frontend, sem caminho ou barra final.

O backend permanece no Render. O CORS rejeita origens não autorizadas. O frontend em `web/` é estático e não exige chaves de banco no navegador.

## Verificação

`python -m unittest discover -s . -p 'test_panel.py' -v`

Os testes cobrem assinatura falsa/expirada, usuário adulterado, origem não autorizada, webhook protegido, meses com menos de 31 dias, saldo sem contas pendentes e operação restrita à identidade assinada. A validação final exige abrir o botão real no Telegram; os testes locais não substituem essa etapa.

O Render Free pode suspender o serviço por inatividade. Isso pode causar demora no primeiro acesso; este painel não cria cobrança nem altera o plano.
