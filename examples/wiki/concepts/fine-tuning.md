---
title: Fine-Tuning
created: 2026-04-17
updated: 2026-04-17
tags: [개념, LLM, 학습]
sources: [raw/articles/2026-04-17_lora-explained.md]
---

# Fine-Tuning

사전 학습된 모델을 특정 도메인/태스크에 맞게 추가 학습하는 과정.

## Kernel

| 유형 | 방법 | VRAM |
|------|------|------|
| Fully Fine-Tuning | 모든 가중치 업데이트 | 가중치 × 2~3배 |
| [[lora]] | 저랭크 행렬만 학습 | 극소 |
| QLoRA | 양자화 + LoRA | 더 극소 |

## 관련

[[lora]], [[transformer]], [[quantization]]
- [[lora-explained]]
