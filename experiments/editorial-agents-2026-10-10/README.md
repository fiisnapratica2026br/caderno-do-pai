# Testes dos exemplos públicos de agentes

Snapshot do projeto público [Shubhamsaboo/awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps), sob Apache-2.0. Este diretório contém somente o código e as fixtures públicas dos exemplos Typed Agentic RAG e First Reader. Não contém relatórios, resultados de testes com arquivos do usuário, rascunhos editoriais ou credenciais.

Execute em ambiente isolado com Python 3.12:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r vendor/typed_rag/requirements.txt
python vendor/typed_rag/test_typed_rag.py
python vendor/agent_skills/evals/first-reader/test_first_reader.py
```

As duas suítes usam fixtures e não fazem chamadas pagas a modelos de IA. Sua aprovação cobre os controles técnicos presentes nas suítes; não comprova qualidade editorial ou correspondência semântica entre afirmações e citações.

Para iniciar a interface:

```bash
cd vendor/typed_rag
streamlit run app.py
```

Perguntas com geração real dependem de uma chave de API fornecida via ambiente. Não salvar a chave no código. A licença original está em `vendor/LICENSE-awesome-llm-apps`.
