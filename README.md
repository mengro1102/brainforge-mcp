# llm-wiki-mcp

> Turn your markdown notes into an AI-powered knowledge graph.

Karpathy의 [LLM Wiki 패턴](https://x.com/karpathy/status/1909363262791352494)을 MCP 서버로 구현한 프로젝트입니다. 마크다운 위키를 지식 그래프로 변환하고, 어떤 LLM 클라이언트에서든 탐색·분석할 수 있습니다.

## 특징

- 🔗 **위키링크 + 인과 관계** 기반 지식 그래프 자동 빌드
- 🧠 **의미 해석** — 단순 데이터가 아닌 "왜 중요한지"를 분석
- 📊 **건강 진단** — 위키의 강점/약점/다음 행동을 제안
- ⚡ **인과 체인 추적** — 개념 간 "왜" 연결을 상류/하류로 분석
- 🔌 **MCP 표준** — Claude Desktop, Cursor, Kiro, VS Code 등 어디서든 동작

## 빠른 시작

### 1. 새 위키 초기화

```bash
uvx llm-wiki-mcp init ~/my-brain
```

### 2. MCP 클라이언트에 등록

```json
{
  "mcpServers": {
    "wiki": {
      "command": "uvx",
      "args": ["llm-wiki-mcp", "--vault", "~/my-brain/wiki"]
    }
  }
}
```

### 3. 채팅에서 사용

```
"LoRA가 내 위키에서 어떤 위치야?"     → explain_node
"메타러닝이랑 Transformer 어떻게 연결돼?" → find_path
"DiscoRL의 인과 관계 보여줘"           → causal_chain
"위키 상태 어때?"                      → graph_summary
```

## 도구 목록

| 도구 | 설명 |
|------|------|
| `explain_node` | 노드 프로파일 — 위치 분석, 인과 역할, 연결 맥락, 성장 제안 |
| `find_path` | 두 개념 간 최단 경로 — 연결 강도 해석, 매개 노드 분석 |
| `causal_chain` | 인과 네트워크 — 상류/하류 분리, 관계 자연어 해석 |
| `graph_summary` | 위키 건강 리포트 — 규모 판정, 강점/약점, 행동 제안 |
| `rebuild_graph` | 그래프 재빌드 — 마크다운 변경 후 갱신 |

## 위키 구조

```
my-brain/
├── raw/          # 불변 원본 (논문, 기사, 메모)
│   ├── papers/
│   ├── articles/
│   ├── transcripts/
│   └── notes/
├── wiki/         # AI가 유지하는 위키
│   ├── sources/     # 원본 요약
│   ├── concepts/    # 개념 페이지
│   ├── entities/    # 인물·조직·모델
│   ├── syntheses/   # 종합·분석
│   ├── index.md     # 목차
│   └── log.md       # 작업 기록
├── output/       # 블로그, 포트폴리오 등
└── graph.json    # 지식 그래프
```

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

Inspired by [Andrej Karpathy's LLM Wiki idea](https://x.com/karpathy/status/1909363262791352494).
