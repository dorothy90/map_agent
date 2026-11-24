"""Build a source-only handoff from the pinned Git tree, or verify it offline (Python 3.11+)."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

COMMIT = "a0ca3ffd126238dedd807f84838f733f8d4a2aee"
Y = "08-YieldAgent/"
H = Y + "harness/"
ENGINE = H + "engine/"
UI = Y + "harness_console/"
FIELDS = ("path", "destination", "class", "role", "sha256", "mode", "bytes")
ROOT_FILES = {
    ".gitignore": ("docs", "두 저장소의 제외 규칙 병합용. 런타임과 자격증명 제외 및 원본 dist 예외 확인"),
    ".env.example": ("docs", "환경 변수 이름 참고용 템플릿. 실제 값과 공급자 주소는 사내에서 설정"),
    "AGENTS.md": ("docs", "기여자용 규칙. 사내 AGENTS.md와 비교"),
    ".python-version": ("required", "uv의 Python 계열 선택"),
    "pyproject.toml": ("required", "백엔드 Python 의존성과 개발 의존성"),
    "uv.lock": ("required", "uv로 재현할 정확한 패키지 버전·해시"),
    "requirements.txt": ("optional", "기존 pip 운영환경에서 비교하는 의존성 목록"),
    "scripts/setup_hermes.sh": ("test", "Git에 포함된 Hermes 엔진 import 검사"),
}
SHARED = {
    "common.py": ("required", "Oracle 연결·환경 변수·SQL 조회 공통 코드"),
    "bin_mapping.py": ("required", "좌/우 BIN 심볼과 PASS 해석"),
    "yield_viz.py": ("required", "수율표의 HTML 생성 및 지표 정의"),
    "yield_table_renderer.py": ("required", "수율표 행·열·표시 서식 생성"),
    "wafer_zones.py": ("required", "yield_viz가 사용하는 웨이퍼 좌표 영역"),
    "wiki_queue.py": ("optional", "기존 위키 메모리 연동의 작업 큐"),
    "wiki_store.py": ("optional", "위키 연동 시 파일 저장 및 조회"),
    "wiki_summarizer.py": ("optional", "위키 연동 시 요약 생성"),
    "fail_history_tools.py": ("optional", "위키 저장소 및 부가 평가의 불량이력 검색 의존성"),
    "lf_utils.py": ("optional", "위키 요약 경로의 추적 연동"),
    "local_trace.py": ("optional", "위키 추적 경로의 로컬 기록"),
    "templates/fail_history_report.html": ("optional", "불량이력 도구가 파일명으로 읽는 Jinja 템플릿"),
    "seed_symbol_bin.py": ("test", "BIN 매핑 테스트가 가져오는 구성 함수. 운영 DB 적재용으로 실행하지 않음"),
    "models.py": ("test", "기존 앱·하네스 간 메시지 상태 호환성 회귀검사"),
    "symbol_bin.py": ("test", "심볼 BIN 정규화·선택 테스트 대상"),
    "symbol_bin_db.py": ("test", "심볼 BIN 메타데이터 조회 테스트 대상"),
    "sql/DF_ALL_PARA_PRB.sql": ("docs", "파라미터 메타데이터 테이블 참조 DDL"),
    "sql/DF_SYMBOL_BIN.sql": ("docs", "BIN 심볼 정의 테이블 참조 DDL"),
    "sql/DF_BMW_PNG.sql": ("docs", "기존 이미지 테이블 참조 DDL"),
    "AGENTS.md": ("docs", "하네스 작업 규칙. 사내 에이전트 지침과 비교"),
}
APP_MODULES = {
    "app": "FastAPI 기동·종료와 라우터 연결", "config": "모델·DB·실행 제한·격리 설정",
    "router": "제품·스킬·결과·산출물 HTTP API", "control": "실행 접수·취소·복구",
    "hermes_chat": "현재 웹 채팅 실행과 프로필 생성", "hermes_chat_worker": "Hermes 모델·도구·스트림을 한 채팅에 연결",
    "shared_file_tools": "공용 수율 폴더로 파일 읽기·검색·이미지 입력 제한",
    "web_delegation": "위임 작업의 도구 권한·프로필·실행 수명 연결",
    "native_tools": "수율 도구 6개를 Hermes의 yield 도구 묶음에 등록", "executor": "인증된 도구 실행과 결과 저장",
    "instructions": "모델에 제공할 지침 파일 로드", "store": "Mongo 세션·실행·테이블·산출물 저장",
    "skill_catalog_worker": "현재 사용자 프로필의 스킬 검색·목록",
    "types": "저장 결과·도구 입력·출력 계약", "native_hermes": "Hermes 프로필 및 공통 상태",
    "chat_tool_helpers": "웹 채팅에서 쓰는 Hermes 도구 연결 함수", "native_ipc": "프로세스 간 실행 메시지",
    "native_budget": "실행 시간·모델 호출량 제한",
    "native_verification": "답변 근거·맵 및 산출물 계약 검증", "context": "실행 시점의 사용자 문맥",
    "completion": "종료 상태 및 근거 평가", "artifact_delivery": "저장된 산출물의 답변 연결",
    "memory": "기존 메모리 연동", "model": "모델 클라이언트 구성", "mcp_server": "별도 선택형 MCP 진입점",
    "__init__": "하네스 Python 패키지",
}
TOOLS = {
    "registry":"도구 공개 스키마·실행 명세 묶음", "list_tables":"Oracle 테이블·컬럼 정보",
    "query_db":"조회 전용 SQL과 원본 행/스키마 보존", "python_tools":"read_result·run_python 결과 읽기와 분석",
    "table_artifact":"render_yield_table 수율표 및 결과 링크",
    "load_wafer_maps":"load_wafer_maps 개별 맵·누적 맵 조회",
    "map_aggregate":"원본 MAP에서 그룹별 좌표 집계", "map_aggregate_plot":"누적 맵 Plotly 시각화",
    "map_bin_plot":"기억한 그룹별 개별 측정의 좌표·BIN 표시", "report_analysis":"수율일보 요청 계약",
    "report_scatter":"웨이퍼 단위 열화·개선 산점도", "report_bin_maps":"주차별 BIN 누적·델타맵",
    "report_policy":"수율표 범위·근거·재시도 정책", "yield_report_data":"수율표 DB 조회와 집계",
    "yield_report_scope":"재사용 가능한 결과 범위·멤버십 검증", "wafer_group_report":"웨이퍼별 보고서 렌더링",
    "__init__":"네이티브 도구 Python 패키지",
}
WEB = {"web":"사내 웹 호스트용 채팅·사이드바·제품 선택·맵 화면", "platform":"웹 호스트 및 게이트웨이 호환 어댑터",
       "protocol":"Hermes/호스트 간 데이터·RPC 계약", "hermes":"Hermes에서 이식한 채팅 UI와 앱 상태",
       "yield":"수율 산출물 화면 계약"}
ENGINE_AREAS = {"agent":"모델 호출·시스템 문맥·턴 처리", "tools":"원본 도구 레지스트리·스킬·메모리",
                "hermes_cli":"CLI/구성·프로필", "plugins":"배포 플러그인과 리소스", "gateway":"원본 게이트웨이·채널",
                "tui_gateway":"터미널 게이트웨이", "pm":"패키지 기능", "cron":"예약 실행", "acp_adapter":"ACP 어댑터"}


def describe(name):
    """One row per tracked file; upstream integrity requires its entire distribution."""
    if name in ROOT_FILES:
        return ROOT_FILES[name]
    if name.startswith(ENGINE):
        rel = name[len(ENGINE):]
        area = ENGINE_AREAS.get(rel.split("/")[0], "원본 런타임·배포 구성")
        return "required", f"Hermes 고정 배포본 · {area} · {rel}"
    if name.startswith(H):
        rel = name[len(H):]
        group, _, path = rel.partition("/")
        stem = PurePosixPath(rel).stem
        if group == "tools":
            return "required", TOOLS[stem]
        if group == "tests":
            return "test", f"백엔드 회귀·계약 테스트 및 fixture · {path}"
        if group == "evals":
            return "test", f"수율 하네스 평가 · {path}"
        if group == "engine_sync":
            if path.endswith(".py") or path.startswith("scope_"):
                return "optional", f"핀 비교·동기화 유틸리티 · {path}"
            return None
        areas = {"gateway":"HTTP·WebSocket 세션, 사용자 인증 및 권한", "dev":"개발 전용 루프백 게이트웨이",
                 "isolation":"분석 코드 격리 실행 및 OS별 설치", "instructions":"Hermes 모델에 실제 전달되는 지침",
                 "runtime":"도메인 조회와 분석 프로세스"}
        if group in areas:
            category = "docs" if rel.endswith(".md") and group != "instructions" else "required"
            return category, f"{areas[group]} · {path}"
        if rel == "README.md":
            return "docs", "개발 서버 시작 및 구성 설명"
        if not path and rel.endswith(".py"):
            return ("optional" if stem == "mcp_server" else "required"), APP_MODULES[stem]
        return None
    if name.startswith(UI):
        rel = name[len(UI):]
        parts = rel.split("/")
        if parts[0] == "src":
            area = WEB.get(parts[1], "React 앱 진입점")
            test = ".test." in parts[-1] or "test" in parts or "fixtures" in parts or "vitest" in parts[-1]
            return ("test" if test else "required"), f"{area} · {'/'.join(parts[2:]) or parts[-1]}"
        if parts[0] in {"tests-integration", "e2e-web"} or rel.startswith("vitest.") or rel in {
                "pr9-excluded-tests.json", "pr9-excluded-cases.json", "pr9-ui-tests.json"}:
            return "test", f"브라우저/게이트웨이 회귀 검증·테스트 환경 · {rel}"
        if rel in {"package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "index.html"}:
            return "required", f"프론트 설치·TypeScript 빌드·웹 로딩 · {rel}"
        if rel in {"README.md", ".gitignore"}:
            return "docs", f"콘솔 개발·검증 참고 · {rel}"
        return None
    if name.startswith(Y):
        return SHARED.get(name[len(Y):])
    return None


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def safe_path(name):
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or ".." in path.parts or
            "\\" in name or ":" in name or path.as_posix() != name or
            any(ord(c) < 32 for c in name)):
        raise ValueError(f"Unsafe path: {name!r}")
    return path


def build(repo, archive, inventory):
    tree = git(repo, "ls-tree", "-rz", COMMIT)
    engine_pin = json.loads(git(repo, "show", f"{COMMIT}:{ENGINE}UPSTREAM.json"))["files"]
    engine_hashes = {}
    rows = []
    archive.parent.mkdir(parents=True, exist_ok=True)
    inventory.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("wb") as output, gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as tar, \
            subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as blobs:
        for record in tree.rstrip(b"\0").split(b"\0"):
            metadata, raw_name = record.split(b"\t", 1)
            name = raw_name.decode()
            role = describe(name)
            if not role:
                continue
            safe_path(name)
            mode, kind, oid = metadata.split()
            if kind != b"blob" or mode not in {b"100644", b"100755"}:
                raise ValueError(f"Unexpected Git object: {name}")
            blobs.stdin.write(oid + b"\n")
            blobs.stdin.flush()
            header = blobs.stdout.readline().split()
            if header[:2] != [oid, b"blob"]:
                raise ValueError(f"Missing Git blob: {name}")
            data = blobs.stdout.read(int(header[2]))
            if blobs.stdout.read(1) != b"\n" or len(data) != int(header[2]):
                raise ValueError(f"Truncated Git blob: {name}")
            digest = hashlib.sha256(data).hexdigest()
            if name.startswith(ENGINE) and name[len(ENGINE):] not in {"UPSTREAM.json", "PATCHES.diff"}:
                engine_hashes[name[len(ENGINE):]] = digest
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = int(mode[-3:], 8)
            tar.addfile(info, io.BytesIO(data))
            rows.append((name, name, *role, digest, mode.decode(), str(len(data))))
        blobs.stdin.close()
        if blobs.wait() != 0:
            raise ValueError("Git object reader failed")
    if engine_hashes != engine_pin:
        raise ValueError("Vendored Hermes files differ from the pinned upstream manifest")
    if not ROOT_FILES.keys() <= {r[0] for r in rows} or not {Y + s for s in SHARED} <= {r[0] for r in rows}:
        raise ValueError("An explicitly selected dependency is missing")
    with inventory.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out, delimiter="\t", lineterminator="\n")
        writer.writerow(FIELDS)
        writer.writerows(rows)
    verify(archive, inventory)


def read_inventory(inventory):
    with inventory.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError("Unexpected inventory columns")
        records = {}
        for r in reader:
            name = r["path"]
            safe_path(name)
            if (name != r["destination"] or name in records or r["mode"] not in {"100644", "100755"}
                    or len(r["sha256"]) != 64 or not r["role"] or r["class"] not in {"required", "optional", "test", "docs"}):
                raise ValueError(f"Invalid inventory row: {name}")
            records[name] = r
        if not records:
            raise ValueError("Empty inventory")
    return records


def check_stream(name, row, fileobj, mode, size):
    if size != int(row["bytes"]) or mode != int(row["mode"][-3:], 8):
        raise ValueError(f"File mode or length mismatch: {name}")
    if hashlib.file_digest(fileobj, "sha256").hexdigest() != row["sha256"]:
        raise ValueError(f"SHA-256 mismatch: {name}")


def verify(archive, inventory):
    records = read_inventory(inventory)
    seen = set()
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            name = member.name
            if name not in records or name in seen or not member.isfile() or member.issparse():
                raise ValueError(f"Unexpected archive member: {name}")
            with tar.extractfile(member) as f:
                check_stream(name, records[name], f, member.mode, member.size)
            seen.add(name)
    if records.keys() != seen:
        raise ValueError(f"Archive missing entries: {sorted(records.keys() - seen)[:5]}")
    print(f"archive OK: {len(seen)} regular files")


def verify_tree(root, inventory):
    records = read_inventory(inventory)
    root = root.resolve(strict=True)
    for name, row in records.items():
        path = root / name
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError(f"Expected regular file: {name}")
        st = path.stat()
        with path.open("rb") as f:
            git_mode = 0o755 if st.st_mode & stat.S_IXUSR else 0o644
            check_stream(name, row, f, git_mode, st.st_size)
    print(f"tree OK: {len(records)} listed files (other paths are not checked)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("build", "verify", "verify-tree"):
        cmd = commands.add_parser(action)
        cmd.add_argument("--inventory", type=Path, required=True)
        if action in ("build", "verify"):
            cmd.add_argument("--archive", type=Path, required=True)
        if action == "build":
            cmd.add_argument("--repo", type=Path, required=True)
        if action == "verify-tree":
            cmd.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "build":
        build(args.repo, args.archive, args.inventory)
    elif args.action == "verify":
        verify(args.archive, args.inventory)
    else:
        verify_tree(args.root, args.inventory)


if __name__ == "__main__":
    main()
