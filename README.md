# brainforge-mcp

[![PyPI](https://img.shields.io/pypi/v/brainforge-mcp)](https://pypi.org/project/brainforge-mcp/) [![Python](https://img.shields.io/pypi/pyversions/brainforge-mcp)](https://pypi.org/project/brainforge-mcp/) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> Turn your markdown notes into an AI-powered knowledge graph.

[Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)을 MCP 서버로 구현한 프로젝트입니다. 마크다운 위키를 지식 그래프로 변환하고, 어떤 LLM 클라이언트에서든 탐색·분석할 수 있습니다.

## 핵심 아이디어

```
[원본 자료]  →  [AI가 유지하는 위키]  →  [지식 그래프]  →  [LLM이 탐색·분석]
 논문, 기사       sources/                 graph.json       MCP 도구로 질의
 메모, 영상       concepts/                                  인과 관계 추적
                  entities/                                  건강 진단
```

**MCP 도구가 하는 것**: 지식 그래프 탐색, 노드 분석, 인과 관계 추적, 위키 건강 진단
**LLM이 하는 것**: 원본 읽기 → 요약 → 위키 페이지 생성 → 위키링크/인과 관계 삽입

즉, brainforge-mcp는 **위키의 "눈"** 역할이고, LLM은 **위키의 "손"** 역할입니다.

---

## A-to-Z 예제: 논문 하나에서 지식 그래프까지

### Step 0: 설치 + 초기화

```bash
uvx brainforge-mcp init ~/my-brain
```

생성되는 구조:
```
my-brain/
├── raw/              # 불변 원본 (사용자가 넣는 곳)
│   ├── papers/
│   ├── articles/
│   ├── transcripts/
│   └── notes/
├── wiki/             # AI가 유지하는 위키
│   ├── sources/
│   ├── concepts/
│   ├── entities/
│   ├── syntheses/
│   ├── index.md
│   └── log.md
└── output/           # 블로그, 포트폴리오 등
```

### Step 1: MCP 클라이언트에 등록

Claude Desktop (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "wiki": {
      "command": "uvx",
      "args": ["brainforge-mcp", "--vault", "~/my-brain/wiki"]
    }
  }
}
```

Kiro / Cursor / VS Code (`mcp.json`):
```json
{
  "mcpServers": {
    "wiki": {
      "command": "uvx",
      "args": ["brainforge-mcp", "--vault", "~/my-brain/wiki"]
    }
  }
}
```

### Step 2: 원본 자료 넣기

논문 PDF를 마크다운으로 변환하여 `raw/papers/`에 저장합니다:

```bash
# 예: marker로 PDF → 마크다운 변환
marker_single "lora-paper.pdf" --output_dir ~/my-brain/raw/papers/
```

또는 웹 기사를 직접 마크다운으로 저장:

```markdown
<!-- raw/articles/2026-04-17_lora-explained.md -->
---
title: "LoRA 논문 쉽게 설명하기"
source: https://example.com/lora
date: 2026-04-17
type: article
---

# LoRA 논문 쉽게 설명하기
LLM의 가중치를 Freeze하고 저랭크 행렬만 학습하여...
```

### Step 3: LLM에게 인제스트 요청 (LLM이 하는 일)

채팅에서:
```
"raw/articles/2026-04-17_lora-explained.md를 읽고 위키에 인제스트해줘"
```

LLM이 원본을 읽고 다음 파일들을 생성합니다:

**wiki/sources/lora-explained.md** (소스 요약):
```markdown
---
title: "LoRA 논문 쉽게 설명하기"
created: 2026-04-17
updated: 2026-04-17
tags: [LoRA, Fine-Tuning, PEFT]
sources: [raw/articles/2026-04-17_lora-explained.md]
---

# LoRA 논문 쉽게 설명하기

## Kernel
모델 가중치를 Freeze하고 저랭크 행렬만 학습하여 VRAM 절감.

## 핵심 주장
1. Fully Fine-Tuning 대비 VRAM 대폭 절감
2. 성능은 동등하거나 우수
...
```

**wiki/concepts/lora.md** (개념 페이지):
```markdown
---
title: LoRA (Low-Rank Adaptation)
created: 2026-04-17
updated: 2026-04-17
tags: [개념, Fine-Tuning, PEFT]
sources: [raw/articles/2026-04-17_lora-explained.md]
---

# LoRA (Low-Rank Adaptation)

## Kernel
가중치 행렬 W를 직접 업데이트하지 않고, 저랭크 행렬 LoRA_A·LoRA_B만 학습.

> [!causal] 인과 관계
> [[lora]] →(가능하게 함)→ [[fine-tuning]]의 효율적 수행
> 신뢰도: 높음 | 출처: [[lora-explained]]

## 관련
[[fine-tuning]], [[transformer]], [[quantization]]
```

### Step 4: 그래프 빌드 (MCP 도구)

채팅에서:
```
"위키 그래프 재빌드해줘"
```
→ `rebuild_graph` 도구가 호출되어 wiki/ 마크다운에서 위키링크 + 인과 관계를 파싱하여 graph.json 생성.

### Step 5: 지식 탐색 (MCP 도구)

이제 그래프가 있으므로 탐색이 가능합니다:

```
"LoRA가 내 위키에서 어떤 위치야?"
```
→ `explain_node("LoRA")` 호출:
```
## LoRA (Low-Rank Adaptation)
카테고리: concepts | 태그: Fine-Tuning, PEFT

### 위치 분석
- 연결도: 9 (상위 26%) → 중간 연결자
- 인과 역할: 기반 기술 — 다른 1개 개념을 가능하게 함

### 인과 요약
- LoRA →(가능하게 함)→ Fine-Tuning의 효율적 수행

### 성장 제안
- 인과 관계 callout 추가 권장 (현재 1개)
```

```
"위키 상태 어때?"
```
→ `graph_summary()` 호출:
```
## 위키 건강 리포트

### 규모: 초기 단계
- 실제 페이지: 5개 (concepts 2, sources 1, entities 1)
- 밀도: 2.4 엣지/노드 → 낮은 밀도 — 위키링크 추가 권장

### 약점
- 미해결 노드 3개 (37.5%)
- 인과 비율 5.0% — callout 추가 권장

### 다음 행동
1. 미해결 페이지 생성: transformer(2연결), quantization(1연결)
```

---

## 특징

- 🔗 **위키링크 + 인과 관계** 기반 지식 그래프 자동 빌드
- 🧠 **의미 해석** — "연결 9개"가 아닌 "상위 26% 중간 연결자" 같은 맥락 분석
- 📊 **건강 진단** — 위키의 강점/약점/다음 행동을 구체적으로 제안
- ⚡ **인과 체인 추적** — 개념 간 "왜" 연결을 상류/하류로 분석
- 🔌 **MCP 표준** — Claude Desktop, Cursor, Kiro, VS Code 등 어디서든 동작

## 도구 목록

| 도구 | 설명 | 누가 호출? |
|------|------|-----------|
| `explain_node` | 노드 프로파일 — 위치 분석, 인과 역할, 성장 제안 | LLM이 자동 호출 |
| `find_path` | 두 개념 간 최단 경로 — 연결 강도, 매개 노드 해석 | LLM이 자동 호출 |
| `causal_chain` | 인과 네트워크 — 상류/하류, 관계 자연어 해석 | LLM이 자동 호출 |
| `graph_summary` | 위키 건강 리포트 — 규모, 밀도, 행동 제안 | LLM이 자동 호출 |
| `rebuild_graph` | 그래프 재빌드 — 마크다운 변경 후 갱신 | LLM이 자동 호출 |

> **참고**: 위키 페이지 생성/수정은 MCP 도구가 아닌 LLM이 직접 파일을 작성합니다.
> brainforge-mcp는 "읽기 + 분석" 전문이고, "쓰기"는 LLM의 역할입니다.

## 인과 관계 표기

위키링크(`[[]]`)는 연결만 표현합니다. "왜" 연결되는지는 인과 callout으로 명시합니다:

```markdown
> [!causal] 인과 관계
> [[메타러닝]] →(가능하게 함)→ [[DiscoRL]]의 RL 규칙 자동 발견
> 신뢰도: 높음 | 출처: [[discovering-sota-rl-algorithms]]
```

지원하는 관계 유형:
- `→(가능하게 함)→` / `→(성능 향상)→` / `→(성능 저하)→`
- `→(기반이 됨)→` / `→(발전시킴)→` / `→(대체함)→`
- `→(포함함)→` / `→(적용됨)→`

## 기존 옵시디언 볼트에 적용

이미 옵시디언을 사용 중이라면 `wiki/` 폴더를 볼트 안에 만들고 `--vault` 옵션으로 지정하면 됩니다. 기존 `[[위키링크]]`를 자동으로 파싱합니다.

## 라이선스

MIT

---

Inspired by [Andrej Karpathy's LLM Wiki idea](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).
