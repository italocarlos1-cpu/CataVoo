# Como instalar o CataVoo (Windows 11)

## Jeito mais simples: gerar o .exe
Depois do passo 2 abaixo (copiar os arquivos para o fork), instale o Python 3.12 e dê dois cliques em
`packaging\drone\build_exe.bat`. O programa fica em `dist\CataVoo\CataVoo.exe`.
Outra opção: no GitHub, aba Actions → "Build CataVoo.exe" → Run workflow. Detalhes em `packaging/drone/README.md`.
Para a IA de paisagens (CLIP), rode depois o `Instalar_IA_Paisagens.bat` que fica na pasta do `.exe`.

## Jeito completo (rodar pelo Python)

1. No GitHub, faça um **fork** de https://github.com/Aseiel/VideoHighlighter e clone-o:
   git clone https://github.com/SEU_USUARIO/VideoHighlighter.git
   cd VideoHighlighter
   git remote add upstream https://github.com/Aseiel/VideoHighlighter.git
   git checkout -b drone

2. Copie o conteúdo deste zip para a raiz do clone (as pastas `drone/`, `tests/` e `docs/` se mesclam).

3. Crie um ambiente Python 3.11+ e instale as dependências do VideoHighlighter:
   python -m venv .venv
   .venv\Scripts\activate
   pip install torch --index-url https://download.pytorch.org/whl/cu128   (CUDA para a placa NVIDIA)
   pip install -r requirements.txt

4. Teste:  python -m pytest tests/test_drone_*.py -q   (70 testes)
5. Use:    python -m drone      (janela)

Para receber as atualizações do original:  git fetch upstream && git merge upstream/main
(o pacote drone/ não altera nenhum arquivo do original, então o merge não gera conflitos)

## IA opcional (só se quiser)
- Local, grátis: instale o Ollama (ollama.com) e rode `ollama pull qwen3:8b`, ou abra o LM Studio com um modelo carregado e o servidor ligado.
- Paga: crie uma chave de API na OpenAI ou na Anthropic e cole no campo "Chave da API".
- Na janela, marque "Assistente de IA" e clique em "Testar conexão".

## Edição manual (timeline)
- Na janela principal, clique em "✂ Edição manual" (embaixo, ao lado de REFAZER e Outra versão).
- Troque o estilo na barra de cima para ver os cortes de cada estilo sem renderizar; ajuste com o mouse ou com as setas (1 quadro).
- Clique em "RENDERIZAR ESTE ESTILO" quando estiver bom. Detalhes na seção 13 do manual.
