# 아카이브 v1 기능 계약서 (Feature Contract)

> v2 구현 시 반드시 유지해야 할 v1 UI/UX 및 전체 기능 명세.
> v2의 목적: 자료 업로드 시 자동 업데이트 + 반응형/인쇄/공유/번역. v1 동작은 그대로 보존.
> 브랜치: `review/v1-sources` (소스 전용, 프로덕션 푸시 없음)

## 1. 소스 파일

| 파일 | 역할 | 라인 |
|---|---|---|
| `build_dashboard.py` | PC 빌드 코어 (CSS/JS 인라인, 전역 함수) | 1522 |
| `build_pc.py` | PC 전용: 원본 해상도 이미지 base64 내장 | 47 |
| `build_mobile.py` | 모바일 전용: WebP 550px 썸네일 / 1200px 확대, 컴팩트 UI | 792 |
| `ib_section.py` | IB 자료 섹션 (`build_ib_section`, `IB_CSS`, `IB_JS`) | 433 |
| `memo_feature.py` | 메모 포스트잇 (`MEMO_CSS`, `MEMO_JS`, `MEMO_MODAL_HTML`, `memo_button`, `memo_button_full`) | 162 |
| `translate_posts.py` | 문단별 한국어 번역 (v2, Gemini 3.6-flash) | 183 |

JS/CSS는 별도 파일 없이 각 py에 문자열 상수로 인라인 (`build_dashboard.py:31` CSS, `:284`~ JS).

## 2. 입력 → 빌드 → 게시 트리거

```
[수집] ① iOS Safari 북마크릿 → HTML 업로드 → 인제스트 → posts.json
       ② Drive '1. 정보 업로드' → 크론 x-drive-inbox-monitor(12h) → X 아카이브
[정규 입력 경로]
  - X 게시물: ~/workspace/x-bookmarks/posts.json      (DEFAULT_POSTS)
  - 웹글:     ~/workspace/article-archive/index.json   (--index 기본값, articles 배열)
              └ 폴백: ~/workspace/goals/goal-10/files/index.json (build_pc.py:37)
  - 학습 링크/IB: ~/workspace/x-bookmarks/study_links.json
                  ~/workspace/x-bookmarks/resource_library/ib_juwon.json
                  ~/workspace/x-bookmarks/resource_library/index.json
[빌드] python3 build_dashboard.py --index <index.json>   → 단일 HTML (PC)
       python3 build_mobile.py                          → 단일 HTML (모바일, WebP 티어)
       python3 build_pc.py                              → goals/goal-10/files/웹글-아카이브-PC.html
[검증] python3 deploy_gate.py --expected-file <html>     → GitHub 2MiB 잘림 검사
[게시] git push origin main → https://baekgujuju.github.io/Archive/ (GitHub Pages)
```

## 3. 핵심 기능 계약 (PC/모바일 공통)

- 카드: `<details class="article" data-type data-author data-topics data-idx data-pin-id>` + `<summary class="card-toggle">`. 펼치면 `.full-text` 표시, `.summary` 숨김.
- 검색: 쉼표 구분 키워드, OR/AND 토글 버튼. 유형 탭(X/웹글/IB), 주제 칩, 인물 칩(5명 초과 시 "전체인물" 펼침), 북마크 색상 필터.
- 초기화(reset): `.reset` 버튼 — 전체 필터/검색 해제, 기본 레이아웃 복원.
- 메모: `📝 메모` 버튼 → 모달 → `localStorage['archive_memo_'+id]` 저장. 카드에 메모 아이콘 표시.
- 북마크: 7색 (빨강/주황/노랑/초록/파랑/남색/보라) hex를 `archive-bookmarks`에 직접 저장 (v1 호환). 카드 좌측 테두리 색상.
- 핀: `archive-pinned`, 상단 "📌 고정된 글" 섹션으로 이동 (원위치 복원 지원).
- 숨기기/복원: `archive-deleted-posts`, `archive-deleted-replies`. 설정 패널에서 복원.
- 답글: `<details class="replies">` "💬 댓글 N개", 스레드 배지, 댓글별 삭제 버튼.
- 인용글: `<details class="quoted" open>`, 인용 이미지 `quoted-media`.
- URL 인라인: `url_inline_html()` — 본문 링크의 스크랩 title/content를 1200자까지 카드 아래 직접 표시.
- 공유: 인쇄(`window.print`), 이미지 공유(캔버스 렌더→`navigator.share` 폴백 다운로드), HTML 공유(Blob→`navigator.share` 폴백 다운로드).
- Drive 동기화: 공개 OAuth client_id `43687402502-...apps.googleusercontent.com`, `drive.appdata` 스코프, 상태키 `archive-pinned/bookmarks/bookmark-names/deleted-replies/deleted-posts` + `archive_memo_*`, 2초 간격 변경분 업로드.
- 인쇄 CSS: `@page { margin: 14mm 12mm }` + `@media print { body { zoom: 0.5 } }` (iOS 390px→A4 대응).
- 키보드: ESC 닫기. 제스처(모바일): 라이트박스 핀치/팬 줌, 리더 헤더 아래로 스와이프 시 닫기.

## 4. 10-12 분할 이미지 뷰어 (PC 전용, build_dashboard.py)

CSS (`#article-reader-modal.img-expanded`, 139–149행):
```css
.reader-content{max-width:96vw;width:96vw;height:94vh;max-height:94vh;padding:0;display:flex;overflow:hidden}
.reader-body{display:flex;width:100%;height:100%;min-height:0}
.reader-text-panel{width:400px;flex:none;overflow-y:auto;padding:32px 24px;border-left:1px solid #e0d8c8;background:#fffdf8;order:2}
.reader-image-panel{flex:1;overflow:auto;display:flex;align-items:center;justify-content:center;padding:24px;background:#141414}
.reader-image-panel img{width:100%;height:100%;object-fit:contain;cursor:zoom-out}
.reader-text-panel .reader-images img.active{border-color:var(--accent)}
```

JS 동작 (`attachImgHandlers`/`restoreModal`, 505–556행):
1. 카드 클릭 → `#article-reader-modal` 표시 (`.reader-body`에 제목/메타/본문/이미지/연관글/저자).
2. 본문 이미지 클릭 (`cursor:zoom-in`) → 원본 HTML을 `body.dataset.orig`에 보관 후 `.reader-body`를 `<div class="reader-image-panel">` + `<div class="reader-text-panel">`(제목/메타/본문/썸네일 스트립/연관글/저자)로 교체, `img-expanded` 클래스 추가. 클릭한 이미지를 `cloneNode(false)`로 패널에 복제 (`object-fit:contain`).
3. 좌측 썸네일 클릭 → 우측 큰 이미지 `src` 교체 + `active` 테두리 이동.
4. 우측 큰 이미지 클릭 또는 ESC → `restoreModal()`: `dataset.orig` 복원, 클래스 제거.

## 5. 모바일 리더/라이트박스 (build_mobile.py)

- `#reader-sheet` `<dialog>` 전체화면: `.reader-header`(← 목록, 제목, 인쇄/이미지공유/HTML공유) + `.reader-body`(스크롤). 헤더 아래 스와이프(80px) 시 닫기.
- `.lightbox` `<dialog>`: `.lightbox-stage` 팬/핀치 줌 (`paint()` → `translate(x,y) scale(s)`), `.lightbox-strip` 썸네일, 도움말.
- `#scroll-top-btn` (↑): `scrollY>600` 시 표시, 클릭 시 `scrollTo({top:0,behavior:'smooth'})`.
- 카드 미디어는 접힌 상태에서 숨김 (`.post-media{display:none}`, `[open]` 시 표시). 44px 터치 타겟, `env(safe-area-inset-*)` 대응.

## 6. 주제/연관 도출

```python
def topics_of(article):  # build_dashboard.py:1079
    return article.get("topics") if isinstance(article.get("topics"), list) else []
```
- 주제는 원시 `category`가 아니라 정규화된 `topics[]` 리스트를 사용. 색상은 `sum(map(ord, topic)) % 4` → 4색 `TAG_COLORS`.

```python
def compute_related(articles, top_n=5):  # build_dashboard.py:1333
```
- 점수 = 주제 겹침×10 + 저자 일치×8 + 키워드(2자↑ 한글/3자↑ 영문, 불용어 제외) 겹침 (최대 15). score>5인 상위 5개. 토큰 사용 없음.
- 렌더: `article_html(article, related=[...])` → "🔗 연관 게시물" 최대 5개 링크.

## 7. 번역 스키마/캐시 (translate_posts.py)

- 대상: 한국어([가-힣])가 전혀 없는 글 (`is_english_only`).
- 문단 분리: 빈 줄(`\n\n`) 기준 (`split_paragraphs`).
- 캐시: `v2/data/translations.json` — `post_id → {"paragraphs": [...], "translated": [...]}` (순서 동일, 개수 불일치 시 경고 후 스킵).
- 호출: `gemini -m gemini-3.6-flash -p "[번호] 문단..."` 프롬프트, `[번호] 번역문` 형식 파싱. API 키는 `~/.gemini/.api_key` 런타임 참조 (저장소 미포함).
- UI 연결은 v2 작업 범위 (v1에 번역 UI 없음).

## 8. 제외된 것 (민감정보 제거 확인)

- `posts.json`, `index.json`, `media-*` 등 개인 수집 데이터 미포함.
- API 키/비밀번호/토큰 없음. 포함된 Google OAuth client_id는 공개 ID (브라우저에 노출되는 값).
- `/home/hatch/workspace` 경로는 빌드 기본값일 뿐 자격증명 아님.
