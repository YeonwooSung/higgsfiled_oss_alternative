# 통합 앱(Studio) 설계 — Product Lock · Camera Sketch · Pocket VFX를 하나로

작성일: 2026-10-25 · 상태: 설계 (구현 전)

## 1. 의도

지금까지 세 기능은 서로 독립된 Next.js 앱 세 개(`apps/product-lock` :3000, `apps/camera-sketch` :3001, `apps/pocket-vfx` :3002)로 만들었다. 백엔드(FastAPI `services/api`, worker `services/worker`)와 공통 패키지(`packages/ui`, `packages/web-core`, `packages/contracts`)는 이미 하나다. 해커톤에는 **세 기능을 하나의 웹 앱으로 묶어 한 번만 출품**한다. 그래서 심사위원이 한 주소에서 세 기능을 모두 써 보고, 한 기능의 결과를 다음 기능으로 이어 붙여 하나의 결과물을 만드는 흐름까지 볼 수 있어야 한다.

## 2. 사용자가 정한 것

- 세 기능을 **하나의 웹 앱**으로 만들어 출품한다.
- 앱을 열면 **Camera Sketch가 기본 화면**이다.
- 기능 사이 이동은 **왼쪽의, 이름이 보이는 사이드바**로 한다. 시안 A안이며, 아이콘만 보이는 얇은 레일(B)이나 상단 탭(C)은 고르지 않았다.
- 세 기능은 **기본적으로 독립적으로 동작**한다. 여기에 더해 **한 기능의 결과를 다른 기능의 입력으로 넘기는 기능**을 둔다.
- 넘기기 경로는 아래 세 가지다(5장).
  - Product Lock 결과 영상 → Pocket VFX
  - Camera Sketch 결과 영상 → Pocket VFX
  - Product Lock 커버 이미지 → Camera Sketch
- UX 구성은 위 흐름에 맞게 개편한다. 세부 판단은 설계자에게 맡겼다.

## 3. 목표 · 비목표 · 반목표

**목표**
1. 한 origin, 한 Next.js 앱에서 세 기능의 기존 동작이 모두 그대로 된다. 기존 테스트와 기존 walk-through 단계가 모두 통과해야 한다.
2. 사이드바로 기능을 오가고, 현재 세션에서 만든 프로젝트를 기능과 상관없이 다시 찾을 수 있다.
3. 결과 화면에서 버튼 하나로 다음 기능에 결과를 넘길 수 있다. 다시 업로드하지 않으며, 업로드와 같은 검사를 거친 뒤 대상 기능의 편집 화면이 열린다.
4. Mock 프로필에서 다음 데모 체인 전체가 끝까지 실행된다: Product Lock 렌더 → 커버를 Camera Sketch로 → Geometry 렌더 → 영상을 Pocket VFX로 → Quick render.

**비목표**
- 세 기능의 편집기를 하나의 타임라인이나 캔버스로 합치지 않는다.
- 새 기능, 새 모델, GPU나 Comfy 연동은 이 작업 범위가 아니다.
- 각 기능 스펙(docs/01~03)의 제한값(길이, 해상도, 프리셋)은 바꾸지 않는다. 넘겨받은 입력도 같은 제한을 따른다.
- Product Lock으로 들어오는 넘기기 경로는 만들지 않는다. Product Lock에는 배경이 분리된 제품 사진이 필요한데, 다른 기능의 결과는 그런 형태가 아니다.
- 로그인, OAuth, 결제는 다루지 않는다. 익명 세션을 그대로 쓴다.

**반목표** (기술적으로 동작해도 실패로 본다)
- **Mock 결과가 실제 결과처럼 보이는 것.** 넘기기 체인 중간에 Mock이 하나라도 있으면 최종 결과와 공유 페이지에 Mock이 표시되어야 한다.
- **Camera Sketch 편집 화면이 눈에 띄게 좁아지거나 느려지는 것.** 30fps 미리보기 목표가 있다(docs/02).
- **원본 보존 보장이 깨지는 것.** 넘기기는 원본 바이트를 그대로 복사해야 한다. 재인코딩한 사본을 "원본"으로 다루면 안 된다.
- **기능별 스펙의 검사(정규화, 길이, 형식 제한)를 넘기기 경로가 건너뛰는 것.**

## 4. 화면 구조 (UX)

### 4.1 앱 셸
- **주소 구조**: `/`는 `/camera`로 이동시킨다. 각 기능은 `/camera`, `/product`, `/vfx`(시작 화면)과 `/camera/p/[id]`, `/product/p/[id]`, `/vfx/p/[id]`(편집 화면)을 쓴다. 공유 페이지는 `/s/[token]` 하나로 두고, share meta가 알려주는 기능에 맞는 뷰를 그린다.
- **왼쪽 사이드바** (폭 약 200px, 이름이 보임)
  - **위쪽**: 기능 목록. 순서는 Camera Sketch → Product Lock → Pocket VFX이고, 각 항목에 아이콘, 이름, 한 줄 설명이 있다. 현재 기능은 강조한다.
  - **아래쪽**: "내 프로젝트". 현재 세션의 프로젝트를 최근 순으로 보여주고, 각 항목에 기능 아이콘과 제목을 붙인다. 넘기기로 생긴 프로젝트도 여기서 찾을 수 있다.
  - **접기 (설계자 판단)**: 노트북 폭 편집 화면에서는 접을 수 있고, 접으면 아이콘만 남는다. 접힘 상태는 `localStorage`에 기억한다. 저장소를 읽지 못하면 펼친 상태가 기본이다.
  - **휴대폰 폭**: 사이드바를 숨기고, 상단 바의 메뉴 버튼으로 드로어를 연다.
  - **공유 페이지**에는 사이드바를 두지 않는다. 결과만 보여주는 공개 페이지이기 때문이다.
- **상단 바**: 셸이 하나로 소유한다. 브랜드, 프로젝트 제목, 저장 상태, Mock 배지, 언어 전환이 들어간다. 되돌리기·다시 하기 같은 기능별 버튼은 기능이 넣는 슬롯으로 받는다. 지금 세 앱에 있는 TopBar와 LanguageToggle은 이 셸로 대체한다.
- **테마 (설계자 판단)**: 셸은 하나의 어두운 테마를 쓰고, 기능마다 강조색은 그대로 둔다. Camera Sketch는 청록 `#66DCF2`, Pocket VFX는 주황 `#FF9A52`, Product Lock은 초록 계열로, 어두운 배경에서 대비가 맞도록 밝기를 조정한다. Product Lock은 지금 밝은 테마인데 셸에 맞춰 어둡게 바꾼다. 단, 제품 가장자리가 잘 보이도록 미리보기 영역(stage)만은 밝은 중성색을 유지한다. 색은 `@oc/ui`가 쓰는 의미 이름(app/panel/ink/muted/accent/border/danger/stage)을 기능 범위로 다시 정의하는 방식으로 적용한다.
- **언어**: 언어 전환은 하나다. 사전은 셸 공통과 기능별 namespace로 나누고, 고른 언어는 기억한다.

### 4.2 넘기기 UX
- **결과 화면의 "다음 단계" 영역**: 그 결과로 할 수 있는 넘기기 버튼을 보여준다.
  - 예: "Pocket VFX에서 효과 넣기", "Camera Sketch에서 카메라 움직이기".
  - 넘길 수 있는 결과가 없는 기능(Pocket VFX)에는 이 영역을 두지 않는다.
- **버튼을 누르면**: 대상 기능에 새 프로젝트가 생기고, 결과 파일이 입력으로 들어간 상태로 그 기능의 편집 화면이 열린다. 원래 프로젝트는 바뀌지 않는다.
- **열린 화면의 상태**: 대상 기능에서 업로드를 막 끝낸 직후와 같다.
  - Pocket VFX는 Trim 화면이 열린다. 5초짜리 결과라 바로 Prepare clip을 누를 수 있다.
  - Camera Sketch는 장면 분석을 시작하는 화면이 열린다.
- **출처 표시**: 넘겨받은 프로젝트의 입력 옆에 출처 배지를 둔다. 예: "Product Lock 결과에서 가져옴". 배지를 누르면 원래 프로젝트로 간다. 원래 프로젝트가 삭제되었으면 링크 없이 텍스트만 남긴다.
- **진행 표시**: 넘기는 동안(복사와 검사)에는 버튼에 진행 상태를 보여준다. 검사가 실패하면 대상 기능의 업로드 오류 문구를 그대로 쓴다. 예: Pocket VFX의 길이 초과.

### 4.3 데모 체인
Camera Sketch가 기본 화면이므로 그 시작 화면에 "세 기능 이어 쓰기" 안내 한 줄을 둔다. 내용은 Product Lock부터 시작하는 체인 설명과 이동 링크다. 체인은 2장의 세 경로를 이어 붙인 것이다.

## 5. 넘기기 동작 규칙

### 5.1 허용 목록 (서버 allowlist)

| 원본 기능 | 원본 asset role | 대상 기능 | 대상 upload role |
|---|---|---|---|
| product-lock | `render` (MP4) | pocket-vfx | `clip` |
| camera-sketch | `geometry_video`, `cinematic_video` | pocket-vfx | `clip` |
| product-lock | `cover` (PNG) | camera-sketch | `scene` |

이 목록에 없는 조합은 422다. role 이름은 현재 worker가 쓰는 이름이다(`services/worker/oc_worker/executors/*.py`, `services/api/oc_api/routes/shares.py`의 `SHAREABLE_ROLES`).

### 5.2 API (자체 endpoint, 설계자 판단)
`POST /api/v1/handoffs` body는 `{ "source_asset_id": "...", "target_app": "pocket-vfx" | "camera-sketch" }`이고 `Idempotency-Key`를 지원한다. 기존 공통 규칙을 따른다. 같은 body면 같은 결과, 다른 body로 키를 재사용하면 409다.

1. **소유 확인**: 원본 asset이 현재 세션 소유이고 `ready`인지 확인한다. 기존 `owned_asset`을 쓴다. 다른 세션의 asset이면 404다.
2. **허용 확인**: (원본 기능, role) → 대상 기능 조합이 5.1에 있는지 확인한다.
3. **프로젝트 생성**: 대상 기능에 새 프로젝트를 만든다. 기본 config는 기존 `POST /projects`와 같고, 제목은 원본 프로젝트 제목을 바탕으로 한다.
4. **파일 복사**: 원본 객체를 새 object key로 바이트 그대로 복사한다. key는 새 session/project UUID prefix를 쓰고, 복사는 storage 계층에서 한다. local과 S3 백엔드 모두 지원한다.
5. **asset 생성**: 대상 upload role로 새 Asset을 만든다.
   - `source_asset_id`는 원본 asset이다.
   - `meta_json.handoff`에는 다음을 기록한다: `source_app`, `source_project_id`, `source_job_id`, `source_sha256`, `mock`.
6. **검사**: 상태를 `uploaded`로 두고, 일반 업로드의 `/assets/{id}/complete`와 같은 검사·정규화 job을 시작한다. 기능별 크기·길이·형식 제한이 모두 그대로 적용된다.
7. **config 연결**: 대상 프로젝트의 config에 asset을 연결한다. Pocket VFX는 `video_asset_id`, Camera Sketch는 scene asset이다. 업로드 직후 클라이언트가 하는 것과 같은 연결을 서버가 해 둔다.
8. **응답**: `{ project_id, asset_id, app }`를 돌려준다. 프런트는 대상 편집 화면으로 이동하고, 검사 job은 기존 방식대로 지켜본다.

### 5.3 Mock 전파
- `mock`은 원본 결과 asset이 mock 프로필로 만들어졌거나, 원본 자체가 넘겨받은 Mock 입력에서 출발했으면 true다. 즉 체인 상류에 Mock이 하나라도 있으면 true다.
- 대상 프로젝트의 결과는 자기 프로필이 mock이거나 입력의 handoff `mock`이 true이면 Mock으로 표시한다. 결과 화면, 레시피, 공유 페이지(share meta의 mock) 모두 해당한다.
- 이 규칙에는 서버 테스트가 필요하다. 예: mock 결과를 넘긴 뒤 open_weights로 렌더해도 share meta의 mock이 true여야 한다.

### 5.4 레시피와 수명
- **레시피**: 대상 결과의 recipe에 입력 출처(`handoff` 블록)를 포함한다. 그래야 체인을 재현할 수 있다.
- **삭제**: 복사본은 독립된 객체다. 원본 프로젝트를 삭제해도 대상 프로젝트는 영향을 받지 않는다. 보존 기간은 일반 업로드와 같다(docs/00 §10).

### 5.5 프로젝트 목록
`GET /api/v1/projects`를 새로 만든다(설계자 판단). 현재 세션에서 삭제되지 않은 프로젝트를 최근 순으로 돌려주며, 항목은 `{id, app_slug, title, created_at, updated_at}`이다. 사이드바의 "내 프로젝트"가 이것을 쓴다. 지금은 프로젝트 목록 endpoint가 없어서 각 프로젝트를 `/p/[id]` 주소로만 다시 열 수 있다.

## 6. 코드 구조 (설계자 판단, 세부는 구현자 몫)

- **새 앱**: `apps/studio`(패키지 `@oc/studio`)를 만든다. 세 앱의 `src`(api, components, flow, hooks, i18n, model, state, workers)를 기능별 폴더로 옮긴다. 예: `src/features/camera-sketch/...`.
- **중복 정리**: 세 앱에 중복된 셸 요소(TopBar, LanguageToggle, providers, errors 헬퍼)는 셸 하나로 합친다. 기능 고유 로직은 옮기기만 하고 고치지 않는다.
- **테스트 이동**: 세 앱의 vitest 테스트를 함께 옮겨 모두 통과시킨다. 특히 `parity.test.ts`(front/server 좌표 일치)는 반드시 유지한다.
- **번들 분리**: Three.js와 Konva처럼 무거운 의존은 해당 기능 경로에서만 불러온다. Camera Sketch의 depth-prep Web Worker chunk도 그대로 동작해야 한다.
- **Next 설정**: 기존 설정(`canvas: false` alias, `/api` rewrite, 보안 헤더, `transpilePackages`)을 하나로 합친다.
- **알려진 함정**: Next 15 App Router가 React 19.2 canary를 번들하므로 react-konva는 19.2.x를 유지해야 한다. rewrite는 build 시점에 결정되므로 API_ORIGIN은 build arg로 넘긴다.
- **기존 앱 제거**: 옮기기가 끝나고 테스트가 통과하면 `apps/product-lock`, `apps/camera-sketch`, `apps/pocket-vfx`를 지운다. 기존 포트별 주소로 오는 요청에 대한 리다이렉트는 두지 않는다. 외부 사용자가 없기 때문이다.
- **인프라**
  - `infra/docker-compose.yml`은 `web` 하나(:3000, `APP=studio`)로 줄인다.
  - `infra/Dockerfile.web`의 기본 APP을 studio로 바꾼다.
  - MinIO CORS origin은 `http://localhost:3000`만 남긴다.
  - `.env.example`도 맞춘다.
- **CI**: `.github/workflows/ci.yml`의 web job은 studio 하나만 build한다.
- **`scripts/export_app.sh`(앱별 별도 출품용)**: 출품이 하나가 되었으므로 지운다.
- **README**: 통합 앱 기준으로 다시 쓴다. 지금은 제목이 "Product Lock"이고 세 앱 설명을 이어붙인 구조다. 앱 이름은 임시로 "Open Creative Studio"로 하고, 바꾸기 쉽도록 한 곳(셸 i18n)에만 둔다.

### 5.6 리뷰에서 나온 보완 (설계자 판단)
- **share meta에 `app_slug` 추가**: 공유 페이지 `/s/[token]`이 어떤 기능의 뷰를 그릴지 알 수 있도록, `GET /public/shares/{token}/meta`에 `app_slug`를 넣는다.
- **`cover` role 구분**: Product Lock과 Pocket VFX 모두 `cover` role을 쓴다. allowlist는 role만이 아니라 **(원본 프로젝트의 `app_slug`, role)** 쌍으로 판정한다. 따라서 Pocket VFX의 `cover`는 넘길 수 없다.
- **이미지 검사의 key 변경**: 이미지 검사는 `object_key`를 정규화된 PNG로 바꾸고 원본 정보를 meta에 남긴다. 넘겨받은 이미지도 일반 업로드와 같은 경로를 타므로 이 동작을 그대로 따른다. 복사본의 key 이름은 일반 업로드와 같은 규칙을 쓴다.
- **Mock 판정 위치**: Mock 판정은 지금 각 기능의 ResultView에서 따로 한다. handoff `mock` 확인은 서버(share meta, recipe)와 프런트(결과 화면) 양쪽에 넣는다.

## 7. 완료 기준

- `uv run pytest -q`, `pnpm -r typecheck`, `pnpm -r test`, `pnpm --filter @oc/studio build`가 모두 통과한다. 새 서버 기능에는 테스트를 붙인다: handoff allowlist, 소유권, idempotency, Mock 전파, 복사 바이트 동일성, 프로젝트 목록 세션 격리.
- `docker compose up`(local 저장소 기본과 `--profile s3` 둘 다)에서 Playwright로 다음을 Mock으로 끝까지 실행한다.
  - 세 기능 각각의 기존 walk-through. 기준은 docs/camera-sketch-verification.md와 docs/pocket-vfx-verification.md의 "Full stack walk-through" 단계다. Product Lock은 pocket-vfx-verification.md 끝에 적힌 같은 단계를 따른다: 업로드 → 투명도 마스크 또는 Detect product → 배경 → Render ad → MP4·Cover PNG·Recipe ZIP 다운로드 → 공유 링크와 Mock 배지 확인.
  - 4.3의 데모 체인. 마지막 결과와 그 공유 페이지에 Mock이 보여야 한다.
  - 사이드바 이동, "내 프로젝트"에서 넘겨받은 프로젝트 다시 열기, 휴대폰 폭에서 드로어 열기.
- 콘솔 에러와 실패한 네트워크 요청이 없어야 한다.
- 검증 기록을 docs에 남긴다.

## 8. 범위 밖: 공개 호스팅

이 작업은 로컬 `docker compose` 기준으로 검증한다. 심사용 공개 URL 배포는 별도 작업으로 다룬다. 다만 나중에 막히지 않도록 다음 값은 하드코딩하지 않고 env로 설정할 수 있게 둔다: 웹 origin, MinIO CORS origin(`MINIO_API_CORS_ALLOW_ORIGIN`), presign용 공개 endpoint(`OC_S3_PUBLIC_ENDPOINT_URL`). `.env.example`의 기본값은 `http://localhost:3000`이다.

## 9. 구현자에게 맡기는 것

- 사이드바와 셸의 시각적 세부(아이콘, 간격, 접힘 애니메이션), 내 프로젝트 목록의 개수 제한이나 페이지 나누기.
- 파일 이동 순서와 폴더 이름, 공통 셸 컴포넌트를 `packages/ui`에 둘지 앱 안에 둘지.
- 넘기기 버튼과 출처 배지의 문구(영어와 한국어).
- handoff 복사를 API 요청 안에서 할지 worker job으로 할지. 단, 파일이 커서 API 요청이 오래 걸리면 job으로 뺀다.
- 기존 검증 문서의 정리 방식.
