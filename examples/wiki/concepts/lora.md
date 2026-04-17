---
title: LoRA (Low-Rank Adaptation)
created: 2026-04-17
updated: 2026-04-17
tags: [개념, Fine-Tuning, PEFT, LLM]
sources: [raw/articles/2026-04-17_lora-explained.md]
---

# LoRA (Low-Rank Adaptation)

## Kernel

가중치 행렬 W(d×k)를 직접 업데이트하지 않고, 저랭크 행렬 LoRA_A(d×r)·LoRA_B(r×k)만 학습하여 W + (LoRA_B × LoRA_A)로 간접 업데이트.

> [!causal] 인과 관계
> [[lora]] →(가능하게 함)→ [[fine-tuning]]의 효율적 수행
> 신뢰도: 높음 | 출처: [[lora-explained]]

> [!causal] 인과 관계
> [[quantization]] + [[lora]] →(가능하게 함)→ 로컬 GPU에서의 도메인 특화 LLM 구축
> 신뢰도: 중간 | 출처: [[lora-explained]]

## 관련

[[fine-tuning]], [[transformer]], [[quantization]]
- [[lora-explained]]
