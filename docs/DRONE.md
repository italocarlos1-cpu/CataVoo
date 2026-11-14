# DroneHighlighter — edição automática de vídeos de drone

> **Manual completo para o usuário:** `docs/DroneHighlighter_Manual.html` (ou `.pdf`). Este arquivo é a referência técnica.

Camada especializada em filmagens aéreas, construída sobre o VideoHighlighter.
Tudo roda localmente: não usa API externa, não cobra assinatura e funciona offline
depois que as dependências e o modelo CLIP estão instalados.

```text
python -m drone                       # janela (Selecionar pasta → GERAR VÍDEO)
python -m drone.cli --pasta "C:\VideosDrone\Pirenopolis\Brutos" --estilo cinematografico ^
    --duracao 90 --titulo "Pirenópolis — Goiás" --musicas "C:\Musicas" --formato ambos
python -m drone.cli --pasta ... --so-analisar   # só ranqueia os trechos, não renderiza
```

## Estilos de edição

| Estilo | Tomadas | Transições | Características |
|---|---|---|---|
| **Automático** | conforme o estilo escolhido | — | analisa a viagem e escolhe um dos estilos abaixo, com ritmo adaptativo |
| Cinematográfico | 3,5–6,5 s | metade das trocas com fusão | equilíbrio entre impacto e respiro |
| Natureza contemplativa | 5–9 s | fusões de 1 s | prefere cachoeira, serra e água; termina com afastamento |
| Pôr do sol / golden hour | 5,5–9 s | fusões de 1,2 s | prefere luz dourada; termina no pôr do sol e desacelera no final |
| Minimalista | 7–12 s | fusões de 1,5 s | poucas tomadas, longas e paradas |
| Cidade histórica / arquitetura | 3,5–6 s | 40% de fusões | valoriza órbitas e revelações do centro histórico |
| Diário de viagem | 3–5,5 s | 35% de fusões | segue a ordem em que você filmou |
| Trailer épico | 2–5 s | 30% de fusões | começa lento e grandioso e acelera até cortes rápidos (curva de ritmo) |
| Viagem dinâmica | 1,8–3,6 s | cortes secos | variedade de lugares, ritmo na batida |
| Aventura / FPV radical | 1,2–2,6 s | cortes secos | favorece sobrevoos e velocidade |
| Redes sociais | 1,5–3 s | cortes secos | gancho forte no início, ideal para Reels e Shorts |

Os estilos ficam em `drone/config.py`. Cada um pode definir: faixa de duração das
tomadas, transição, faixa de BPM e palavras-chave da música, pesos da pontuação,
paisagens preferidas, abertura e fechamento padrão, curva de ritmo
(`pace_curve`), ordem cronológica (`chronological`), preferência por tipo de
movimento (`movement_bias`) e ritmo adaptativo (`adaptive`).

**Modo automático** (`drone/style_advisor.py`, sem IA): monta um perfil da viagem a
partir da análise já feita, com a porcentagem de cada paisagem, os movimentos em
blocos de 3 s, a parcela de imagens estáveis, a variedade, o intervalo de horas
entre as gravações e a distância entre os pontos de GPS. Depois dá uma nota a cada
estilo e mostra no log o motivo da escolha e as alternativas, por exemplo:
"Estilo automático: Pôr do sol (0,86): 30% das cenas com luz de pôr do sol."

**Ritmo adaptativo:** a duração de cada tomada segue o tipo dela. Tomadas paradas,
panorâmicas e órbitas ficam mais tempo na tela; sobrevoos rápidos e tomadas medianas
ficam menos. A duração total e o encaixe na música não mudam.

## Duração, falta de material e um vídeo por grupo

- **Duração automática** (`drone/material.py`): `duration=0` / `--duracao auto`. Conta as tomadas
  distintas e fortes e sugere o tempo (horizontal 15–180 s, vertical 15–60 s).
- **Falta de material**: `Job.shortfall` = `perguntar` (padrão na janela) | `encurtar` | `esticar`
  (padrão na CLI, `--se-faltar`). Com "perguntar", a janela mostra um diálogo antes do render.
- **Um vídeo por grupo** (`drone/grouping.py`): `Job.group_by` = `nada` | `voo` (intervalo de mais de 10 min
  entre gravações) | `dia` | `local` (GPS, raio de 1,5 km) | `subpasta`, e `summary=True` (`--resumo`)
  para gerar também o vídeo da viagem inteira. A análise é feita uma vez; cada grupo tem
  a sua seleção e, no modo Automático, o seu estilo.
- **Música**: volume abaixando nos últimos ~4 s; uma faixa mais curta que o vídeo é repetida.

## v0.5.5 — mini editor, tema escuro, arquivos de trabalho em `projeto/`

- **Edição manual = mini editor.** Abre maximizada (F11 tela cheia). Monitor que toca a sequência
  final com a música sem renderizar (`drone/preview_player.py`: dois QMediaPlayer alternando,
  relógio próprio, música num terceiro player; pausado, o monitor mostra o quadro exato tirado com
  ffmpeg). Tocar tomada (T), clipe original (C), clique/duplo clique numa faixa, barra de posição,
  linha vermelha nas faixas durante a reprodução. Arrastar na sequência reordena de verdade
  (`SequenceStrip.dropEvent`: o InternalMove do Qt em IconMode só mexia nos ícones).
  Cores fixas por paisagem (`theme.CATEGORY_COLOURS`, antes vinham de `hash()`, que muda a cada
  execução) e legenda.
- **Tema** preto e cinza (`drone/theme.py`, uma folha de estilo para as duas janelas); tela
  principal em duas colunas.
- **Sem janelas de cmd piscando**: `drone/win_console.py` põe `CREATE_NO_WINDOW` como padrão em
  todo `subprocess.Popen` da janela (inclui o motor do VideoHighlighter).
- **`_dh_editados/projeto/`** guarda `.edl.yaml`, `_musica.m4a`, `_relatorio.json` e
  `edicao_manual.json` (movido automaticamente da versão anterior).

## Base do VideoHighlighter

A v0.5.4 roda sobre o VideoHighlighter **0.15.1** (merge de `upstream/main` no branch `drone`).
Para atualizar de novo: `git fetch upstream && git merge upstream/main` no branch `drone`, depois
rodar os testes `tests/test_drone_*.py` (o workflow do Windows faz isso a cada push).

## CLIP no .exe — `Instalar_IA_Paisagens.bat` (v0.5.3)

O `.exe` sai sem PyTorch. O `Instalar_IA_Paisagens.bat`, copiado ao lado dele pelo
`DroneHighlighter.spec`, baixa um Python embutido temporário + `pip.pyz` e instala `torch`
(índice `cu126` se `nvidia-smi -L` responder, senão `cpu`) e `transformers` em
`packs\clip\site-packages`, com `--python-version` igual ao do build (`_internal\dh_python.txt`).
Grava `packs\clip\pack.json` (`dep: torch`) e já baixa `openai/clip-vit-base-patch32`.

No arranque, o runtime hook do upstream (`packaging/packs/rthook_packs.py`) acrescenta o pack
ao `sys.path` (no fim: o que vem no bundle sempre ganha). Para isso funcionar o spec segue as
regras de `tools/pack_core_args.py`: torch fica de fora, a biblioteca padrão vai inteira e os
pacotes compartilhados (numpy, PIL, filelock…) vão completos. `drone/landscape.py` carrega o
CLIP direto pelo `transformers` em CUDA fp16 ou CPU fp32, sem OpenVINO. O workflow do Windows
roda o `.bat /silencioso /cpu` numa cópia da pasta e exige `Classificação de paisagem: clip`.

## Edição manual (timeline) — v0.5

- `drone/pipeline.py` agora é separado em `_analyse` (análise), `prepare_group` / `plan_format`
  (plano, sem render) e `render_plan`. A classe `Session` analisa uma vez e planeja qualquer
  grupo/estilo/formato sob demanda (`Session.plan`) e renderiza um plano editado (`Session.render`).
- `drone/edit_session.py`: `EditPlan` editável (aparar com ou sem ímã, ±1 quadro em limites reais
  de quadro, deslizar, inserir/substituir candidato, reordenar, remover, transição por tomada,
  `snap_all`, `to_plan`, JSON) e `EditStore` (`<saída>/edicao_manual.json`, por grupo|estilo|formato).
- `drone/editor_window.py`: janela PySide6 com faixas por vídeo (miniaturas do cache), tomadas
  coloridas, candidatos cinza, arrastar bordas e meio, teclado, imagens exatas de entrada e saída
  (ffmpeg), prévia sem som (QMediaPlayer criado sob demanda, sem QAudioOutput), sequência
  reordenável, troca de estilo/formato sem render (✎ = editado), ímã, Restaurar, Salvar e Renderizar.
- Botão **✂ Edição manual** na tela principal (`gui.py`, `SessionWorker`).
- A duração total pode mudar com as edições (decisão do usuário) e é sempre mostrada.

## Não gostou? Refazer com ajustes (sem IA)

Depois de gerar um vídeo, escreva o que mudar no campo **Refazer com ajustes** e
clique em **REFAZER**. A análise já fica no cache, então só a montagem e o render
são refeitos. O texto é lido por palavras-chave (`drone/feedback.py`):

| Você escreve | O que muda |
|---|---|
| "mais cachoeira", "menos cidade", "sem estrada" | dá preferência ou evita esse tipo de paisagem |
| "cortes mais rápidos" / "tomadas mais longas" | ritmo: duração de cada tomada × 0,75 / × 1,3 |
| "vídeo mais curto" / "mais longo" | duração total × 0,75 / × 1,25 |
| "começar com uma revelação", "terminar com pôr do sol" | escolhe a tomada de abertura ou de encerramento (paisagem ou movimento) |
| "trocar as tomadas", "ficou repetitivo" | evita os trechos usados na versão anterior |
| "sem transições" / "transições suaves" | cortes secos ou fusões |
| "sem tremer", "mais suave" | aumenta o peso da estabilidade |

O botão **Outra versão** gera uma alternativa com a mesma configuração, mas com outras
tomadas e outra ordem. Na linha de comando, use
`--refazer <relatorio.json> --ajustes "..."` e `--variacao 1`, `2`…

## Assistente de IA (opcional, desligado por padrão)

O fluxo principal não usa IA. Para ligar o assistente, marque a caixa **Assistente
de IA** na janela ou use `--ia <provedor>` na linha de comando. A IA pode:

1. **Interpretar** as instruções e os ajustes em linguagem livre. O resultado é o
   mesmo objeto produzido pelo leitor de palavras-chave.
2. **Escolher e ordenar** as tomadas a partir de uma pré-seleção feita pelo sistema,
   que já descartou trechos tremidos, borrados e repetidos. O ritmo dos cortes
   continua preso à música.

**O que é enviado:** apenas texto, ou seja, as instruções e uma tabela dos trechos
(arquivo, tempo, paisagem, movimento, notas). Nunca imagens, vídeo ou áudio.

| Provedor | Onde roda | Custo | Configuração |
|---|---|---|---|
| Ollama | sua GPU | grátis | instale o Ollama e rode `ollama pull qwen3:8b` (≈5 GB, cabe na RTX 4060 de 8 GB) |
| LM Studio | sua GPU | grátis | carregue um modelo e ligue o servidor local; o sistema usa o modelo carregado |
| Outro compatível | sua GPU | grátis | llama.cpp `llama-server`, Jan, etc.: informe o endereço |
| OpenAI | nuvem | pago por uso | chave no campo "Chave da API" ou na variável `OPENAI_API_KEY`; modelo padrão `gpt-5-mini` (editável) |
| Claude | nuvem | pago por uso | chave no campo ou na variável `ANTHROPIC_API_KEY`; modelo padrão `claude-haiku-5-5` (editável) |

Cada execução manda uma ou duas mensagens de poucos milhares de tokens, então o custo
nas APIs pagas é baixo. A chave fica em `%LOCALAPPDATA%\DroneHighlighter\config.json`,
só no seu PC. Se preferir não gravá-la, deixe o campo vazio e use a variável de ambiente.

**Segurança da resposta:** toda resposta da IA é validada. Valores desconhecidos ou
fora de faixa são descartados. Se a IA não responder ou responder algo inválido, o
log mostra o motivo e o sistema segue com o diretor automático. Um problema com a IA
nunca impede a geração do vídeo.

**VRAM:** o CLIP e o LLM local não ficam carregados ao mesmo tempo. O CLIP é liberado
antes que o LLM seja chamado. O Ollama mantém o modelo carregado por alguns minutos,
o que não atrapalha o render, porque o NVENC usa outra parte da GPU.

Teste rápido: `python -m drone.cli --pasta . --ia ollama --testar-ia`, ou o botão
**Testar conexão** na janela.

## Licença

O pacote `drone/`, `tests/test_drone_pipeline.py` e `tests/drone_fixtures.py` são
**AGPL-3.0-or-later**, a mesma licença do VideoHighlighter. Se você distribuir o
programa, ou oferecê-lo pela rede, precisa disponibilizar o código-fonte completo,
incluindo as suas modificações.

Os projetos SkyGrid foram usados **somente como referência de ideias**. Nenhuma linha
foi copiada:

| Projeto | Licença encontrada | Como foi tratado |
|---|---|---|
| skygrid-auto-editor | **nenhum arquivo de licença** (todos os direitos reservados) | só os conceitos, tudo reimplementado |
| skygrid-shot-search | MIT (`pyproject.toml`, README) | só os conceitos; as dependências `ultralytics` (AGPL) e ChromaDB foram evitadas |

| Conceito SkyGrid | Implementação própria aqui |
|---|---|
| Detector de tremor por fluxo LK (penaliza a *magnitude*) | `motion_analysis`: o jitter é a diferença entre o movimento instantâneo e a tendência. Uma panorâmica suave e rápida tem jitter ≈ 0 |
| Classificador de movimento (fluxo denso, média dx/dy) | Ajuste de similaridade RANSAC + paralaxe topo/base → panorâmica × órbita, aproximação, afastamento, revelação, sobrevoo |
| Janelas de qualidade de 1 s | Linha do tempo de 4 amostras/s + janelas deslizantes julgadas pelos piores momentos (p20/p80) |
| Reenquadramento inteligente com YOLO | Saliência por resíduo espectral (sem modelo) para posicionar o recorte 9:16 |
| Perfis DJI | `config.DJI_PROFILES`, leitura do `.SRT` de telemetria (GPS e altitude) e aviso de 10 bits/D-Log |
| Divisão de tomadas longas + CLIP + índice vetorial | Candidatos por janela, CLIP zero-shot pelo carregador do próprio VideoHighlighter e cache em SQLite (sem ChromaDB) |
| Sequenciador por batida | Slots em número inteiro de batidas; quando há transição, o corte é estendido para que a transição comece na batida |

## Arquitetura

O pacote `drone/` fica na raiz do repositório e **não altera nenhum arquivo do
VideoHighlighter**. Por isso as atualizações do projeto original entram com
`git pull upstream main` sem conflito.

```text
ingest ──► analyzer (ffmpeg → pares de frames) ──► analysis_cache (SQLite)
              │  frame_metrics: nitidez, exposição, cor, saliência, assinaturas
              │  motion_analysis: vx, vy, zoom, rolagem, paralaxe → jitter/jerk
landscape (CLIP uma vez para a pasta inteira, depois libera a VRAM | heurística de cor)
visual_scoring ──► candidatos (janelas) com 4 critérios + tipo de movimento + categoria
music_selector (beats: modules.audio.music_analysis)
cinematic_director: slots na batida → shot_selection (MMR + duplicate_detection)
                    → abertura/fechamento/alternância → Edl (modules.media.edl)
edl_adapter: 16:9 → render_edl | 9:16 → recorte pela saliência + transitions.build_reel
```

O que vem do VideoHighlighter: `Edl`/`Cut`/`render_edl`/`save_edl`, `build_reel`
(crossfade, título, música), `analyze_music`, `adaptive_threshold`, `encoder_chain`
(NVENC → libx264), `ffmpeg_exe` e `ClipFramePrefilter`. O `.edl.yaml` salvo junto de
cada vídeo pode ser aberto no editor do VideoHighlighter para ajustes manuais.

**Dependências novas: nenhuma.** O pacote usa apenas o que o `requirements.txt` do
VideoHighlighter já instala.

## Pontuação (`drone/config.py`)

Os pesos são os da especificação: 30 / 25 / 20 / 15 / 10. Cada estilo pode
sobrescrevê-los, e você pode calibrá-los em `%LOCALAPPDATA%\DroneHighlighter\config.json`.

Os limiares atuais foram ajustados com filmagem sintética, que testa se o algoritmo
reage ao tipo certo de mudança. **Eles ainda precisam ser calibrados com clipes DJI reais.**
O relatório JSON salvo junto de cada vídeo traz as notas de cada tomada para ajudar
nisso.

## Desempenho estimado (i5-10600K + RTX 2060)

Medido neste ambiente de teste (2 vCPUs, sem GPU): a análise de 4K H.264 rodou a
4,9× o tempo real e a de 4K HEVC 10 bits a 1,7×. O gargalo é a decodificação. No
seu PC, as estimativas são:

- **Análise (só na primeira vez):** cerca de 5 a 6× o tempo real pela CPU, ou de 8 a
  12× com NVDEC. 45 min de material dão algo entre 4 e 9 min. Na segunda montagem
  da mesma viagem, a análise leva segundos.
- **CLIP ViT-B/32:** cerca de 600 MB de VRAM em fp16, carregado uma única vez por
  execução e depois liberado.
- **Render de 90 s em 4K:** de 6 a 10 min. O corte é feito por NVENC, mas a etapa de
  normalização do VideoHighlighter usa libx264 na CPU. Um vertical de 45 s em
  1080×1920 leva cerca de 1 min.

## Testes

```text
python -m pytest tests/test_drone_pipeline.py -q
```

São 70 testes (`test_drone_*.py`): tipo de movimento, suave × tremido, desfoque, duplicatas, cache e
renomeação, parser de instruções, cortes na batida, recorte 9:16, telemetria SRT,
integração do CLIP, geração completa 16:9 + 9:16 com música, ajustes sem IA, variações e o assistente de IA testado contra um servidor falso que imita Ollama/LM Studio, OpenAI e Claude, incluindo respostas inválidas e servidor fora do ar. A suíte original
continua passando (3.792 testes).

## Próximas etapas

1. Calibrar os limiares com 5 a 10 clipes DJI reais seus: alguns bons, alguns
   tremidos e alguns com D-Log.
2. Medir o CLIP na sua placa NVIDIA (no CI ele roda de verdade, mas no processador).
3. Normalizar com NVENC e CRF menor em 4K (proposta de PR para o upstream).
4. Recorte 9:16 que acompanha o assunto durante panorâmicas e órbitas, em vez de ficar fixo.
5. Correção automática de D-Log/HLG (LUT) e nivelamento do horizonte.
6. Usar GPS/SRT para espalhar a montagem por locais diferentes (`shot_place` do upstream).
7. Biblioteca pesquisável de filmagens, aproveitando o cache existente.
