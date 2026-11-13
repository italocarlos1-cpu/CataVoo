# SPDX-License-Identifier: AGPL-3.0-or-later
"""Command line: python -m drone.cli --pasta C:\\VideosDrone\\Pirenopolis\\Brutos ...

Examples
--------
    python -m drone.cli --pasta "C:\\VideosDrone\\Pirenopolis\\Brutos" ^
        --estilo cinematografico --duracao 90 --titulo "Pirenópolis — Goiás" ^
        --musicas "C:\\Musicas" --formato ambos

    python -m drone.cli --pasta ... --instrucoes "vídeo contemplativo de 2 minutos, priorizando cachoeiras"

    python -m drone.cli --pasta ... --so-analisar     # ranks candidates, renders nothing
    python -m drone.cli --pasta ... --duracao auto --separar voo --resumo

    # redo the last version with changes (no AI needed)
    python -m drone.cli --pasta ... --refazer "..._relatorio.json" ^
        --ajustes "mais cachoeira, cortes mais rápidos, trocar as tomadas"

    # optional AI assistant (off unless --ia is given)
    python -m drone.cli --pasta ... --ia ollama --ia-modelo qwen3:8b --instrucoes "..."
    set ANTHROPIC_API_KEY=...   &   python -m drone.cli --pasta ... --ia anthropic
    python -m drone.cli --pasta . --ia lmstudio --testar-ia
"""
from __future__ import annotations

import argparse
import sys

from drone.ai_assist import PROVIDERS
from drone.config import STYLES, DroneConfig
from drone.pipeline import Job, run


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="drone", description="DroneHighlighter — edição automática de vídeos de drone")
    ap.add_argument("--pasta", required=True, help="pasta com os vídeos brutos")
    ap.add_argument("--estilo", default="cinematografico", choices=sorted(STYLES))
    ap.add_argument("--duracao", default="", help="duração do horizontal em segundos, ou 'auto'")
    ap.add_argument("--duracao-vertical", default="", help="duração do vertical em segundos, ou 'auto'")
    ap.add_argument("--se-faltar", default="esticar", choices=["esticar", "encurtar"],
                    help="se não houver material para a duração pedida")
    ap.add_argument("--separar", default="nada", choices=["nada", "voo", "dia", "local", "subpasta"],
                    help="um vídeo por voo / dia / local (GPS) / subpasta")
    ap.add_argument("--resumo", action="store_true",
                    help="com --separar: também um vídeo da viagem inteira")
    ap.add_argument("--formato", default="ambos", choices=["horizontal", "vertical", "ambos"])
    ap.add_argument("--titulo", default="")
    ap.add_argument("--musicas", default="", help="pasta da biblioteca de músicas")
    ap.add_argument("--musica", default="", help="um arquivo de música específico")
    ap.add_argument("--instrucoes", default="", help="texto livre com preferências")
    ap.add_argument("--saida", default="", help="pasta de saída (padrão: <pasta>/_dh_editados)")
    ap.add_argument("--resolucao", default="4k", choices=["4k", "1080p", "720p"])
    ap.add_argument("--recursivo", action="store_true")
    ap.add_argument("--sem-clip", action="store_true", help="não usar CLIP (heurística de cores)")
    ap.add_argument("--cpu", action="store_true", help="sem decodificação/codificação por GPU")
    ap.add_argument("--so-analisar", action="store_true")
    g = ap.add_argument_group("refazer / variações (sem IA)")
    g.add_argument("--ajustes", default="", help='ex.: "mais cachoeira, cortes mais rápidos"')
    g.add_argument("--refazer", default="", help="relatório JSON da versão anterior")
    g.add_argument("--variacao", type=int, default=0, help="1, 2, 3…: versões alternativas")
    g = ap.add_argument_group("assistente de IA (opcional, desligado por padrão)")
    g.add_argument("--ia", default="", choices=[""] + sorted(PROVIDERS),
                   help="liga o assistente com este provedor")
    g.add_argument("--ia-modelo", default="")
    g.add_argument("--ia-url", default="")
    g.add_argument("--ia-chave", default="", help="ou use OPENAI_API_KEY / ANTHROPIC_API_KEY")
    g.add_argument("--ia-so-instrucoes", action="store_true",
                   help="a IA só interpreta o texto; quem escolhe as tomadas é o sistema")
    g.add_argument("--testar-ia", action="store_true", help="só testa a conexão com a IA")
    return ap


def _safe_console() -> None:
    """Emoji in the log must never crash the program: redirected to a file or
    a pipe, Windows would encode with cp1252 (and the .exe ignores PYTHONUTF8)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv=None) -> int:
    _safe_console()
    a = build_parser().parse_args(argv)
    style = STYLES[a.estilo]
    sizes = {"4k": (3840, 2160), "1080p": (1920, 1080), "720p": (1280, 720)}
    def seconds(text, default):
        text = str(text).strip().lower()
        if text in ("auto", "automatica", "automática", "0"):
            return 0.0
        return float(text.replace(",", ".")) if text else default
    duration = seconds(a.duracao, style.default_duration)
    vdefault = min(60.0, max(30.0, duration / 2)) if duration else 0.0
    job = Job(folder=a.pasta, style=a.estilo,
              duration=duration,
              vertical_duration=seconds(a.duracao_vertical, vdefault),
              shortfall=a.se_faltar, group_by=a.separar, summary=a.resumo,
              horizontal=a.formato in ("horizontal", "ambos"),
              vertical=a.formato in ("vertical", "ambos"),
              title=a.titulo, music_dir=a.musicas, music_file=a.musica, prompt=a.instrucoes,
              output_dir=a.saida, recursive=a.recursivo, horizontal_size=sizes[a.resolucao],
              analyse_only=a.so_analisar, feedback=a.ajustes, variation=a.variacao,
              previous_report=a.refazer)
    cfg = DroneConfig.load()
    if a.ia:
        cfg.ai.enabled, cfg.ai.provider = True, a.ia
        cfg.ai.model, cfg.ai.base_url = a.ia_modelo, a.ia_url
        if a.ia_chave:
            cfg.ai.api_key = a.ia_chave
        cfg.ai.direct = not a.ia_so_instrucoes
    if a.testar_ia:
        from drone.ai_assist import AIClient, AIError
        if not cfg.ai.enabled:
            print("Assistente de IA desligado. Use --ia <provedor>.")
            return 1
        try:
            print(AIClient(cfg.ai).ping())
            return 0
        except AIError as exc:
            print("Falhou:", exc)
            return 2
    if a.sem_clip:
        cfg.use_clip = False
    if a.cpu:
        cfg.render_mode, cfg.hwaccel = "cpu", "none"

    def progress(pct, text):
        sys.stderr.write(f"\r[{pct:5.1f}%] {text[:70]:70s}")
        sys.stderr.flush()

    res = run(job, cfg, log_fn=lambda m: print("\n" + m if m else m), progress_fn=progress)
    print()
    if a.so_analisar:
        print(res.report)
    for o in res.outputs:
        print("Vídeo:", o)
    if res.report and not a.so_analisar:
        print("Relatório:", res.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
