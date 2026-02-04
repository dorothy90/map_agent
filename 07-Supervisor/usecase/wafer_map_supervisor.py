# ============================================================
# 1. 환경 설정
# ============================================================
from dotenv import load_dotenv

load_dotenv(override=True)

import os
import json
from typing import List, Optional
from langchain_core.messages import convert_to_messages


# ============================================================
# 2. 헬퍼 함수
# ============================================================
def pretty_print_message(message, indent=False):
    """개별 메시지를 포맷팅하여 출력합니다."""
    pretty_message = message.pretty_repr(html=True)
    if not indent:
        print(pretty_message)
        return

    indented = "\n".join("\t" + c for c in pretty_message.split("\n"))
    print(indented)


def pretty_print_messages(update, last_message=False):
    """그래프 실행 결과의 메시지 업데이트를 포맷팅하여 출력합니다."""
    is_subgraph = False

    if isinstance(update, tuple):
        ns, update = update
        if len(ns) == 0:
            return

        graph_id = ns[-1].split(":")[0]
        print(f"[서브그래프 {graph_id}로부터 업데이트]")
        print("\n")
        is_subgraph = True

    for node_name, node_update in update.items():
        if node_update is None or "messages" not in node_update:
            continue
        update_label = f"[노드 {node_name}로부터 업데이트]"
        if is_subgraph:
            update_label = "\t" + update_label

        print(update_label)
        print("\n")

        messages = convert_to_messages(node_update["messages"])
        if last_message:
            messages = messages[-1:]

        for m in messages:
            pretty_print_message(m, indent=is_subgraph)
        print("\n")


# ============================================================
# 3. Oracle DB 연결 설정
# ============================================================
ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
ORACLE_TABLE = os.getenv("ORACLE_TABLE", "LANGGRAPH_DATA")


# ============================================================
# 4. 내부 함수들 (LLM 컨텍스트를 거치지 않음)
# ============================================================
import oracledb
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import platform
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import multiprocessing as mp

# orjson 사용 (없으면 기본 json fallback)
try:
    import orjson

    def fast_json_loads(s):
        return orjson.loads(s)

except ImportError:

    def fast_json_loads(s):
        return json.loads(s)


# 폰트 설정
current_os = platform.system()
if current_os == "Windows":
    font_path = "C:/Windows/Fonts/malgun.ttf"
    fontprop = fm.FontProperties(fname=font_path, size=12)
    plt.rc("font", family=fontprop.get_name())
elif current_os == "Darwin":  # macOS
    plt.rcParams["font.family"] = "AppleGothic"
else:
    try:
        plt.rcParams["font.family"] = "NanumGothic"
    except:
        print("한글 폰트를 찾을 수 없습니다.")

plt.rcParams["axes.unicode_minus"] = False


def _query_wafer_data(
    lot_id: Optional[str] = None,
    lot_ids: Optional[str] = None,
    wf_ids: Optional[str] = None,
    groupkey: Optional[str] = None,
) -> list:
    """Oracle DB에서 wafer 데이터 조회 (내부 함수)

    조회 방식:
    1. lot_ids: 여러 lot의 모든 wafer 조회 (lot 기준)
    2. groupkey: lot_id.wf_id 형식으로 특정 wafer 조회 (wafer 기준)
    3. lot_id + wf_ids: 단일 lot의 특정 wafer 조회 (wafer 기준)
    """
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)

    try:
        cur = conn.cursor()
        results = []

        # 방식 1: lot_ids 사용 (여러 lot의 모든 wafer 조회)
        if lot_ids:
            lot_list = [x.strip() for x in lot_ids.split(",")]
            placeholders = ",".join([f":lot{i}" for i in range(len(lot_list))])
            sql = f"""
                SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
                FROM {ORACLE_TABLE}
                WHERE lot_id IN ({placeholders})
                ORDER BY lot_id, wf_id
            """
            params = {f"lot{i}": lot for i, lot in enumerate(lot_list)}
            cur.execute(sql, params)

            # 예시
            # lot_list = ["4SS4UR9", "4SSYY23", "4SS8JDL"]
            # placeholders = ":lot0,:lot1,:lot2"
            # sql = "WHERE lot_id IN (:lot0,:lot1,:lot2)"
            # params = {"lot0": "4SS4UR9", "lot1": "4SSYY23", "lot2": "4SS8JDL"}

            columns = [desc[0].lower() for desc in cur.description]
            rows = cur.fetchall()

            for row in rows:
                record = dict(zip(columns, row))
                if record.get("map_val_json"):
                    if hasattr(record["map_val_json"], "read"):
                        record["map_val_json"] = record["map_val_json"].read()
                results.append(record)

        # 방식 2: groupkey 사용 (lot_id.wf_id 형식)
        elif groupkey:
            specs = [s.strip() for s in groupkey.split(",")]
            for spec in specs:
                if "." in spec:
                    parts = spec.rsplit(".", 1)
                    spec_lot_id = parts[0]
                    spec_wf_id = int(parts[1])

                    sql = f"""
                        SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
                        FROM {ORACLE_TABLE}
                        WHERE lot_id = :lot_id AND wf_id = :wf_id
                    """
                    cur.execute(sql, {"lot_id": spec_lot_id, "wf_id": spec_wf_id})
                else:
                    sql = f"""
                        SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
                        FROM {ORACLE_TABLE}
                        WHERE lot_id = :lot_id
                        ORDER BY wf_id
                    """
                    cur.execute(sql, {"lot_id": spec})

                columns = [desc[0].lower() for desc in cur.description]
                rows = cur.fetchall()

                for row in rows:
                    record = dict(zip(columns, row))
                    if record.get("map_val_json"):
                        if hasattr(record["map_val_json"], "read"):
                            record["map_val_json"] = record["map_val_json"].read()
                    results.append(record)

        # 방식 1: lot_id + wf_ids 사용
        elif lot_id:
            wf_id_list = None
            if wf_ids:
                wf_id_list = [int(x.strip()) for x in wf_ids.split(",")]

            if wf_id_list is None or len(wf_id_list) == 0:
                sql = f"""
                    SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
                    FROM {ORACLE_TABLE}
                    WHERE lot_id = :lot_id
                    ORDER BY wf_id
                """
                cur.execute(sql, {"lot_id": lot_id})
            else:
                placeholders = ",".join([f":wf{i}" for i in range(len(wf_id_list))])
                sql = f"""
                    SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
                    FROM {ORACLE_TABLE}
                    WHERE lot_id = :lot_id AND wf_id IN ({placeholders})
                    ORDER BY wf_id
                """
                params = {"lot_id": lot_id}
                for i, wf_id in enumerate(wf_id_list):
                    params[f"wf{i}"] = wf_id
                cur.execute(sql, params)

            columns = [desc[0].lower() for desc in cur.description]
            rows = cur.fetchall()

            for row in rows:
                record = dict(zip(columns, row))
                if record.get("map_val_json"):
                    if hasattr(record["map_val_json"], "read"):
                        record["map_val_json"] = record["map_val_json"].read()
                results.append(record)

        return results
    finally:
        cur.close()
        conn.close()


def _parse_map_json(map_val_json: str) -> dict:
    """map_val_json 문자열을 파싱하여 딕셔너리로 반환

    형식: {"MAP": ["x,y,pt1h_bin,pt2c_bin", ...]}
    반환: {"x_y": {"pt1h_bin": "A", "pt2c_bin": "B"}, ...}
    """
    if isinstance(map_val_json, str):
        raw_data = fast_json_loads(map_val_json)
    else:
        raw_data = map_val_json

    result = {}
    for item in raw_data["MAP"]:
        parts = item.split(",")
        x, y = parts[0], parts[1]
        pt1h_bin = parts[2] if len(parts) > 2 else ""
        pt2c_bin = parts[3] if len(parts) > 3 else ""
        result[f"{x}_{y}"] = {"pt1h_bin": pt1h_bin, "pt2c_bin": pt2c_bin}

    return result


def _parse_wafer_for_cummap(args):
    """단일 wafer 파싱 (cummap 병렬 처리용)

    Returns:
        tuple: (rows, cols, passes) - 각각 list
    """
    map_val_json, bin_type = args
    if isinstance(map_val_json, str):
        raw_data = fast_json_loads(map_val_json)
    else:
        raw_data = map_val_json

    rows, cols, passes = [], [], []
    bin_idx = 2 if bin_type == "pt1h_bin" else 3

    for item in raw_data["MAP"]:
        parts = item.split(",")
        rows.append(int(parts[0]))
        cols.append(int(parts[1]))
        bin_val = parts[bin_idx] if len(parts) > bin_idx else ""
        passes.append(1 if bin_val == "A" else 0)

    return rows, cols, passes


def _parse_wafer_for_binmap(args):
    """단일 wafer 파싱 (binmap 병렬 처리용)

    Returns:
        dict: {"x_y": {"pt1h_bin": "A", "pt2c_bin": "B"}, ...}
    """
    map_val_json = args
    if isinstance(map_val_json, str):
        raw_data = fast_json_loads(map_val_json)
    else:
        raw_data = map_val_json

    result = {}
    for item in raw_data["MAP"]:
        parts = item.split(",")
        x, y = parts[0], parts[1]
        pt1h_bin = parts[2] if len(parts) > 2 else ""
        pt2c_bin = parts[3] if len(parts) > 3 else ""
        result[f"{x}_{y}"] = {"pt1h_bin": pt1h_bin, "pt2c_bin": pt2c_bin}

    return result


def _get_map_bounds(map_data_list: list) -> tuple:
    """가장 많은 좌표를 가진 wafer에서 경계값 계산
    (동일 제품의 wafer는 같은 die layout을 가지므로 하나만 파싱)
    """
    if not map_data_list:
        return 0, 0, 0, 0

    # 문자열 길이가 가장 긴 wafer 선택 (좌표가 가장 많을 가능성 높음)
    longest_data = max(map_data_list, key=lambda d: len(d["map_val_json"]))
    map_json = _parse_map_json(longest_data["map_val_json"])

    rows, cols = [], []
    for key in map_json.keys():
        row, col = map(int, key.split("_"))
        rows.append(row)
        cols.append(col)

    return min(rows), max(rows), min(cols), max(cols)


def _visualize_binmap(map_data_list: list, bin_type: str = "pt1h_bin") -> str:
    """여러 wafer의 binmap을 개별적으로 시각화 (병렬 처리 버전)

    Args:
        map_data_list: wafer 데이터 리스트
        bin_type: "pt1h_bin" 또는 "pt2c_bin"
    """
    if not map_data_list:
        return "데이터가 없습니다."

    n_wafers = len(map_data_list)
    n_cols = min(4, n_wafers)
    n_rows = (n_wafers + n_cols - 1) // n_cols

    min_row, max_row, min_col, max_col = _get_map_bounds(map_data_list)
    height = max_row - min_row + 1
    width = max_col - min_col + 1

    # 병렬 파싱 (ThreadPoolExecutor 사용 - I/O bound 작업에 적합)
    n_workers = min(mp.cpu_count(), len(map_data_list), 8)
    args_list = [d["map_val_json"] for d in map_data_list]

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        parsed_maps = list(executor.map(_parse_wafer_for_binmap, args_list))

    # 모든 bin 값 수집 (이미 파싱된 결과 사용)
    all_values = set()
    for map_json in parsed_maps:
        for value in map_json.values():
            bin_val = value.get(bin_type, "")
            if bin_val:
                all_values.add(bin_val)
    value_to_num = {v: i for i, v in enumerate(sorted(all_values))}

    fig, axes = plt.subplots(n_rows, n_cols, figsize=(4 * n_cols, 4 * n_rows))
    if n_wafers == 1:
        axes = np.array([[axes]])
    elif n_rows == 1:
        axes = axes.reshape(1, -1)
    elif n_cols == 1:
        axes = axes.reshape(-1, 1)

    # 고유 lot_id 수집
    unique_lots = list(set(d["lot_id"] for d in map_data_list))

    # 전체 제목: 단일 lot vs 다중 lot
    if len(unique_lots) == 1:
        title = f"Binmap ({bin_type}) - Lot: {unique_lots[0]} ({n_wafers} wafers)"
    else:
        title = f"Binmap ({bin_type}) - {len(unique_lots)} Lots ({n_wafers} wafers)"
    fig.suptitle(title, fontsize=14)

    for idx, (data, map_json) in enumerate(zip(map_data_list, parsed_maps)):
        row_idx = idx // n_cols
        col_idx = idx % n_cols
        ax = axes[row_idx, col_idx]

        map_array = np.full((height, width), np.nan)

        for key, value in map_json.items():
            r, c = map(int, key.split("_"))
            bin_val = value.get(bin_type, "")
            if bin_val and bin_val in value_to_num:
                map_array[r - min_row, c - min_col] = value_to_num[bin_val]

        im = ax.imshow(map_array, cmap="tab20", interpolation="nearest")
        # 개별 subplot 제목: lot_id.wf_id 형식
        ax.set_title(f"{data['lot_id']}.{data['wf_id']}")
        ax.set_xlabel("Col")
        ax.set_ylabel("Row")

    for idx in range(n_wafers, n_rows * n_cols):
        row_idx = idx // n_cols
        col_idx = idx % n_cols
        axes[row_idx, col_idx].axis("off")

    plt.tight_layout()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 파일명: 단일 lot이면 lot_id, 다중이면 "multi"
    file_lot = unique_lots[0] if len(unique_lots) == 1 else "multi"
    filepath = f"binmap_{file_lot}_{timestamp}.png"
    plt.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.show()

    return filepath


def _visualize_cummap(map_data_list: list, bin_type: str = "pt1h_bin") -> tuple:
    """여러 wafer를 Pass Rate 기반 cummap으로 시각화 (병렬 처리 + 벡터화 버전)

    Args:
        map_data_list: wafer 데이터 리스트
        bin_type: "pt1h_bin" 또는 "pt2c_bin"
    """
    if not map_data_list:
        return None, 0

    n_wafers = len(map_data_list)

    min_row, max_row, min_col, max_col = _get_map_bounds(map_data_list)
    height = max_row - min_row + 1
    width = max_col - min_col + 1

    # 병렬 파싱 (ThreadPoolExecutor 사용)
    n_workers = min(mp.cpu_count(), len(map_data_list), 8)
    args_list = [(d["map_val_json"], bin_type) for d in map_data_list]

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        results = list(executor.map(_parse_wafer_for_cummap, args_list))

    # 결과 합치기
    all_rows, all_cols, all_pass = [], [], []
    for rows, cols, passes in results:
        all_rows.extend(rows)
        all_cols.extend(cols)
        all_pass.extend(passes)

    # NumPy 배열로 변환 및 인덱스 조정
    all_rows = np.array(all_rows) - min_row
    all_cols = np.array(all_cols) - min_col
    all_pass = np.array(all_pass)

    # 벡터화된 누적 연산 (np.add.at 사용)
    pass_sum = np.zeros((height, width))
    count = np.zeros((height, width))
    np.add.at(pass_sum, (all_rows, all_cols), all_pass)
    np.add.at(count, (all_rows, all_cols), 1)

    with np.errstate(divide="ignore", invalid="ignore"):
        pass_rate = np.where(count > 0, pass_sum / count, np.nan)

    valid_rates = pass_rate[~np.isnan(pass_rate)]
    avg_pass_rate = np.mean(valid_rates) * 100 if len(valid_rates) > 0 else 0

    fig, ax = plt.subplots(figsize=(10, 8))

    # 고유 lot_id 수집
    unique_lots = list(set(d["lot_id"] for d in map_data_list))

    # 제목: 단일 lot vs 다중 lot
    if len(unique_lots) == 1:
        title = f"Cummap ({bin_type}) - Lot: {unique_lots[0]}\n({n_wafers} wafers, Avg Pass Rate: {avg_pass_rate:.1f}%)"
    else:
        title = f"Cummap ({bin_type}) - {len(unique_lots)} Lots\n({n_wafers} wafers, Avg Pass Rate: {avg_pass_rate:.1f}%)"
    ax.set_title(title, fontsize=14)

    im = ax.imshow(pass_rate, cmap="RdYlGn", vmin=0, vmax=1, interpolation="nearest")

    cbar = plt.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Pass Rate", fontsize=12)
    cbar.set_ticks([0, 0.25, 0.5, 0.75, 1.0])
    cbar.set_ticklabels(["0%\n(Fail)", "25%", "50%", "75%", "100%\n(Pass)"])

    ax.set_xlabel("Col")
    ax.set_ylabel("Row")

    plt.tight_layout()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 파일명: 단일 lot이면 lot_id, 다중이면 "multi"
    file_lot = unique_lots[0] if len(unique_lots) == 1 else "multi"
    filepath = f"cummap_{file_lot}_{timestamp}.png"
    plt.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.show()  # 메모리 해제

    return filepath, avg_pass_rate


# ============================================================
# 5. LLM이 호출하는 단일 통합 도구
# ============================================================
def show_wafer_map(
    lot_id: Optional[str] = None,
    lot_ids: Optional[str] = None,
    wf_ids: Optional[str] = None,
    groupkey: Optional[str] = None,
    map_type: str = "binmap",
    bin_type: str = "pt1h_bin",
) -> str:
    """Wafer map 시각화 도구 (DB 조회 + 시각화를 한번에 처리)

    LLM은 사용자 질문에서 파라미터만 파싱하여 전달합니다.
    실제 데이터는 LLM 컨텍스트를 거치지 않고 내부에서 직접 처리됩니다.

    Args:
        lot_id: 단일 lot ID (wf_ids와 함께 사용). 예: "4SS4UR9"
        lot_ids: 여러 lot ID (쉼표 구분, 각 lot의 모든 wafer 조회). 예: "4SSW0PC,4SSXCEW,4SSR2CD"
        wf_ids: wafer ID들 (쉼표 구분, lot_id와 함께 사용). 예: "1,2,3"
        groupkey: lot_id.wf_id 형식 (쉼표 구분). 예: "4SS4UR9.1, 4SSYY23.2"
        map_type: 시각화 유형 (쉼표로 여러 개 지정 가능)
            - "binmap": 개별 wafer map 비교
            - "cummap": 누적 Pass Rate map
            - "binmap,cummap": 둘 다 생성
            - "all": 모든 map 유형 생성
        bin_type: "pt1h_bin" (기본값) 또는 "pt2c_bin" - 시각화할 bin 종류

    Returns:
        str: 결과 메시지 (이미지 파일 경로 포함)

    Examples:
        # Lot 기준 조회 (모든 wafer)
        show_wafer_map(lot_ids="4SSW0PC,4SSXCEW,4SSR2CD", map_type="cummap")

        # Wafer 기준 조회 (lot.wf 형식)
        show_wafer_map(groupkey="4SS4UR9.1, 4SSYY23.2, 4SS8JDL.3", map_type="binmap")

        # Wafer 기준 조회 (lot_id + wf_ids)
        show_wafer_map(lot_id="4SS4UR9", wf_ids="1,2,3", map_type="binmap")

        # binmap과 cummap 둘 다 생성
        show_wafer_map(lot_id="4SS4UR9", wf_ids="1,2,3", map_type="binmap,cummap")
    """
    # 1. 내부에서 DB 조회 (데이터가 LLM을 거치지 않음)
    map_data_list = _query_wafer_data(
        lot_id=lot_id, lot_ids=lot_ids, wf_ids=wf_ids, groupkey=groupkey
    )

    if not map_data_list:
        return "조회된 데이터가 없습니다. lot_id와 wf_id를 확인해주세요."

    n_wafers = len(map_data_list)
    lot_info = map_data_list[0]["lot_id"]

    # bin_type 검증
    if bin_type not in ["pt1h_bin", "pt2c_bin"]:
        bin_type = "pt1h_bin"

    # map_type 파싱 (쉼표 구분 문자열 지원)
    requested_types = set(t.strip().lower() for t in map_type.split(","))

    # "all" 처리: 모든 타입으로 확장
    if "all" in requested_types:
        requested_types = {"binmap", "cummap"}  # 나중에 deltamap 추가 시 여기에 추가

    # ====== 로그 출력 ======
    print("=" * 60)
    print(f"[LOG] Requested Map Types: {requested_types}")
    print(f"[LOG] Bin Type: {bin_type}")
    print(f"[LOG] 조회된 (lot_id, wf_id) 순서쌍:")
    for data in map_data_list:
        print(f"       - ({data['lot_id']}, {data['wf_id']})")
    print(f"[LOG] 총 {n_wafers}개 wafer")
    print("=" * 60)

    # 2. 시각화 수행 (요청된 모든 타입에 대해)
    results = []

    if "binmap" in requested_types:
        filepath = _visualize_binmap(map_data_list, bin_type=bin_type)
        results.append(f"Binmap: {filepath}")

    if "cummap" in requested_types:
        filepath, avg_pass_rate = _visualize_cummap(map_data_list, bin_type=bin_type)
        if filepath:
            results.append(f"Cummap: {filepath} (평균 Pass Rate: {avg_pass_rate:.1f}%)")
        else:
            results.append("Cummap 생성 실패")

    # 나중에 deltamap 추가 시:
    # if "deltamap" in requested_types:
    #     filepath = _visualize_deltamap(map_data_list, bin_type=bin_type)
    #     results.append(f"Deltamap: {filepath}")

    # 결과 메시지 생성
    if not results:
        return "유효한 map_type이 지정되지 않았습니다. (binmap, cummap, all 중 선택)"

    result_msg = "이미지가 생성되었습니다:\n"
    result_msg += "\n".join(f"  - {r}" for r in results)
    result_msg += (
        f"\n\n- Lot: {lot_info}\n- Wafer 수: {n_wafers}개\n- Bin Type: {bin_type}"
    )

    return result_msg


# ============================================================
# 6. 에이전트 생성
# ============================================================
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

# OpenRouter 모델 생성
model = ChatOpenAI(
    model="gpt-oss-120b",
    base_url=os.getenv("OPENROUTER_BASE_URL"),
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0,
    request_timeout=180,
)

# map_agent 생성 (단일 통합 도구 사용)
map_agent = create_agent(
    model,
    tools=[show_wafer_map],
    system_prompt=(
        "You are a wafer map specialist agent.\n\n"
        "YOUR ONLY TOOL: show_wafer_map\n"
        "This tool handles both DB query and visualization internally.\n"
        "You just need to parse parameters from user query and pass them.\n\n"
        "CRITICAL PARAMETER PARSING RULES:\n\n"
        "=== LOT 기준 조회 (해당 lot의 모든 wafer) ===\n"
        "1. lot_ids: Multiple lot IDs without wf_id (fetch ALL wafers of each lot)\n"
        "   - Pattern: Only lot IDs separated by comma, NO dots\n"
        "   - User: '4SSW0PC,4SSXCEW,4SSR2CD cummap 그려줘'\n"
        "   - Call: show_wafer_map(lot_ids='4SSW0PC,4SSXCEW,4SSR2CD', map_type='cummap')\n"
        "   - User: '4SS4UR9, 4SSYY23 binmap 보여줘'\n"
        "   - Call: show_wafer_map(lot_ids='4SS4UR9,4SSYY23', map_type='binmap')\n\n"
        "=== WAFER 기준 조회 (특정 wafer만) ===\n"
        "2. groupkey: When input contains 'LOT_ID.WF_ID' format (with dots)\n"
        "   - Pattern: lot_id.wf_id separated by comma\n"
        "   - User: '4SS4UR9.1, 4SSYY23.2, 4SS8JDL.3 binmap 보여줘'\n"
        "   - Call: show_wafer_map(groupkey='4SS4UR9.1, 4SSYY23.2, 4SS8JDL.3', map_type='binmap')\n"
        "   - NOTE: Convert .01, .02 to .1, .2 (remove leading zeros from wf_id)\n\n"
        "3. lot_id + wf_ids: Single lot with specific wafers\n"
        "   - User: '4SS4UR9의 wf_id 1,2,3 binmap 보여줘'\n"
        "   - Call: show_wafer_map(lot_id='4SS4UR9', wf_ids='1,2,3', map_type='binmap')\n\n"
        "=== DETECTION RULE ===\n"
        "- Contains dots (.) like 'XXXXX.N' -> USE groupkey (specific wafers)\n"
        "- Multiple lot IDs without dots -> USE lot_ids (all wafers of lots)\n"
        "- Single lot with 'wf_id' keyword -> USE lot_id + wf_ids\n\n"
        "4. map_type (supports comma-separated values):\n"
        "   - 'binmap': Individual wafer maps (default)\n"
        "   - 'cummap': Cumulative pass rate map\n"
        "   - 'binmap,cummap': Generate both maps\n"
        "   - 'all': Generate all available map types\n"
        "   - User: 'binmap과 cummap 둘 다 보여줘' -> map_type='binmap,cummap'\n"
        "   - User: 'binmap/cummap 그려줘' -> map_type='binmap,cummap'\n\n"
        "5. bin_type: 'pt1h_bin' (default) or 'pt2c_bin'\n\n"
        "IMPORTANT:\n"
        "- Available parameters: lot_id, lot_ids, wf_ids, groupkey, map_type, bin_type\n"
        "- Always respond in Korean"
    ),
    name="map_agent",
)


# Dummy 도구 및 에이전트
def dummy_tool() -> str:
    """Placeholder tool - returns message that this feature is not yet implemented"""
    return "이 기능은 아직 구현되지 않았습니다. 곧 추가될 예정입니다."


dummy_agent_2 = create_agent(
    model,
    tools=[dummy_tool],
    system_prompt="You are a placeholder agent. Always respond in Korean.",
    name="dummy_agent_2",
)

dummy_agent_3 = create_agent(
    model,
    tools=[dummy_tool],
    system_prompt="You are a placeholder agent. Always respond in Korean.",
    name="dummy_agent_3",
)


# ============================================================
# 7. Supervisor 생성
# ============================================================
from langgraph_supervisor import create_supervisor

map_agent.name = "map_agent"
dummy_agent_2.name = "dummy_agent_2"
dummy_agent_3.name = "dummy_agent_3"

supervisor = create_supervisor(
    model=model,
    agents=[map_agent, dummy_agent_2, dummy_agent_3],
    prompt=(
        "You are a supervisor managing three specialized agents:\n\n"
        "1. MAP_AGENT:\n"
        "   - Specializes in wafer map visualization\n"
        "   - Uses a single efficient tool (show_wafer_map) that handles DB query + visualization internally\n"
        "   - Can generate binmap (individual maps) and cummap (cumulative pass rate map)\n"
        "   - Use for: wafer, lot, map, binmap, cummap related queries\n\n"
        "2. DUMMY_AGENT_2:\n"
        "   - Placeholder agent (not yet implemented)\n\n"
        "3. DUMMY_AGENT_3:\n"
        "   - Placeholder agent (not yet implemented)\n\n"
        "ORCHESTRATION GUIDELINES:\n"
        "- Analyze the user's request carefully\n"
        "- Route wafer/lot/map/binmap/cummap related queries to map_agent\n"
        "- Route other queries to appropriate agent\n"
        "- Always respond in Korean"
    ),
    add_handoff_back_messages=True,
    output_mode="full_history",
).compile()


# ============================================================
# 8. 테스트 실행
# ============================================================
def run_test(test_name: str, user_message: str):
    """테스트 실행 헬퍼 함수"""
    print(f"\n{'='*80}")
    print(f"{test_name}")
    print(f"{'='*80}")
    print(f"User: {user_message}\n")

    for chunk in supervisor.stream(
        {"messages": [{"role": "user", "content": user_message}]}
    ):
        pretty_print_messages(chunk, last_message=True)


if __name__ == "__main__":
    print("Wafer Map Supervisor 시작")
    print("=" * 80)

    # 테스트 1: Binmap 단일
    run_test("테스트 1: Binmap 단일", "lot_id 4SS4UR9, wf_id 1의 binmap을 보여줘")

    # 테스트 2: Binmap 다중
    run_test(
        "테스트 2: Binmap 다중", "lot_id 4SS4UR9의 wf_id 1, 2, 3 binmap을 비교해줘"
    )

    # 테스트 3: Cummap
    run_test("테스트 3: Cummap", "lot_id 4SS4UR9의 wf_id 1~5 cummap을 그려줘")

    # 테스트 4: 다른 lot 비교
    run_test(
        "테스트 4: 다른 lot 비교", "4SS4UR9.1, 4SSYY23.2, 4SS8JDL.3 binmap을 비교해줘"
    )
