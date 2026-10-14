# 아카이브 홈 화면·PC 앱 바로가기

최종 후보 기능. 공개 배포는 사용자 최종 승인 후 진행합니다.

## 아이폰·아이패드
Safari에서 배포 주소를 열고 공유 → 홈 화면에 추가를 선택합니다. 이름은 아카이브로 표시됩니다. 앱으로 열기 옵션이 제공되면 켜 둡니다. 기존 바로가기의 아이콘은 기기 캐시에 남을 수 있으므로 배포 후 기존 바로가기를 지우고 다시 추가합니다. 브라우저의 홈 화면 추가 절차를 사이트가 대신 실행할 수는 없습니다.

## PC
Chrome 또는 Edge에서 배포 주소를 열고 주소창의 설치 아이콘 또는 메뉴의 앱 설치를 선택합니다. 설치 후 시작 메뉴에서 실행하고 작업 표시줄에 고정할 수 있습니다. 바탕 화면 바로가기 제공 여부와 메뉴 이름은 브라우저·OS 버전에 따라 다릅니다. 일반 URL 바로가기를 직접 만들 때 사용할 archive.ico도 제공합니다.

## 운영·유지보수
- site.webmanifest의 id/start_url/scope는 모두 ./입니다. GitHub Pages의 /Archive/v2/ 경로를 유지하며 로컬 미리보기에도 맞습니다. 검색/글 선택 URL 대신 기본 아카이브로 실행합니다.
- display standalone으로 설치된 웹 앱은 독립 창을 사용합니다. 태블릿과 폴더블 사용을 위해 화면 방향을 강제하지 않습니다.
- index.html의 apple-touch-icon 180px, manifest PNG 192/512px, favicon PNG 32px, ICO 16~256px를 모두 함께 배포합니다. 모든 경로는 상대경로입니다.
- icons/archive-master.png가 생성 원본입니다. 책장과 만년필을 모티프로 image_gen 스킬·도구로 만들었고, Pillow로 크기별 포맷을 패키징했습니다. 인증정보나 외부 폰트/이미지 서비스는 필요하지 않습니다.
- 별도 서비스 워커/오래된 소스 캐시를 추가하지 않았습니다. 앱 설치가 완전한 오프라인 보장이라는 뜻은 아닙니다. 기존 아카이브 데이터 캐시 정책을 따릅니다.
- iOS Safari 실제 기기의 홈 화면 아이콘과 앱 실행, Windows Chrome/Edge 설치는 배포 후 직접 확인해야 합니다. 데스크톱 브라우저 미리보기만으로 실제 iOS 검증 완료라고 보고하지 않습니다.

근거: https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/SafariWebContent/ConfiguringWebApplications/ConfiguringWebApplications.html
https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable
