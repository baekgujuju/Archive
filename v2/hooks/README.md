# v2 자동화 후크

> SOURCE ONLY — `review/v2-automation` 브랜치. 프로덕션 게시(`main` 푸시)는
> 최종 QA 전까지 비활성. 루트 v1 파일(`build_dashboard.py` 등)은 수정하지 않음.

## 파일

| 파일 | 역할 |
|---|---|
| `../ib_adapter.py` | 정규 IB 소스 → v2 IB 레코드 (안정적 `ib_pin_*`/`ib_*` ID) |
| `post_ingest_hook.py` | 업로드-성공 후크: 락 → 변경감지 → 빌드 → 번역 → 커밋 (게시 기본 OFF) |
| `monitor.py` | 5분 경량 모니터: 소스 해시 비교만, 변경 없으면 모델 호출 없음 |
| `.monitor_state.json` | 모니터 워터마크 (자동 생성, 커밋 불필요) |
| `.post_ingest.lock` / `.monitor.lock` | flock 락 파일 (자동 생성) |

## 동작 흐름

```
[5분 모니터] monitor.py
  → 5종 소스 해시(X posts.json / Web index.json / IB 3종) vs 워터마크
  → 변경 없음: 조용히 종료 (LLM/API 호출 없음)
  → 변경 있음: post_ingest_hook.py 실행
      1. flock排他 락
      2. build_data.py --check (결합 소스 해시로 재확인)
      3. translations.json 백업 → 전체 빌드(원자적 교체, 번역 캐시 자동 보존)
         → 신규 영어 글만 translate_posts.py (캐시 히트 시 스킵)
      4. git status --porcelain -- data/ 로 변경 파일 확인 → 커밋
      5. 게시: V2_PUBLISH=1 일 때만 git push (기본 비활성)
```

## 활성화 단계 (정확)

1. 브랜치 반영 확인:
   ```
   git fetch origin && git checkout review/v2-automation
   python3 v2/ib_adapter.py          # 39건 출력되는지 확인
   python3 v2/hooks/monitor.py       # 첫 실행: 워터마크 생성 후 종료 (exit 0)
   ```

2. 5분 모니터 크론 등록 (아래 스펙 그대로):
   - id: `v2-archive-monitor`
   - schedule: `interval`, `every: 5m`, timezone `Asia/Seoul`
   - owner: `goal:goal-10` (웹글 아카이브 목표)
   - body: `python3 ~/workspace/article-archive/v2/hooks/monitor.py` 실행.
     변경 감지 시에만 후크가 빌드/번역/커밋 수행. 게시(push)는 하지 않음.
   - delivery: 변경 시에만 보고 (본문에서 조용히 종료하므로 별도 설정 불필요)

3. 게시 활성화 (최종 QA 완료 후에만):
   ```
   V2_PUBLISH=1 python3 v2/hooks/post_ingest_hook.py
   ```
   또는 크론 본문에 환경변수 추가. 활성화 전까지는 커밋만 되고 push는 안 됨.

## 새 업로드가 Pages에 도달했는지 검증

1. Drive "5. X 게시물, 메모모음"에 테스트 HTML 1건 업로드
2. 12시간 이내 `x-drive-inbox-monitor`가 `posts.json`에 병합 (상태: `drive_inbox_state.json`)
3. 5분 이내 `v2-archive-monitor`가 변경 감지 → `v2/data/` 커밋 생성
   - 확인: `git log --oneline -3 -- v2/data/` (브랜치에서)
   - 확인: `v2/data/manifest.json`의 `version` 시각 갱신
4. 게시 활성화 후: `git push` → 1~2분 후 `https://baekgujuju.github.io/Archive/v2/` 에서
   새 글 카드 확인 (시크릿 창 권장, 캐시 주의)
5. 번역 검증: 영어 글이면 `v2/data/translations.json`에 해당 post_id 존재 확인

## 안전장치

- **동시 실행 방지**: `monitor.py`·`post_ingest_hook.py` 각각 flock排他 락
- **멱등성**: `--check` 결합 해시로 이중 확인, 변경 없으면 아무것도 안 함
- **원자적 빌드**: 스테이징 → 검증(해시) → 교체, 실패 시 이전 세대 유지
- **번역 캐시 보존**: `build_data.py`가 스테이징에 자동 복사 + 후크의 백업/복원 이중 안전망
- **ID 안정성**: `ib_pin_*`·`ib_*` 모두 md5 기반 (v1 `hash()` 랜덤 버그 수정)
  → 기존 `localStorage['archive_memo_ib_*']` 메모 유지
- **자격증명**: git push는 기존 `~/.git-credentials` (credential.helper store) 사용.
  새로 저장하는 비밀값 없음. Gemini 키는 `~/.gemini/.api_key` 런타임 참조.
- **게시 게이트**: `V2_PUBLISH=1` 명시 없이는 어떤 경로로도 push하지 않음
