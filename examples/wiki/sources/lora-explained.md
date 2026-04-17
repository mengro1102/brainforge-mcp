---
title: "LoRA 논문 쉽게 설명하기"
created: 2026-04-17
updated: 2026-04-17
tags: [LoRA, Fine-Tuning, PEFT, LLM]
sources: [raw/articles/2026-04-17_lora-explained.md]
type: article
status: ingested
---

# LoRA 논문 쉽게 설명하기

## Kernel

LLM의 Fully Fine-Tuning은 가중치의 2~3배 VRAM이 필요하여 비현실적. [[lora|LoRA]]는 모델 가중치를 Freeze하고, 저랭크 행렬만 학습하여 간접적으로 가중치를 업데이트.

## 핵심 주장

1. Fully Fine-Tuning의 한계: 가중치 수 × 2~3배 VRAM 필요
2. LoRA 핵심: 모델 가중치 Freeze + 저랭크 행렬만 학습
3. 적용 위치: [[transformer]]의 Q, K, V, O 레이어
4. 장점: VRAM 절감, 성능 동등, 추론 속도 동일, 원복 용이

> [!causal] 인과 관계
> [[lora]] →(가능하게 함)→ [[fine-tuning]]의 효율적 수행
> 신뢰도: 높음 | 출처: [[lora-explained]]

## 관련 개념

[[lora]], [[fine-tuning]], [[transformer]], [[quantization]]
