# v2 릴리스 노트 (2026-10-13)

## 배포 정보
- **브랜치**: `main`
- **커밋**: `e01dcf87c10dcb692b1c8d0ff7bba6092fd507c1`
- **소스 ZIP**: SHA256 `eb40d84a11f1854b9190620ba6a222ff4c2a9518fdce4577b668b4cb11d1be18`

## 데이터
- X: 158건
- Web: 2건
- IB: 63건
- 총 223건 (원본 전부 보존)
- 번역 캐시: 12건

## 주요 기능
- PC: 요약 네이티브 인라인 확장
- 모바일: 전체 리더
- 관련 글 카드 내부 표시
- 인라인 이미지 → 스플릿 뷰어
- 홈 버튼/터치 타겟 개선
- Drive 5분 자동 인제스트 → 빌드 → 발행

## 정직한 한계 (Honest Limits)

### 1. Drive 인제스트 형식
- **직접 지원**: HTML/HTM만
- **미지원**: PDF, 이미지(PNG/JPG), 텍스트(MD/TXT)의 범용 변환은 아직 구현되지 않음
- 미지원 형식은 pending 유지 (silent 처리하지 않음)

### 2. 정본 변경 감지
- **지원**: 웹 정본(`study_links.json`) 및 IB 정본 변경 감지
- IB 첫 동기화 시 rich body 보존, 이후 정본 변경(짧아져도) 반영

### 3. 실기기 테스트
- **미확인**: 실제 iPhone에서의 OAuth 플로우는 물리적으로 테스트되지 않음
- **미확인**: iOS Safari에서의 전체 동작은 실기기 확인 필요
- 데스크톱 Chrome에서는 159건 카드 렌더링 확인됨

### 4. 자동화
- 5분 스케줄러: `v2-archive-5m-poll` (활성)
- 12h 크론의 HTML 인제스트는 제외됨 (경쟁 방지)
- 변경 없으면 모델 호출 없음

## 테스트 결과
- retry_execution: 13/13 통과
- render: 48 통과
- bootstrap: 28 통과
- drive: 17 통과
- lifecycle: 6 통과
- integration: 11 통과 (6개 JS classic 파싱)
- builder: 2 통과

## 2026-10-13 운영 감사 반영

### 스케줄러 변경
- **이전**: Hatch agent cron (5분마다 어시스턴트 깨움, 288회/일)
- **현재**: 영속 Python 워커 (`v2/hooks/persistent_worker.py`)
  - 300초마다 `scheduler_runner.py` 실행
  - 모델 호출 없음 (순수 Python)
  - singleton flock + PID 파일
  - 로그 1MB 로테이션
  - V2_PUBLISH=1, V2_PUBLISH_BRANCH=main을 자식에 프로그래밍 방식으로 설정

### Watchdog
- 12h 크론 (`x-drive-inbox-monitor`)이 워커 상태 확인 및 재시작 담당
- VM 재시작 시 최대 12h 후 복구 (문서화된 한계)

### 타임아웃
- Drive 인제스트: 600초
- Monitor: 2400초 (번역 1800초 허용)
- 전체 실행 flock으로 중복 틱 스킵
