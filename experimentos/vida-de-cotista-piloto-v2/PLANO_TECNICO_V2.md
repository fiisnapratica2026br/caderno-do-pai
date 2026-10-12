# PLANO TÉCNICO — PILOTO V2

## Objetivo
Fazer um vídeo melhor que o Piloto V1 sem gastar em lote antes de validar a técnica.

## O que já sabemos
- notebook local não tem GPU adequada para LTX/FLUX pesados
- SadTalker foi descartado como rota principal
- ElevenLabs já resolveu as vozes
- OpenArt conectado, mas saldo gratuito atual não cobre geração de vídeo
- Runway conectado, mas a workspace Free atual não libera modelos de vídeo via integração
- Sync Labs oferece `sync-3`, descrito como lipsync profissional com inteligência visual

## Estratégia V2
### Teste A — Sync-3
Entrada:
- referência oficial do Marcelo
- áudio oficial da fala curta
- modo de cabeça/rosto, quando aplicável

Fala:
“Pera aí... como assim o aluguel não entrou?”

Avaliar:
- preservação do rosto
- olhos
- movimento de cabeça
- tronco
- boca
- sincronismo
- naturalidade geral

Se aprovado, usar Sync apenas nas tomadas em que boca visível for realmente necessária.

### Teste B — movimento-base + Sync
Se o Teste A ainda parecer avatar:
1. gerar movimento-base sem fala em ferramenta image-to-video
2. aplicar lip sync sobre o vídeo, não sobre foto parada
3. usar cortes de reação/B-roll

## Gate de qualidade
Só continuar o episódio se:
- identidade >= 7/10
- movimento >= 7/10
- naturalidade >= 7/10
- usabilidade >= 7/10

## Custos
Antes de qualquer geração paga, estimar e pedir aprovação explícita.

## Montagem
MPT/editor entra somente depois dos clipes aprovados.
