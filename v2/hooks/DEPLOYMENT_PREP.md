# v2 배포 준비 (2026-10-13)

## 1. 기존 업로드 형식/러너 조사

### 12시간 러너 (`monitor_drive_inbox.py`)
| 형식 | 처리 |
|------|------|
| `.html` | `ingest_post_html.py`로 전체 인제스트 → posts.json |
| `.md`, `.txt`, `.png`, `.jpg`, `.jpeg` | 다운로드만 (인제스트 없음, "others") |
| 기타 형식 | 다운로드만 ("기타 형식" 표시) |
| `.pdf` | **전용 러너 없음** (기타 형식으로 처리) |

### 5분 폴 (`drive_poll.py`) — 지원 경로
| 형식 | 처리 |
|------|------|
| `.html`, `.htm` | `ingest_post_html.py`로 인제스트 → posts.json |
| 비HTML | **pending 유지** (processed에 기록하지 않음) |

### IB
- Drive 업로드가 아닌 `ib_adapter.py` 정본 병합
- 정본: `study_links.json`, `resource_library/index.json`, `ib_juwon.json`

## 2. 경쟁 상태 (Race Condition)

**문제**: 12h 러너는 `flock`을 사용하지 않음 (grep 0건 확인).
격리된 폴 상태(`.drive_poll_state.json`)만으로는 공유 `posts.json`에 대한
동시 쓰기를 방지할 수 없음.

**해결**: 최종 QA 후 12h 인제스트를 일시 중지하고 5m 결정적 폴을 활성화.
- 12h의 무관한 무거운 작업(페르소나 등)은 별도로 안전하면 유지
- 5m 폴과 12h 인제스트가 동시에 `posts.json`에 쓰지 않도록 보장

## 3. 스케줄러 러너

`scheduler_runner.py`:
- 순서: `drive_poll.py` → `monitor.py` (순차 실행)
- 실패 시 즉시 중단하고 로그 기록 (`scheduler.log`)
- 변경 없으면 모델 호출 없음 (monitor 해시 비교)

## 4. 활성화 절차 (최종 QA 후)

1. 12h 크론의 인제스트 부분 일시 중지
2. 5m 크론 활성화: `*/5 * * * * python3 v2/hooks/scheduler_runner.py`
3. 스케줄러 readback 확인 (로그에서 실행 확인)
4. 격리된 입력/상태로 ingestion-to-Pages 스모크 테스트
   - 이미 공개된 아카이브 HTML 사용
   - 합성 프로덕션 레코드 없음
   - 사용자 비공개 레코드 없음

## 5. 상태

- [ ] 최종 QA 완료
- [ ] 12h 인제스트 일시 중지
- [ ] 5m 스케줄러 활성화
- [ ] 스케줄러 readback 확인
- [ ] 스모크 테스트 (격리 환경)
- [ ] 프로덕션 승인
