# Projeto DroneHighlighter — Editor Automático de Vídeos de Drone 100% Local

## 1. Objetivo do projeto

Desenvolver um software gratuito, open source e totalmente local, especializado na **edição automática de vídeos capturados por drones**, utilizando projetos existentes no GitHub como base.

O objetivo é eliminar praticamente todo o trabalho manual de edição.

O usuário possui uma grande quantidade de vídeos brutos gravados com drone, principalmente de viagens, cachoeiras, montanhas, paisagens naturais e cidades históricas.

Atualmente, esses vídeos ficam armazenados sem utilização porque a edição manual exige muito tempo.

O software deverá analisar automaticamente uma pasta contendo vários vídeos, identificar os melhores momentos, selecionar as tomadas mais interessantes e produzir vídeos cinematográficos prontos para publicação.

**Requisito principal: selecionar a pasta, configurar o estilo de edição e clicar em gerar. O restante deve ser automático.**

## 2. Projetos open source de referência

### Projeto principal — VideoHighlighter

https://github.com/Aseiel/VideoHighlighter

Este será o projeto-base do nosso fork.

Queremos aproveitar, na medida em que estejam implementados e funcionais:

- Interface gráfica.
- Importação de vídeos.
- Processamento e análise visual local.
- Identificação de cenas e objetos.
- Seleção automática de destaques.
- Integração com modelos locais como CLIP.
- Sistema de montagem automática.
- Análise e sincronização de músicas.
- Sistema de listas de edição (EDL).
- Transições.
- Renderização e exportação de vídeos.

A intenção é modificar e ampliar o VideoHighlighter, não construir um editor completamente novo.

O projeto é distribuído sob AGPL-3.0, cujas obrigações devem ser respeitadas.

### Projeto complementar 1 — SkyGrid Shot Search

https://github.com/Manvi0b/skygrid-shot-search

Estudar suas técnicas de:

- Análise de filmagens aéreas.
- Busca visual por descrições.
- Indexação de cenas.
- Utilização de CLIP.
- Detecção de objetos.
- Divisão de vídeos longos em pequenos segmentos.
- Localização dos trechos visualmente mais interessantes.
- Identificação de similaridade entre tomadas.

É particularmente interessante para resolver o problema de filmagens longas e contínuas, nas quais existem vários momentos interessantes sem mudanças claras de cena.

Verificar a licença efetiva de cada componente antes de reutilizar código.

### Projeto complementar 2 — SkyGrid Auto Editor

https://github.com/Manvi0b/skygrid-auto-editor

Estudar suas abordagens para:

- Edição automática de vídeos de drone.
- Avaliação de estabilidade por fluxo óptico.
- Análise de movimento da câmera.
- Perfis específicos para filmagens DJI.
- Seleção dos melhores takes.
- Montagem cinematográfica.
- Transições e sincronização musical.
- Exportação para diferentes formatos.

Importante: não presumir que o código pode ser copiado livremente. Verificar a licença e, se necessário, implementar independentemente as técnicas.

**Estratégia geral:** usar o VideoHighlighter como motor principal e incorporar apenas os algoritmos e recursos realmente úteis dos projetos SkyGrid, evitando duplicações desnecessárias.

## 3. Requisitos obrigatórios

O programa deverá:

1. Funcionar 100% localmente no Windows.
2. Não utilizar Gemini, OpenAI, Claude ou qualquer API externa para executar a edição.
3. Não depender de assinatura ou pagamento.
4. Funcionar offline após a instalação das dependências e dos modelos necessários.
5. Ser especializado exclusivamente em filmagens de drones.
6. Aceitar vídeos MP4 e MOV, incluindo gravações DJI em 4K.
7. Analisar grandes quantidades de vídeos automaticamente.
8. Identificar os melhores trechos com base em critérios técnicos e visuais.
9. Evitar cenas repetitivas ou muito semelhantes.
10. Criar montagens cinematográficas sem necessidade de edição manual.
11. Utilizar músicas disponíveis localmente, com sincronização automática dos cortes.
12. Permitir exportação horizontal 16:9 e vertical 9:16.
13. Manter uma interface gráfica simples.
14. Aproveitar os recursos de hardware existentes.
15. Preservar a possibilidade de receber atualizações do projeto original.

Não utilizar uma LLM local grande se isso não for necessário. Preferir algoritmos clássicos de visão computacional e modelos locais leves.

## 4. Hardware disponível

Computador principal:

- Sistema operacional: Windows 11.
- Processador: Intel Core i5-10600K.
- Placa de vídeo: NVIDIA RTX 2060, 6 GB VRAM.

O sistema deve funcionar com esse hardware, sem exigir uma GPU mais potente.

Priorizar aceleração por GPU quando possível, mas manter alternativas de processamento por CPU.

Para renderização, considerar FFmpeg com NVENC, verificando os formatos e codecs efetivamente suportados.

Evitar manter vários modelos pesados carregados simultaneamente na VRAM.

## 5. Fluxo de funcionamento esperado

**ETAPA 1 — Importação**

O usuário seleciona uma pasta:

`C:\VideosDrone\Pirenopolis\Brutos`

Essa pasta poderá conter dezenas de arquivos de vídeo.

O software identifica os vídeos, coleta seus metadados e prepara o processamento.

**ETAPA 2 — Análise local**

O programa examina as filmagens e identifica:

- Paisagens naturais.
- Cachoeiras.
- Montanhas e serras.
- Rios e lagos.
- Cidades e construções.
- Movimentos suaves e cinematográficos.
- Aproximações e afastamentos.
- Panorâmicas.
- Órbitas.
- Movimentos verticais e revelações de paisagem.
- Trechos tremidos.
- Trechos com baixa nitidez.
- Filmagens repetitivas.
- Cenas visualmente semelhantes.

Usar CLIP e/ou outros modelos locais leves quando eles realmente melhorarem a análise visual.

Utilizar OpenCV, técnicas de análise de movimento e outros algoritmos adequados para a avaliação técnica.

**ETAPA 3 — Classificação**

Cada trecho recebe uma pontuação de qualidade.

Proposta inicial de pesos:

| Critério | Peso |
|---|---:|
| Qualidade visual e composição | 30% |
| Suavidade e estabilidade | 25% |
| Interesse da paisagem | 20% |
| Movimento cinematográfico | 15% |
| Variedade e originalidade | 10% |

Esses pesos são parâmetros iniciais de desenvolvimento, não valores validados. Devem ser ajustáveis e calibrados com vídeos reais.

Importante: movimentos intencionais e suaves do drone não devem ser confundidos com vibração ou instabilidade.

O programa deverá selecionar cenas que combinem qualidade individual e diversidade no conjunto final.

**ETAPA 4 — Montagem automática**

O sistema organiza as tomadas e gera uma sequência cinematográfica.

Deverá considerar:

- Abertura visualmente impactante.
- Alternância equilibrada entre tipos de paisagens.
- Duração adequada de cada take.
- Ritmo da música.
- Cortes naturais.
- Transições discretas.
- Evitar repetições.
- Encerramento visualmente agradável.

Sempre que possível, utilizar o motor de montagem e renderização do VideoHighlighter.

**ETAPA 5 — Música e títulos**

O usuário poderá manter uma biblioteca local de músicas.

O programa seleciona uma faixa compatível com o estilo escolhido e sincroniza os cortes com seu ritmo.

Preferências iniciais:

- Instrumental cinematográfica.
- Alternativa / indie.
- Eletrônica ambiente.
- Música tranquila para paisagens naturais.

Permitir inserir automaticamente o nome do destino, por exemplo:

"Pirenópolis — Goiás"

As músicas devem ser arquivos fornecidos pelo usuário, com direitos adequados para a utilização pretendida.

**ETAPA 6 — Exportação**

Produzir inicialmente:

- Reels vertical — 9:16, aproximadamente 45 a 60 segundos.
- Vídeo horizontal — 16:9, até 4K, aproximadamente 90 a 120 segundos.

As durações serão configuráveis.

O reenquadramento vertical deve tentar preservar o assunto principal e evitar cortes inadequados.

## 6. Interface desejada

A interface deverá ser simples, priorizando a automação.

Elementos principais:

**Selecionar pasta:** diretório contendo os vídeos brutos.

**Estilo de edição:**
- Cinematográfico.
- Natureza contemplativa.
- Viagem dinâmica.
- Vídeo curto para redes sociais.

**Duração:** 30, 45, 60, 90 ou 120 segundos, além de valor personalizado.

**Música:** selecionar biblioteca ou estilo.

**Formato:** horizontal, vertical ou ambos.

**Título:** nome do destino.

**Instruções opcionais:** campo de texto com comandos como:

"Crie um vídeo cinematográfico de Pirenópolis com 90 segundos, priorizando cachoeiras, serras e movimentos suaves, com música instrumental tranquila."

Como não utilizaremos APIs nem uma LLM obrigatória, inicialmente esse texto poderá ser interpretado por um sistema local baseado em palavras-chave, parâmetros e perfis predefinidos.

Não é necessário compreender linguagem natural ilimitada na primeira versão.

**Botão principal: GERAR VÍDEO**

Exibir progresso e permitir visualizar o resultado final.

A edição manual poderá continuar disponível, mas não será necessária para o fluxo principal.

## 7. Arquitetura técnica sugerida

Manter os módulos existentes do VideoHighlighter sempre que possível.

Criar componentes separados para as funcionalidades específicas de drones, por exemplo:

```text
VideoHighlighter/
├── modules/
│   ├── media/
│   ├── segments/
│   └── audio/
│
├── drone/
│   ├── ingest.py
│   ├── scene_detection.py
│   ├── motion_analysis.py
│   ├── visual_scoring.py
│   ├── duplicate_detection.py
│   ├── shot_selection.py
│   ├── cinematic_director.py
│   ├── music_selector.py
│   ├── prompt_parser.py
│   ├── edl_adapter.py
│   └── analysis_cache.py
│
└── main.py
```

Essa estrutura é apenas uma sugestão. Antes de implementá-la, examinar a arquitetura real do repositório para evitar redundância com componentes existentes.

### Tecnologias possíveis

- Python.
- OpenCV.
- FFmpeg.
- CLIP local.
- PyTorch, quando necessário.
- NumPy.
- Análise de fluxo óptico.
- Embeddings visuais.
- Cache persistente, preferencialmente SQLite ou arquivos estruturados.

Não adicionar YOLO, ChromaDB, Ollama ou outras dependências apenas por estarem presentes nos projetos de referência. Primeiro verificar se são realmente necessárias.

## 8. Cache e reaproveitamento dos vídeos

Essa é uma funcionalidade importante.

Como o usuário possui muito material bruto, seria interessante que cada vídeo fosse analisado apenas uma vez, sempre que possível.

O programa deve guardar:

- Identificação e metadados do arquivo.
- Duração e resolução.
- Segmentos detectados.
- Pontuação técnica e visual.
- Embeddings, quando utilizados.
- Identificação dos melhores trechos.
- Assinatura do arquivo ou mecanismo de invalidação de cache.

Assim, caso o usuário gere outra montagem da mesma viagem, o programa reaproveita os resultados existentes, evitando repetir análises pesadas.

No futuro, isso poderá formar uma biblioteca inteligente e pesquisável de filmagens de drone.

## 9. Prioridades de desenvolvimento

### Fase 1 — Avaliação e execução do projeto original

- Examinar o código real do VideoHighlighter.
- Identificar dependências, licenças e compatibilidade com Windows.
- Verificar os módulos de análise, seleção e edição.
- Instalar e executar o programa original.
- Validar o processamento de vídeos DJI.
- Identificar o que já funciona e o que precisa ser adaptado.

### Fase 2 — Análise especializada em drones

- Implementar ou adaptar a divisão de tomadas longas em segmentos.
- Melhorar a análise de estabilidade e movimentos de câmera.
- Implementar critérios de avaliação visual.
- Eliminar cenas semelhantes e repetitivas.
- Criar um sistema de pontuação configurável.

### Fase 3 — Montagem cinematográfica

- Aprimorar a seleção e ordenação dos takes.
- Ajustar a duração dos trechos.
- Sincronizar cortes com música.
- Criar estilos de edição.
- Melhorar a organização narrativa.
- Gerar vídeos automaticamente.

### Fase 4 — Interface e automação

- Simplificar a interface.
- Adicionar os perfis de drone.
- Permitir instruções textuais simples.
- Adicionar títulos automáticos.
- Implementar exportação vertical e horizontal.
- Criar o cache persistente.

## 10. Regras importantes para o Claude Code

1. Examinar os três repositórios antes de propor grandes mudanças.
2. Não presumir que funcionalidades descritas nos READMEs estejam implementadas corretamente; verificar o código.
3. Não reescrever desnecessariamente recursos que já funcionam no VideoHighlighter.
4. Manter o projeto modular.
5. Não introduzir dependências de serviços externos.
6. Não implementar funcionalidades pagas.
7. Verificar a licença de cada repositório e de cada modelo utilizado.
8. Priorizar estabilidade, desempenho e facilidade de utilização.
9. Fazer alterações incrementais e testáveis.
10. Preservar a estrutura necessária para incorporar atualizações futuras do VideoHighlighter.
11. Não sacrificar a qualidade visual apenas para reduzir o tempo de processamento.
12. Evitar exclusão automática dos arquivos originais.
13. Testar os algoritmos com filmagens reais de drone, não apenas vídeos genéricos.

## 11. Resultado final esperado

Queremos chegar a uma experiência como esta:

O usuário possui 30 vídeos brutos de uma viagem a Pirenópolis.

Ele abre o DroneHighlighter, seleciona a pasta e escolhe:

- Estilo: cinematográfico.
- Duração: 90 segundos.
- Música: instrumental tranquila.
- Prioridade: cachoeiras, montanhas e movimentos suaves.
- Exportação: horizontal 4K e vertical para Instagram.

Clica em **Gerar vídeo**.

O programa analisa todos os arquivos, escolhe os melhores trechos, elimina cenas repetidas, organiza uma sequência, sincroniza com a música, insere o título e exporta os vídeos.

Tudo isso utilizando exclusivamente os recursos do computador, sem enviar arquivos à internet e sem cobrança de API.

## 12. Primeira tarefa solicitada ao Claude

**Antes de começar a programar, faça uma auditoria técnica dos três repositórios mencionados.**

Identifique:

- Arquitetura real do VideoHighlighter.
- Linguagens e bibliotecas utilizadas.
- Módulos que podem ser reaproveitados.
- Algoritmos presentes nos projetos SkyGrid que representam melhorias concretas.
- Duplicações que devem ser evitadas.
- Riscos de licença.
- Desempenho estimado no hardware disponível.
- Melhor estratégia para criar o fork mantendo compatibilidade com o projeto original.

Depois, apresente um plano de implementação dividido em pequenas etapas e **comece pela primeira versão funcional, capaz de analisar uma pasta de vídeos de drone e gerar uma montagem automática melhorada**.

Priorize a implementação de código executável e testes reais em vez de apenas documentação conceitual.

O princípio central do projeto é:

**Transformar um grande acervo de vídeos brutos de drone em vídeos cinematográficos prontos, com o mínimo de intervenção do usuário, utilizando somente ferramentas locais, gratuitas e open source.**