# v2.5.8 검토 후보 — 아직 배포하지 않음

변경: 공개 공통 설정 자동 읽기 + 단일 내보내기/PIN + 삭제 항목 관리만 노출 + 노란 형광펜(선택/제거/문맥 앵커).

공통 설정은 GitHub Pages data/shared-state.json에서 로그인 없이 읽습니다. 내보낼 때만 drive.file OAuth/PIN을 사용하며 일반 Drive의 archive-shared-state.json을 생성합니다. appProperties는 OAuth 앱 간 공유되지 않으므로 공개 custom properties(archive=baekgujuju/Archive, purpose=shared-state)를 사용합니다. Muse는 연결된 소유자 계정의 파일만 조회합니다. 서버는 명시된 필드만 발행하며 인증정보 등 나머지는 제외합니다. 네 자리 PIN은 실수 방지이며 계정 인증이 실제 Drive 쓰기 경계를 제공합니다.

최초 사용자 내보내기 전까지 shared-state.json을 만들지 마세요. 기존 private appData/로컬 메모를 빈 상태로 덮어쓰지 않습니다. 사용자는 기존 설정이 있는 기기에서 새 OAuth 동의 후 최초 내보내기를 해야 합니다. 방문자 수정은 그 창에서만 유지되고 새로고침은 마지막 공통 설정으로 되돌립니다. 소유자도 내보내지 않은 변경은 동일합니다. 내보내기 후에는 접수/발행 대기를 표시하며 기존 5분 스케줄러가 발행한 뒤 확정됩니다.

worker: scheduler_runner.py의 마지막 publish_settings.py. 기존 post_ingest 잠금/브랜치 검사. 데이터 빌더가 shared-state.json을 원자적 교체 시 보존합니다. 설정 변경은 자료 최신업데이트 시각을 바꾸지 않습니다. 실패 push는 다음 주기 재시도합니다. 기존 글/이미지/v1 파일을 수정하지 마세요.

형광펜은 본문 텍스트만 지원하며 위치+앞뒤 문맥을 저장합니다. 문맥이 달라져 위치를 확정하지 못하면 잘못 칠하는 대신 표시를 생략합니다. 형광펜도 공통 내보내기에 포함됩니다. 모바일 Chromium 390px 표시/가로 넘침 없음 확인. 실제 iOS 길게 누르기는 미검증입니다. 인쇄/이미지 공유 내 형광펜 재현은 아직 검증하지 않았습니다.

검증: render48, bootstrap30, integration11, related duplicates, tool metadata, updated label, inline print, shared settings lifecycle, quote anchors5, publisher3, build_data3 통과. legacy private-sync lifecycle suite는 구 사양이므로 새 공통 읽기 suite로 대체하며 기존 파일은 참고용으로 보존.

교차 검토를 요청합니다. 특히 properties 가시성, Muse 실제 CLI 파일조회 응답, 스케줄러 단일 worker 재시작, 최초 내보내기 없음 처리, public baseline vs active draft, 형광펜 선택/대화창 top layer/다중 문단, 소스 갱신 시 보존을 확인하세요. 자동 배포 금지. 카페 과거 복구/북마크릿은 추후 범위입니다.

## 교차 검토 반영 및 배포 승인
사용자가 2026-10-16 공개 배포를 명시 승인했습니다. 앵커의 JSON/필드/길이/숫자 검증을 클라이언트와 발행기에 추가, iOS pointerdown 취소를 mousedown만 취소하도록 변경, 데이터 빌더는 공통 설정 스키마 오류 시 이전 세대를 보존하고 중단합니다. Drive CLI v3/custom properties 실제 지원은 배포 시 읽기 전용 쿼리로 확인하십시오. 지원 확인 후 변경 파일만 배포하고 기존 단일 worker를 안전하게 재시작하십시오. 빈 shared-state 생성/실제 메모 대신 내보내기/기존 appData 변경은 금지합니다. 소유자의 최초 내보내기는 사용자가 직접 진행합니다.
