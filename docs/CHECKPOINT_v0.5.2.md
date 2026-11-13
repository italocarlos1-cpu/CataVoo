# CHECKPOINT — DroneHighlighter v0.5.2 (13/11/2026)

> **Como usar este relatório:** abra uma nova conversa, anexe o arquivo
> `DroneHighlighter_checkpoint_v0.5.2.zip` e cole este relatório (ou anexe-o).
> Ele contém o que é preciso para continuar o projeto do ponto em que parou:
> objetivo, decisões, arquitetura, estado atual, testes, limitações e próximos passos.

---

## 0. Prompt sugerido para retomar

```text
Estou continuando o projeto DroneHighlighter (editor automático de vídeos de drone,
100% local, fork do VideoHighlighter). Anexei o checkpoint v0.5.2 (zip) e este relatório.
1) Clone https://github.com/Aseiel/VideoHighlighter no commit c0e4a3d6 (release 0.15.0)
   e extraia o zip na raiz (só acrescenta arquivos).
2) Instale numpy, opencv-python, PySide6, pyyaml e pytest, e rode:
   python -m pytest tests/test_drone_*.py -q  (devem passar 70 testes).
3) Leia as seções 5 (decisões), 6 (arquitetura) e 12 (próximos passos) deste relatório.
Próxima tarefa: <descreva aqui, ex.: "adicionar desfazer/refazer na timeline" ou "exportar para DaVinci (etapa A)">.
Responda em português.
```

---

## 1. Objetivo do projeto

Software gratuito, open source e **100% local no Windows** que transforma uma pasta de
vídeos brutos de drone (viagens, cachoeiras, serras, cidades históricas; DJI 4K, MP4/MOV)
em vídeos cinematográficos prontos. O fluxo principal é **selecionar a pasta → configurar o
estilo → GERAR**. O programa analisa, pontua, elimina repetições, monta no ritmo da música,
coloca o título e exporta em 16:9 (até 4K) e 9:16.

Requisitos-chave da especificação original (arquivo `docs/ESPECIFICACAO_ORIGINAL.md` no zip):
- sem Gemini/OpenAI/Claude no fluxo principal, sem assinatura, offline após a instalação;
- preferir visão computacional clássica e modelos locais leves (CLIP), sem LLM grande obrigatória;
- não confundir movimento intencional suave com vibração;
- cache para analisar cada vídeo uma única vez;
- usar o VideoHighlighter como base, preservar a possibilidade de receber atualizações dele e respeitar a AGPL-3.0;
- nunca apagar ou alterar os originais.

**Hardware do usuário:** Windows 11, Intel i5-10600K. A especificação cita uma RTX 2060
de 6 GB; depois o usuário mencionou uma **RTX 4060 de 8 GB**. Considerar ambas.

---

## 2. Base e licenças

| Item | Situação |
|---|---|
| Projeto-base | **Aseiel/VideoHighlighter**, commit `c0e4a3d62f1eb177377cc5ed85d109cf08ea8c67` (release 0.15.0, 12/11/2026), AGPL-3.0, com CLA e licença comercial dupla do autor. Python/PySide6, ~206 mil linhas. |
| Nosso código | Pacote `drone/` + testes + docs, **AGPL-3.0-or-later** (cabeçalhos SPDX em todos os arquivos). |
| skygrid-auto-editor (Manvi0b) | **Sem arquivo de licença**, ou seja, todos os direitos reservados. Só ideias foram usadas, nenhuma linha copiada. |
| skygrid-shot-search (Manvi0b) | MIT. Só ideias. Dependências evitadas: `ultralytics`/YOLO (AGPL, proibido pelo teste `test_no_agpl_detector.py` do próprio VH) e ChromaDB. |
| Decisão do usuário | "Usar ideias e conceitos dos SkyGrid, criar algo próprio que substitua o que as licenças não permitem; todo o projeto novo em AGPL-3.0." |

Observação: para contribuir de volta ao upstream é preciso assinar o CLA, que permite ao
autor usar o código também na versão comercial. Pela AGPL, distribuir o programa ou
oferecê-lo pela rede obriga a disponibilizar o código-fonte.

---

## 3. Como restaurar o projeto

```bat
git clone https://github.com/Aseiel/VideoHighlighter.git
cd VideoHighlighter
git checkout c0e4a3d62f1eb177377cc5ed85d109cf08ea8c67
git checkout -b drone
:: extraia o zip aqui (pastas drone\, tests\, docs\ se mesclam; nenhum arquivo do VH é substituído)
python -m venv .venv && .venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
python -m pytest tests/test_drone_*.py -q
python -m drone          :: janela
python -m drone.cli ...  :: linha de comando
```

Para atualizar a partir do upstream:
`git remote add upstream https://github.com/Aseiel/VideoHighlighter.git && git fetch upstream && git merge upstream/main`
e rodar os testes `test_drone_*`.

---

## 4. Histórico de versões (o que cada etapa entregou)

| Versão | Entregas |
|---|---|
| 0.1 | Auditoria dos 3 repositórios; pacote `drone/` com ingestão (DJI, `.SRT`), análise ffmpeg em pares de quadros, métricas visuais, movimento por RANSAC + jitter, CLIP/heurística, candidatos por janela, MMR com anti-duplicata, diretor na batida, render 16:9 (render_edl) e 9:16 com recorte pela saliência, cache SQLite, CLI, GUI PySide6, 19 testes. |
| 0.2 | "Refazer com ajustes" sem IA (`feedback.py`), "Outra versão" (variação), **assistente de IA opcional, desligado por padrão** (Ollama, LM Studio, compatível, OpenAI, Anthropic), só texto enviado, validação e fallback. |
| 0.3 | 10 estilos + **Automático** (`style_advisor.py`, sem IA), curva de ritmo (trailer), preferências por estilo, ordem cronológica, viés de movimento, **ritmo adaptativo**. |
| 0.5.2 | **Ícone oficial** escolhido pelo usuário (drone preto e âmbar com câmera): `packaging/drone/DroneHighlighter.ico` (16 a 256 px) e `.png` (512 px), usado no .exe, na janela e na capa do manual. Versão `0.5.2`. |
| 0.5.1 | **Kit do .exe para Windows**: `packaging/drone/` (receita PyInstaller com duas saídas, a janela e a linha de comando; ffmpeg embutido via imageio-ffmpeg; edição "lite" sem PyTorch e edição opcional com CLIP), `build_exe.bat` (duplo clique: venv, dependências, build, autoteste, zip), workflow `.github/workflows/build-drone-exe.yaml` (testes no Windows + artefato .zip), ícone próprio, autoteste `DRONEHIGHLIGHTER_SELFTEST`, chamadas ao ffmpeg sem janela de console. Versão `0.5.1`. |
| 0.5 | **Edição manual (timeline)**: botão "✂ Edição manual" na tela principal; janela com faixas por vídeo, tomadas coloridas, candidatos cinza, arrastar bordas e meio, teclado quadro a quadro, imagens exatas de entrada e saída, prévia, sequência reordenável, **troca de estilo/formato sem render** (✎ = editado), ímã na batida, salvar/restaurar/renderizar. Pipeline separado em análise → plano → render (`Session`). Versão do pacote: `0.5.0`. |
| 0.4 | **Duração automática** (`material.py`), **aviso de falta de material** (perguntar/encurtar/esticar), **um vídeo por voo/dia/local/subpasta + resumo** (`grouping.py`), música com fade de ~4 s e loop se for curta, **manual de 30 páginas** (HTML/PDF). Versão do pacote: `0.4.0`. |

---

## 5. Decisões tomadas (não rediscutir sem motivo)

1. **Não editar arquivos do upstream.** Tudo vive em `drone/`, `tests/test_drone_*`, `tests/drone_fixtures.py` e `docs/`. Entrada separada: `python -m drone`. O VH completo continua em `python main.py`.
2. **Reaproveitar o motor do VH** em vez de reescrever: `Edl`/`Cut`/`render_edl`/`save_edl`/`load_edl`, `build_reel`, `analyze_music`, `adaptive_threshold`, `encoder_chain`, `ffmpeg_exe`, `ClipFramePrefilter`.
3. **Tremor = incoerência temporal, não magnitude.** O jitter é ‖movimento instantâneo − tendência (média móvel de ~1,75 s)‖, medido em pares de quadros *adjacentes* (amostragem `select='lt(mod(n,step),2)'`).
4. **Paisagem:** CLIP zero-shot pelo carregador do VH, carregado uma vez e liberado (nunca junto com o LLM); fallback por heurística de cores.
5. **Fluxo principal sem IA.** A IA é opcional e desligada; recebe só texto (instruções + tabela de trechos), nunca imagens; toda resposta é validada; qualquer falha cai no diretor automático.
6. **Ritmo preso à batida:** cada tomada avança um número inteiro de batidas; com transição, o corte é estendido para a transição começar na batida.
7. **Pouco material:** o programa nunca usa loop, câmera lenta ou tela preta para completar o tempo; ele sai mais curto e avisa. As políticas são perguntar (padrão na GUI), encurtar e esticar (padrão na CLI).
8. **Duração automática:** soma das tomadas *fortes* (nota ≥ 80% da média das 5 melhores) no tamanho normal do estilo, arredondada de 5 em 5 s; horizontal entre 15 e 180 s, vertical entre 15 e 60 s.
9. **Grupos:** um voo novo começa com mais de 10 min entre gravações; local = raio de 1,5 km pelo GPS (sem GPS, separa por voo); cada grupo tem seleção, estilo (no Automático) e duração próprios.
10. **Música:** é escolhida por palavras-chave no nome do arquivo ou pasta, depois por BPM e duração; fade de ~4 s no fim; repete se for curta. Música não é prioridade para o usuário.
11. **Manual:** o usuário quer um manual que explique cada recurso, as diferenças, vantagens e desvantagens. Mantê-lo atualizado a cada recurso novo.
12. **Timeline:** é permitido mudar a duração total ao ajustar os cortes; o total é atualizado e mostrado.
13. **Edição manual em janela separada**, aberta pelo botão "✂ Edição manual" ao lado de REFAZER e Outra versão. Trocar o estilo mostra os cortes daquele estilo **sem renderizar**; as edições ficam guardadas por grupo, estilo e formato, e o ✎ marca os estilos editados.
14. **Teclado e ímã:** as setas movem 1 quadro (até o limite real de quadro) e ignoram o ímã; Shift+seta move 1 batida (ímã ligado) ou 1 s; arrastar com o ímã pula de batida em batida; "Encaixar tudo na batida" e a pergunta antes de renderizar fazem a "interação com o ímã" depois do ajuste fino. "Na batida" tolera meio quadro.
16. **Distribuição em .exe:** o `.exe` é gerado **no Windows**, pelo `build_exe.bat` ou pelo GitHub Actions (o sandbox é Linux e o PyInstaller não faz build cruzado). A receita foi validada aqui gerando o binário Linux equivalente: a CLI gerou 16:9 e 9:16 com música num ambiente limpo, sem ffmpeg no PATH (usou o ffmpeg embutido), e a janela passou no autoteste. Formato pasta (onedir), não arquivo único (abre mais rápido). Tamanho ~350–700 MB na edição lite.
15. **Prévia sem som:** o QMediaPlayer é criado sob demanda e **sem QAudioOutput** (no sandbox sem áudio, o QAudioOutput derrubava o processo). `DRONEHIGHLIGHTER_NO_VIDEO_PREVIEW=1` desliga a prévia.

---

## 6. Arquitetura (`drone/`, ~6.200 linhas)

```text
Session (pipeline): _analyse → grouping → prepare_group(estilo) → plan_format(formato) → render_plan
  └─ Edição manual: Session.plan → EditPlan (edit_session) ⇄ EditorWindow (editor_window) → to_plan → Session.render

ingest → analyzer(media_io + frame_metrics + motion_analysis) → analysis_cache
      → landscape (CLIP | heurística) → grouping
      → por grupo: style_advisor (se Automático) → music_selector
                   → visual_scoring.candidates → material.estimate (+ falta de material)
                   → [ai_assist.direct opcional] → cinematic_director.direct (shot_selection, duplicate_detection)
                   → edl_adapter (16:9 render_edl | 9:16 recorte + build_reel)
```

| Arquivo | Linhas | Função |
|---|---:|---|
| `__init__.py` | 26 | `__version__ = "0.5.2"`, `ANALYZER_VERSION = 1` (faz parte da chave do cache) |
| `__main__.py` | 10 | `python -m drone`: GUI sem argumentos, CLI com argumentos |
| `media_io.py` | 285 | `probe` (ffprobe/cv2), `iter_frame_pairs` (ffmpeg, hwaccel auto com fallback), `grab_frame` |
| `ingest.py` | 139 | `scan_folder`, detecção DJI, `parse_dji_srt` (lat/lon/alt), aviso de 10 bits; ignora `.LRF` |
| `frame_metrics.py` | 108 | nitidez (var. do Laplaciano), exposição, contraste, colorfulness, verde/água/quente/céu, bordas, saliência espectral (sal_x, foco), assinaturas (16×9 + HS 8×4) |
| `motion_analysis.py` | 209 | `pair_motion` (LK + estimateAffinePartial2D RANSAC → vx, vy, zoom, rot, paralaxe), `smoothness_series` (jitter/jerk/tendências), `classify_movement`, `MOVEMENT_VALUE`, scores |
| `analyzer.py` | 130 | uma passada por vídeo → timeline por amostra + miniaturas JPEG para o CLIP; `scene_cuts` |
| `analysis_cache.py` | 183 | SQLite: tabelas `clips`, `thumbs`, `embeddings`, `music`; chave = hash do conteúdo (1º e último MiB) + parâmetros + versão; sobrevive a renomear/mover |
| `landscape.py` | 220 | 12 categorias + 4 "chatas" (prompts CLIP), `ClipScorer`, heurística, `annotate`, `KEYWORDS` pt-BR |
| `visual_scoring.py` | 194 | `Candidate`, `score_window` (5 critérios), `candidates_for_clip` (janelas deslizantes + NMS) |
| `duplicate_detection.py` | 33 | similaridade = min(layout, paleta) × categoria + proximidade no mesmo take |
| `shot_selection.py` | 59 | MMR guloso com limites (duplicata, por clipe, cota por categoria), `already`, `relax` |
| `cinematic_director.py` | 458 | `plan_slots` (batidas, curva de ritmo), estilo ajustado + defaults do estilo, bônus (prefer/avoid/anterior/variação/viés), `_order` (abertura/fechamento/cronologia), `adapt_slots` (ritmo adaptativo), `_add_shots`, `_fill_deficit`, `to_edl` |
| `edl_adapter.py` | 98 | render 16:9 via `render_edl`; 9:16 via `cut_cropped` (recorte pela saliência) + `build_reel`; salva `.edl.yaml` |
| `music_selector.py` | 141 | biblioteca, `analyse` (cache), `choose`, `prepare` (corte, fade de 4 s, `-stream_loop`) |
| `material.py` | 89 | `MaterialEstimate` (distinct, strong, comfortable, maximum, suggested, suggested_vertical), `shortfall` |
| `grouping.py` | 170 | `make_groups`: nada/voo/dia/local/subpasta + resumo |
| `style_advisor.py` | 158 | `profile` (perfil da viagem), `score_styles`, `choose` (log com motivo e alternativas) |
| `feedback.py` | 165 | `Adjustments` (estrutura comum à IA e às palavras-chave), `parse_feedback` |
| `prompt_parser.py` | 125 | instruções → estilo, duração, título, prioridades, música, formatos |
| `ai_assist.py` | 317 | `PROVIDERS`, `AIClient` (urllib, sem dependências; OpenAI-compat e Anthropic), `interpret`, `direct`, `extract_json`, validações |
| `pipeline.py` | 634 | `Job`, `run(..., decide_fn)`, `_setup`, `_analyse`, `GroupPrep`, `prepare_group`, `plan_format`, `render_plan`, `shots_report`, `_make_set`, **`Session`** (plan/render sem rodar tudo), `resolve_adjustments`, `ai_direct` |
| `config.py` | 252 | `AnalysisParams`, `MotionThresholds`, `ScoreWeights`, `Style` + `STYLES` (11), `AISettings`, `DroneConfig` (load/save JSON) |
| `cli.py` | 133 | argparse em pt-BR (ver seção 9) |
| `gui.py` | 555 | janela PySide6 com Worker em QThread, diálogo de falta de material (`threading.Event`), painel de IA, refazer/outra versão, **botão ✂ Edição manual** (`SessionWorker` → `EditorWindow`) |
| `edit_session.py` | 363 | `EditShot`, `ClipInfo`, **`EditPlan`** (from_plan, trim com/sem ímã, nudge por quadro/s/batida, slide, insert/replace/remove/reorder, set_transition, snap_all, on_beat, cut_times, to_plan, to_dict/from_dict), **`EditStore`** (`edicao_manual.json`), `fmt_time` |
| `editor_window.py` | 938 | **janela da timeline**: `ShotItem` (arrastar bordas e meio), `CandidateItem` (duplo clique e menu), `TimelineView` (teclado), prévia de quadros (QThreadPool + ffmpeg), prévia em vídeo, sequência (QListWidget com arrastar), barra de estilo/formato/ímã/zoom, render em QThread |

**Pontos de acoplamento com o VH** (o que pode quebrar num merge do upstream):
`modules.media.edl` (Cut, Edl, render_edl, save_edl, load_edl),
`modules.media.transitions` (build_reel, Transition, ReelCancelled),
`modules.audio.music_analysis.analyze_music`, `modules.segments.scene_cuts.adaptive_threshold`,
`modules.system.encoder_select.encoder_chain`, `modules.system.app_paths.ffmpeg_exe`,
`modules.media.ffmpeg_tools` (probe, ffprobe_exe), `llm.clip_prefilter.ClipFramePrefilter` (`available`, `load`, `infer`).

---

## 7. Parâmetros padrão (`%LOCALAPPDATA%\DroneHighlighter\config.json`)

- **analysis:** `sample_hz 4.0`, `width 480`, `thumb_every 1.0` (mudar invalida o cache).
- **motion** (em larguras de quadro por segundo): `hover_speed 0.012`, `comfort_speed 0.22`, `fast_speed 0.60`, `zoom_rate 0.025`, `parallax_orbit 0.010`, `jitter_ok 0.020`, `jitter_bad 0.12`, `jitter_reject 0.25`, `jerk_ok 0.05`, `jerk_bad 0.40`.
- **weights:** qualidade 0.30, suavidade 0.25, interesse 0.20, movimento 0.15, variedade 0.10 (os estilos sobrescrevem).
- **Outros:** `duplicate_threshold 0.90`, `max_shots_per_clip 3`, `render_mode gpu`, `hwaccel auto`, `use_clip true`.
- **ai:** `enabled false`, `provider ollama`, `timeout 180`, `max_candidates 40`. Modelos padrão: Ollama `qwen3:8b`, OpenAI `gpt-5-mini` (nome tirado de fontes de terceiros, conferir), Anthropic `claude-haiku-5-5`. Chaves pelo campo ou pelas variáveis `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`.
- **Fórmula da qualidade:** 0.40·nitidez (abs × (0.6 + 0.4·rel)) + 0.20 exposição + 0.15 contraste + 0.10 cor + 0.15 foco do assunto.

**Estilos** (`chave`: tomada mín–máx s · transição · BPM · extras):
`automatico` (escolhe entre os demais, adaptativo) · `cinematografico` 3.5–6.5, 50% crossfade 0.6 · `contemplativo` 5–9, crossfade 1.0, prefere natureza, fecha com pull_out · `por_do_sol` 5.5–9, crossfade 1.2, fecha com sunset, curva (1, 1, 1.2) · `minimalista` 7–12, crossfade 1.5, viés hover/pan · `cidade_historica` 3.5–6, 40% crossfade, abre com rise_reveal, viés orbit · `diario_viagem` 3–5.5, cronológico 1.0 · `trailer_epico` 2–5, curva (1.7…0.55), abre com rise_reveal e fecha com pull_out · `viagem_dinamica` 1.8–3.6, cortes, lively · `aventura_fpv` 1.2–2.6, cortes, viés flyover/fast · `redes_sociais` 1.5–3, cortes.

---

## 8. Formatos de dados

- **Saída:** `<pasta>\_dh_editados\{título}[_{grupo}]_{16x9|9x16}_{AAAAMMDD_HHMMSS}.mp4`, mais `.edl.yaml`, `_musica.m4a` e `{título}_{stamp}_relatorio.json`. A edição manual gera `{título}[_{grupo}]_manual_{tag}_{stamp}.mp4` e `{título}_manual_{stamp}_relatorio.json` (`"manual_edit": true`).
- **Edições manuais:** `<saída>\edicao_manual.json` = `{"version": 1, "plans": {"grupo|estilo|formato": {style_key, style_label, tag, unit, magnet, target, edited, shots[] {uid, path, start, end, transition, transition_duration, crop_x, category, movement, score, parts}}}}`. Só os planos editados são gravados.
- **Relatório JSON** (campos principais): `job`, `prompt`, `feedback`, `adjustments`, `ai` {enabled, provider, model, events[]}, `landscape_backend`, `groups[]` {key, label, clips, style, weights, auto_style, music, material, formats{tag: {output, edl, duration, asked, automatic_length, shots[] {uid, source, in, out, role, category, movement, score, parts, transition, crop_x}}}}. O grupo principal (`tudo`) também aparece no nível de cima (`style`, `formats`…), por compatibilidade.
- **Timeline do cache** (por amostra): t, sharp, clip_lo, clip_hi, mean_l, contrast, colorful, green, water, warm, sky, white, edges, sal_x, sal_focus, vx, vy, zoom, rot, parallax, residual, inliers, valid, jitter, jerk, speed, tvx, tvy, tzoom, trot, struct[144], colour[32], cuts. Em memória: cat_probs e interest.

---

## 9. Linha de comando (resumo)

`--pasta` (obrigatório) · `--estilo` · `--duracao N|auto` · `--duracao-vertical N|auto` · `--se-faltar esticar|encurtar` · `--separar nada|voo|dia|local|subpasta` · `--resumo` · `--formato horizontal|vertical|ambos` · `--resolucao 4k|1080p|720p` · `--titulo` · `--musicas` · `--musica` · `--instrucoes` · `--saida` · `--recursivo` · `--sem-clip` · `--cpu` · `--so-analisar` · `--ajustes` · `--refazer <relatorio.json>` · `--variacao N` · `--ia <provedor>` · `--ia-modelo` · `--ia-url` · `--ia-chave` · `--ia-so-instrucoes` · `--testar-ia`.

---

## 10. Testes e validação

- **70 testes, todos passando:** `test_drone_pipeline.py` (11 funções, com parametrização), `test_drone_ai_feedback.py` (10), `test_drone_styles.py` (8), `test_drone_material_groups.py` (9), `test_drone_manual_edit.py` (11: planejar qualquer estilo sem render, passo por quadro real, ímã, limites, total, inserir/substituir/reordenar/transição, salvar/carregar, to_plan, render de plano editado e **a janela da timeline offscreen** com teclas, arrasto e troca de estilo). `tests/drone_fixtures.py` gera filmagem aérea sintética com verdade conhecida (pan, push_in, rise, órbita com paralaxe, pull_out, tremido, borrado, duplicata) e música de clique.
- **Truque importante:** o `tests/conftest.py` do VH substitui o `cv2` por um MagicMock. O `test_drone_pipeline.py` instala o cv2 real durante o módulo e devolve o mock no fim; os outros arquivos importam `from tests import test_drone_pipeline as base`, nunca pelo nome curto, para não duplicar o módulo.
- **Suíte completa do VH com o nosso código:** 4.173 passaram. `test_scan_controls.py`, `test_simple_start.py` e `test_subtitles_reuse.py` travam no sandbox sem tela; o mesmo acontece no código original, então não é regressão.
- **Nunca testado com material real:** vídeos DJI reais (só sintéticos), CLIP real (só a integração, com modelo falso; o sandbox não acessa o Hugging Face), LLM real (só um servidor falso imitando Ollama/LM Studio/OpenAI/Anthropic), uso visual da GUI no Windows (só renderização offscreen), **sensação de arrastar e a prévia em vídeo da timeline** (offscreen, sem som e sem reprodução real), NVENC/NVDEC.
- **Medição de desempenho no sandbox** (2 vCPUs, sem GPU): análise de 4K H.264 a 4,9× o tempo real e de 4K HEVC 10 bits a 1,7×. No PC do usuário, a estimativa é de 5 a 12×.

---

## 11. Limitações e dívidas técnicas conhecidas

1. Limiares e pesos (seção 7) e as regras do Automático foram calibrados só com vídeos sintéticos. É preciso calibrar com 5 a 10 clipes DJI reais (bons, tremidos, D-Log) usando os relatórios JSON.
2. Sem o CLIP, a paisagem é estimada só por cores (não distingue montanha de morro).
3. O recorte 9:16 é estático por tomada (não acompanha o assunto durante uma panorâmica).
4. Não há correção de D-Log/HLG (LUT) nem nivelamento do horizonte.
5. A normalização do `build_reel` do VH usa libx264 crf 23 na CPU, com perda de geração em 4K e mais lentidão. Possível PR no upstream para usar NVENC e CRF menor.
6. `_display_size` ignora a rotação (irrelevante para drones).
7. O nome de modelo padrão da OpenAI pode estar desatualizado.
8. A chave de API fica em texto puro no `config.json` local (a variável de ambiente é a alternativa).
9. A heurística do "Automático" e da paisagem sem CLIP pode errar em viagens mistas.

---

## 12. Próximos passos acordados

**GitHub:** conta `italocarlos1-cpu` conectada, mas o app do Claude só tem acesso a `minha-farmacinha`. Para gerar o .exe pelo GitHub Actions, o usuário precisa: (1) fazer o fork de Aseiel/VideoHighlighter, (2) liberar o fork no app do Claude (Settings → Applications → Installed GitHub Apps → Configure → Repository access) e (3) habilitar o Actions no fork. Depois disso: enviar os arquivos para o ramo `drone` (sem tocar no main), disparar o workflow (tag `drone-v*` ou Run workflow), baixar o artefato e entregar.

**Pendente da v0.5.1/0.5.2:** gerar o `.exe` no Windows do usuário e confirmar que ele abre e gera um vídeo
real. A edição com CLIP (`build_exe.bat clip`) nunca foi testada. O primeiro run do workflow no
Windows também é o primeiro teste da suíte no Windows: pode revelar diferenças de caminho, fontes
ou ffmpeg.

**Concluído na v0.5:** etapas B e C da revisão em timeline (pausa para revisar + janela
completa), com troca de estilo sem render, conforme pedido pelo usuário.

**Pendente / sugerido para a timeline:**

| Item | Descrição | Esforço |
|---|---|---|
| Teste no PC do usuário | Abrir a Edição manual com filmagens reais e anotar o que incomoda (fluidez do arrasto, zoom, prévia 4K HEVC) | — |
| Desfazer/refazer | Ctrl+Z / Ctrl+Y na timeline (hoje não existe; o "Restaurar automático" é o único retorno) | pequeno |
| **A** — exportar | Mandar o plano (automático ou editado) para DaVinci Resolve/Premiere (FCPXML/EDL CMX) e CapCut, reaproveitando `video_ai_editor/timeline_export.py` do VH (`TimelineExporter.to_edl`, `to_fcp_xml`, CapCut) | pequeno |
| **D** — recorte 9:16 | Ajustar na timeline a posição do recorte vertical de cada tomada (`EditShot.crop_x` já existe e é usado no render) | médio |
| Visual | Filmstrip contínuo (hoje as miniaturas são de 1 em 1 s, com espaços), marcas das batidas da música na régua, forma de onda | pequeno a médio |
| Renderizar vários | Botão para renderizar de uma vez todos os estilos/formatos marcados com ✎ | pequeno |

**Outros pendentes:** calibração com material real; validação do CLIP na RTX; recorte 9:16
dinâmico (que acompanha o assunto); LUT para D-Log; normalização com NVENC; biblioteca
pesquisável de filmagens (aproveitando o cache).

**Ao terminar qualquer recurso novo:** atualizar `docs/DRONE.md`, o manual
(`docs/manual_template.html` → `docs/DroneHighlighter_Manual.html` com as imagens de
`docs/manual_imagens/` embutidas em base64 nos marcadores `{{IMG_TELA}}`, `{{IMG_DIALOGO}}` e `{{IMG_EDITOR}}`, e o PDF
via `google-chrome --headless=new --no-sandbox --no-pdf-header-footer --print-to-pdf`),
`INSTALAR.md`, `__version__`, os testes e este checkpoint, e entregar um zip versionado.

---

## 13. Conteúdo do zip do checkpoint

```text
drone/                          pacote completo (27 arquivos .py)
tests/test_drone_*.py           5 arquivos de teste (70 testes)
tests/drone_fixtures.py         gerador de filmagem sintética
docs/DRONE.md                   referência técnica
docs/DroneHighlighter_Manual.html / .pdf   manual do usuário (34 páginas: seção 13 Edição manual, seção 21 com o .exe)
docs/manual_template.html       fonte do manual (com os marcadores das imagens)
packaging/drone/                kit do .exe: DroneHighlighter.spec, build_exe.bat, launch_gui.py, launch_cli.py, ícone (.ico/.png), README.md
.github/workflows/build-drone-exe.yaml   build do .exe + testes no Windows (GitHub Actions)
docs/manual_imagens/            telas usadas no manual (tela_principal, dialogo_pouco_material, edicao_manual)
docs/ESPECIFICACAO_ORIGINAL.md  especificação original enviada pelo usuário
docs/CHECKPOINT_v0.5.2.md       este relatório
INSTALAR.md                     instalação passo a passo
UPSTREAM_COMMIT.txt             commit exato do VideoHighlighter usado como base
```
