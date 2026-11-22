# 사내 레포에 반영한 파일 검수

01에서 만든 `REF`는 고정 원본, `DEST`는 사내 브랜치, `AUDIT/plan.tsv`는 사내에서 정한 목적지와 처리 방식이다. 다음 순서로 파일 이식을 마무리한다.

1. **참조본 무결성.** `python3 "$KIT/build_kit.py" verify-tree --root "$REF" --inventory "$KIT/FILES.tsv"`가 성공하는지 확인한다. 아래 사내 작업은 이 참조본을 기준으로 한다.
2. **ADD와 KEEP.** 각 행의 원본 경로와 사내 목적지를 대조한다. 새 파일과 그대로 쓰기로 한 파일은 FILES.tsv의 SHA-256과 Git 실행 비트가 일치해야 한다. 아래 검사는 재배치한 파일도 destination 열로 찾는다. 키 이름을 유지한 UTF-8 TSV를 사용한다.
3. **MERGE.** 양쪽 내용이 달라야 하는 파일은 양쪽 경로, 사내에서 보존한 기능, 가져온 함수나 계약, 검토 근거를 기록한다. 각 행을 실제 diff에서 확인한다. 특히 공용 DB 연결, 사용자 인증, 엔진 로딩, 도구 등록, 웹 진입점을 확인한다. 수동 병합한 파일은 참조본과 해시가 다를 수 있다.
4. **HOLD.** 보류 이유·대체 파일·담당자를 사내 기록에 남긴다. 필수 파일이 보류 중이면 해당 기능을 미완료로 표시한다. 기존 앱 체크포인트에서 삭제된 여섯 경로는 CHECKPOINT-144.tsv에 이력으로 남아 있다. 사내 다른 코드가 참조하는 경로를 자동 삭제하지 않는다.
5. **변경 범위.** 아래 Git 명령으로 추적 중인 변경, 새 파일, 스테이징된 변경을 함께 본다. 정상적으로 추가할 파일이 표시되지 않으면 `git check-ignore -v -- 상대경로`로 사내 무시 규칙을 확인한다. 필요한 경로만 선별해 스테이징한 뒤 검토한다. 특히 기존 프론트의 잠금 파일과 실행 스크립트의 모드를 확인한다.

```bash
python3 - "$KIT" "$DEST" "$AUDIT/plan.tsv" <<'PY'
import sys
from pathlib import Path
kit, dest, sheet = map(Path, sys.argv[1:])
sys.path.insert(0, str(kit))
from port_plan import table, location, identical
rows = {r['path']: r for r in table(kit / 'FILES.tsv')}
decisions = table(sheet)
assert len(rows) == len(decisions) and set(rows) == {d['path'] for d in decisions}
checked = 0
for d in decisions:
    if d['action'] in ('ADD', 'KEEP'):
        if not identical(location(dest, d['destination']), rows[d['path']]):
            sys.exit('확인 필요 ' + d['destination'])
        checked += 1
print(f'원본과 일치하는 ADD/KEEP 파일 {checked}개. MERGE/HOLD는 대응표에서 별도로 검토하세요.')
PY
git -C "$DEST" status --short --untracked-files=all
git -C "$DEST" diff --stat
git -C "$DEST" diff --cached --stat
git -C "$DEST" diff --cached --check
```

**완료 기록**에는 외부 기준 커밋, 사내 시작·종료 커밋, ADD/KEEP/MERGE/HOLD 수량, 실제 검수 결과, 미결 항목을 남긴다. FILES.tsv의 4,853개 경로 모두에 결정이 있고 필수 기능에 미결이 없는지 확인한다. 빈 사내 레포와 이미 코드가 있는 사내 레포는 ADD 수가 다르다.

**이미 사내 환경에서 기존 프로그램을 실행할 수 있다면** 새 채팅에서 수율일보를 요청하고 사내 DB 제품 하나를 선택한다. 월 3·주 4·일 3개 기간, 열화·개선 각 3개 웨이퍼 산점도, 검사별 직전 주·최근 주·델타 BIN 맵을 동일한 DB 조건으로 대조한다. 스킬을 옮겼다면 새 채팅의 스킬 목록도 확인한다. 실행 환경이 준비되지 않은 경우 파일 검수와 기능 검증의 완료 여부를 각각 기록한다.
