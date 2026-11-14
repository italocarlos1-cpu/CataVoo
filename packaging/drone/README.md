# Gerar o DroneHighlighter.exe

O `.exe` do Windows precisa ser gerado **no Windows** (o PyInstaller não gera `.exe` a partir do Linux).
Existem dois caminhos, e os dois usam a mesma receita (`DroneHighlighter.spec`).

## A) No seu PC (duplo clique)
1. Instale o **Python 3.12** em python.org, marcando *Add python.exe to PATH*.
2. Dê dois cliques em `packaging\drone\build_exe.bat`. Ele cria um ambiente isolado (`.venv-build`),
   instala as dependências, gera o programa, faz um autoteste e compacta tudo.
3. O resultado fica em `dist\DroneHighlighter\DroneHighlighter.exe` e `dist\DroneHighlighter-VERSÃO-win64.zip`.

## IA de paisagens (CLIP) depois, sem gerar de novo
Ao lado do `.exe` vai o **`Instalar_IA_Paisagens.bat`**. Com o programa fechado, dois cliques nele baixam o
PyTorch (com CUDA se houver placa NVIDIA; `/cpu` força a versão sem placa) e o transformers para
`packs\clip\site-packages`, e já baixam o modelo CLIP. Não precisa de Python no PC: o instalador usa um
Python temporário e apaga depois. Ao abrir, o programa acha o pacote sozinho (o mesmo mecanismo de
"packs" do VideoHighlighter, `packaging/packs/rthook_packs.py`). Para desinstalar, apague `packs\clip`.

O workflow do GitHub testa isso de verdade: numa cópia da pasta, roda o `.bat /silencioso /cpu` e confere
que o `DroneHighlighter-cli.exe` classifica as paisagens com o CLIP.

`build_exe.bat clip` ainda existe para quem quer o CLIP *dentro* do `.exe` (vários GB, sem internet depois).

## B) No GitHub (sem instalar nada)
1. Envie o projeto para o seu fork no GitHub.
2. Na aba **Actions**, escolha **Build DroneHighlighter.exe** e clique em **Run workflow**.
3. No fim, baixe o artefato **DroneHighlighter-VERSÃO-win64** (ex.: `DroneHighlighter-0.5.5-win64`, um .zip; extraído, vira uma pasta com o mesmo nome, então versões diferentes não se misturam). Antes de gerar o programa, o workflow
   também roda os testes do DroneHighlighter no Windows.

## O que vai dentro
- `DroneHighlighter.exe` é a janela; `DroneHighlighter-cli.exe` é a linha de comando (mesmas opções de `python -m drone.cli`).
- O ffmpeg vem embutido (imageio-ffmpeg), então não é preciso instalar ffmpeg nem Python no PC que vai usar.
- A pasta precisa ficar inteira, porque o `.exe` usa os arquivos de `_internal`. Para levar a outro PC, copie a pasta ou o `.zip`.
- O `.exe` não traz o PyTorch: até rodar o `Instalar_IA_Paisagens.bat`, as paisagens são reconhecidas pela cor e o restante funciona igual.
- O cache e as configurações ficam em `%LOCALAPPDATA%\DroneHighlighter`, compartilhados com a versão em Python.

## Ícone
`DroneHighlighter.ico` (16 a 256 px) e `DroneHighlighter.png` (512 px) foram gerados a partir da arte
enviada pelo usuário (drone com hélices âmbar e câmera). Para trocar o ícone, substitua os dois arquivos
e gere o .exe de novo.

## Licença
O programa empacotado é AGPL-3.0. Ao distribuir o `.exe`, disponibilize também o código-fonte
(por exemplo, o link do seu fork). O ffmpeg embutido é GPL; o PySide6/Qt é LGPL.
