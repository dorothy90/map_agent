# ============================================================
# 1. 환경 설정
# ============================================================
from dotenv import load_dotenv

load_dotenv(override=True)

import os
import json
from typing import List, Optional, Annotated, TypedDict, Literal
from langchain_core.messages import convert_to_messages, HumanMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.types import Command
from langgraph.prebuilt import InjectedState


# ============================================================
# 1.1 커스텀 State 정의 (Agent들이 공유하는 데이터)
# ============================================================
class WaferMapState(TypedDict):
    """Wafer Map Supervisor의 공유 State
    
    모든 agent들이 이 State를 통해 구조화된 데이터를 공유합니다.
    """
    messages: Annotated[list, add_messages]  # 대화 히스토리
    
    # 조회 파라미터
    lot_id: str                    # 단일 lot ID
    lot_ids: str                   # 여러 lot ID (쉼표 구분)
    wf_ids: str                    # wafer ID들 (쉼표 구분)
    groupkey: str                  # lot_id.wf_id 형식 (쉼표 구분)
    map_type: str                  # 시각화 유형 (binmap, cummap, all)
    bin_type: str                  # bin 종류 (pt1h_bin, pt2c_bin)
    
    # 결과 데이터
    wafer_data: list               # DB에서 가져온 wafer 데이터
    image_paths: list              # 생성된 이미지 경로들
    
    # 라우팅
    next_agent: str                # 다음 에이전트 (supervisor, map_agent, END)

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
    groupkey: Optional[str] = None
) -> list:
    """Oracle DB에서 wafer 데이터 조회 (내부 함수)

    조회 방식:
    1. lot_ids: 여러 lot의 모든 wafer 조회 (lot 기준)
    2. groupkey: lot_id.wf_id 형식으로 특정 wafer 조회 (wafer 기준)
    3. lot_id + wf_ids: 단일 lot의 특정 wafer 조회 (wafer 기준)
    """
    conn = oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=ORACLE_DSN
    )

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
        raw_data = json.loads(map_val_json)
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
    """모든 map 데이터에서 row, col의 최소/최대값 계산"""
    all_rows = []
    all_cols = []

    for data in map_data_list:
        map_json = _parse_map_json(data["map_val_json"])
        for key in map_json.keys():
            row, col = map(int, key.split("_"))
            all_rows.append(row)
            all_cols.append(col)

    return min(all_rows), max(all_rows), min(all_cols), max(all_cols)


def _visualize_binmap(map_data_list: list, bin_type: str = "pt1h_bin") -> str:
    """여러 wafer의 binmap을 개별적으로 시각화 (내부 함수)

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

    # 모든 bin 값 수집
    all_values = set()
    for data in map_data_list:
        map_json = _parse_map_json(data["map_val_json"])
        for value in map_json.values():
            bin_val = value.get(bin_type, "")
            if bin_val:
                all_values.add(bin_val)
    value_to_num = {v: i for i, v in enumerate(sorted(all_values))}

    fig, axes = plt.subplots(n_rows, n_cols, figsize=(4*n_cols, 4*n_rows))
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

    for idx, data in enumerate(map_data_list):
        row_idx = idx // n_cols
        col_idx = idx % n_cols
        ax = axes[row_idx, col_idx]

        map_array = np.full((height, width), np.nan)
        map_json = _parse_map_json(data["map_val_json"])

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
    plt.close()  # plt.show() 대신 plt.close() 사용 (터미널 블로킹 방지)

    return filepath


def _visualize_cummap(map_data_list: list, bin_type: str = "pt1h_bin") -> tuple:
    """여러 wafer를 Pass Rate 기반 cummap으로 시각화 (내부 함수)

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

    pass_sum = np.zeros((height, width))
    count = np.zeros((height, width))

    for data in map_data_list:
        map_json = _parse_map_json(data["map_val_json"])

        for key, value in map_json.items():
            r, c = map(int, key.split("_"))
            r_idx = r - min_row
            c_idx = c - min_col

            bin_val = value.get(bin_type, "")
            pass_sum[r_idx, c_idx] += 1 if bin_val == "A" else 0
            count[r_idx, c_idx] += 1

    with np.errstate(divide='ignore', invalid='ignore'):
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
    plt.close()  # plt.show() 대신 plt.close() 사용 (터미널 블로킹 방지)

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
    bin_type: str = "pt1h_bin"
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
        lot_id=lot_id,
        lot_ids=lot_ids,
        wf_ids=wf_ids,
        groupkey=groupkey
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
    result_msg += f"\n\n- Lot: {lot_info}\n- Wafer 수: {n_wafers}개\n- Bin Type: {bin_type}"

    return result_msg


# ============================================================
# 6. LLM 모델 설정
# ============================================================
# OpenRouter 모델 생성
model = ChatOpenAI(
    model="gpt-oss-120b",
    base_url=os.getenv("OPENROUTER_BASE_URL"),
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0,
    request_timeout=180,
)


# ============================================================
# 7. Supervisor 노드 구현
# ============================================================
SUPERVISOR_SYSTEM_PROMPT = """You are a supervisor managing wafer map visualization tasks.

Your job is to:
1. Analyze the user's request
2. Extract parameters for wafer map visualization
3. Route to the appropriate agent

AVAILABLE AGENTS:
- map_agent: Handles wafer map visualization (binmap, cummap)

PARAMETER EXTRACTION RULES:

=== LOT 기준 조회 (해당 lot의 모든 wafer) ===
1. lot_ids: Multiple lot IDs without wf_id (fetch ALL wafers of each lot)
   - Pattern: Only lot IDs separated by comma, NO dots
   - Example: '4SSW0PC,4SSXCEW,4SSR2CD cummap 그려줘'
   - Extract: lot_ids='4SSW0PC,4SSXCEW,4SSR2CD', map_type='cummap'

=== WAFER 기준 조회 (특정 wafer만) ===
2. groupkey: When input contains 'LOT_ID.WF_ID' format (with dots)
   - Pattern: lot_id.wf_id separated by comma
   - Example: '4SS4UR9.1, 4SSYY23.2 binmap 보여줘'
   - Extract: groupkey='4SS4UR9.1,4SSYY23.2', map_type='binmap'

3. lot_id + wf_ids: Single lot with specific wafers
   - Example: '4SS4UR9의 wf_id 1,2,3 binmap 보여줘'
   - Extract: lot_id='4SS4UR9', wf_ids='1,2,3', map_type='binmap'

=== MAP TYPE ===
- 'binmap': Individual wafer maps (default)
- 'cummap': Cumulative pass rate map
- 'binmap,cummap' or 'all': Both maps

=== BIN TYPE ===
- 'pt1h_bin' (default) or 'pt2c_bin'

Respond in JSON format:
{
    "next_agent": "map_agent" or "END",
    "lot_id": "...",
    "lot_ids": "...",
    "wf_ids": "...",
    "groupkey": "...",
    "map_type": "binmap",
    "bin_type": "pt1h_bin",
    "message": "한국어로 사용자에게 전달할 메시지"
}

If the request is not about wafer maps, set next_agent to "END" and provide a helpful message.
Always respond in Korean for the message field.
"""


def supervisor_node(state: WaferMapState) -> Command:
    """Supervisor 노드: 사용자 요청을 분석하고 적절한 agent로 라우팅
    
    LLM이 사용자 요청에서 파라미터를 추출하고 다음 agent를 결정합니다.
    """
    messages = state.get("messages", [])
    
    # LLM 호출
    response = model.invoke([
        {"role": "system", "content": SUPERVISOR_SYSTEM_PROMPT},
        *messages
    ])
    
    # JSON 파싱
    try:
        # JSON 부분만 추출
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        parsed = json.loads(content.strip())
    except (json.JSONDecodeError, IndexError):
        # 파싱 실패 시 기본값
        parsed = {
            "next_agent": "END",
            "message": "요청을 이해하지 못했습니다. wafer map 관련 질문을 해주세요."
        }
    
    next_agent = parsed.get("next_agent", "END")
    
    # 결과 메시지 생성
    result_message = AIMessage(
        content=parsed.get("message", "처리 중입니다..."),
        name="supervisor"
    )
    
    # State 업데이트 데이터
    update_data = {
        "messages": [result_message],
        "lot_id": parsed.get("lot_id", ""),
        "lot_ids": parsed.get("lot_ids", ""),
        "wf_ids": parsed.get("wf_ids", ""),
        "groupkey": parsed.get("groupkey", ""),
        "map_type": parsed.get("map_type", "binmap"),
        "bin_type": parsed.get("bin_type", "pt1h_bin"),
        "next_agent": next_agent,
    }
    
    # 다음 노드로 라우팅
    if next_agent == "END":
        return Command(goto=END, update=update_data)
    else:
        return Command(goto=next_agent, update=update_data)


# ============================================================
# 8. Map Agent 노드 구현
# ============================================================
def map_agent_node(state: WaferMapState) -> Command:
    """Map Agent 노드: State에서 파라미터를 읽어 DB 조회 + 시각화 수행
    
    Supervisor가 추출한 파라미터를 사용하여 작업을 수행하고 결과를 State에 저장합니다.
    """
    # State에서 파라미터 읽기
    lot_id = state.get("lot_id", "")
    lot_ids = state.get("lot_ids", "")
    wf_ids = state.get("wf_ids", "")
    groupkey = state.get("groupkey", "")
    map_type = state.get("map_type", "binmap")
    bin_type = state.get("bin_type", "pt1h_bin")
    
    # 파라미터 로깅
    print("=" * 60)
    print("[Map Agent] State에서 파라미터 읽기:")
    print(f"  - lot_id: {lot_id}")
    print(f"  - lot_ids: {lot_ids}")
    print(f"  - wf_ids: {wf_ids}")
    print(f"  - groupkey: {groupkey}")
    print(f"  - map_type: {map_type}")
    print(f"  - bin_type: {bin_type}")
    print("=" * 60)
    
    # 파라미터가 없으면 에러 메시지 반환 후 END로 이동
    if not any([lot_id, lot_ids, groupkey]):
        error_message = AIMessage(
            content="조회할 lot_id 또는 wafer 정보가 없습니다. 다시 요청해주세요.",
            name="map_agent"
        )
        return Command(
            goto=END,
            update={"messages": [error_message], "image_paths": []}
        )
    
    # DB 조회 (내부 함수 사용)
    wafer_data = _query_wafer_data(
        lot_id=lot_id if lot_id else None,
        lot_ids=lot_ids if lot_ids else None,
        wf_ids=wf_ids if wf_ids else None,
        groupkey=groupkey if groupkey else None
    )
    
    if not wafer_data:
        error_message = AIMessage(
            content="조회된 데이터가 없습니다. lot_id와 wf_id를 확인해주세요.",
            name="map_agent"
        )
        return Command(
            goto=END,
            update={"messages": [error_message], "wafer_data": [], "image_paths": []}
        )
    
    # 시각화 수행
    n_wafers = len(wafer_data)
    
    # map_type 파싱
    requested_types = set(t.strip().lower() for t in map_type.split(","))
    if "all" in requested_types:
        requested_types = {"binmap", "cummap"}
    
    # 시각화 결과 수집
    image_paths = []
    results = []
    
    if "binmap" in requested_types:
        filepath = _visualize_binmap(wafer_data, bin_type=bin_type)
        image_paths.append(filepath)
        results.append(f"Binmap: {filepath}")
    
    if "cummap" in requested_types:
        filepath, avg_pass_rate = _visualize_cummap(wafer_data, bin_type=bin_type)
        if filepath:
            image_paths.append(filepath)
            results.append(f"Cummap: {filepath} (평균 Pass Rate: {avg_pass_rate:.1f}%)")
    
    # 결과 메시지 생성
    result_msg = f"이미지가 생성되었습니다:\n"
    result_msg += "\n".join(f"  - {r}" for r in results)
    result_msg += f"\n\n- Wafer 수: {n_wafers}개\n- Bin Type: {bin_type}"
    
    result_message = AIMessage(content=result_msg, name="map_agent")
    
    # 작업 완료 후 END로 직접 이동 (supervisor 재호출 방지)
    return Command(
        goto=END,
        update={
            "messages": [result_message],
            "wafer_data": wafer_data,
            "image_paths": image_paths,
        }
    )


# ============================================================
# 9. 최종 응답 노드
# ============================================================
def final_response_node(state: WaferMapState) -> Command:
    """최종 응답 생성 노드: 작업 결과를 요약하여 사용자에게 전달"""
    image_paths = state.get("image_paths", [])
    messages = state.get("messages", [])
    
    if image_paths:
        # 이미지가 생성된 경우
        final_msg = "작업이 완료되었습니다.\n\n"
        final_msg += "생성된 이미지:\n"
        for path in image_paths:
            final_msg += f"  - {path}\n"
    else:
        # 마지막 메시지 사용
        if messages:
            last_msg = messages[-1]
            final_msg = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
        else:
            final_msg = "처리가 완료되었습니다."
    
    final_message = AIMessage(content=final_msg, name="supervisor")
    
    return Command(
        goto=END,
        update={"messages": [final_message]}
    )


# ============================================================
# 10. StateGraph 구성
# ============================================================
def should_continue(state: WaferMapState) -> Literal["map_agent", "final", "__end__"]:
    """조건부 엣지: 다음 노드 결정"""
    next_agent = state.get("next_agent", "END")
    
    if next_agent == "map_agent":
        return "map_agent"
    elif next_agent == "END":
        return "__end__"
    else:
        return "final"


# 그래프 빌드
workflow = StateGraph(WaferMapState)

# 노드 추가
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("map_agent", map_agent_node)
workflow.add_node("final", final_response_node)

# 엣지 정의
workflow.add_edge(START, "supervisor")

# supervisor의 조건부 엣지는 Command가 처리하므로 별도 정의 불필요
# map_agent에서 supervisor로 돌아오는 것도 Command가 처리

# 컴파일
supervisor = workflow.compile()


# ============================================================
# 11. 테스트 실행
# ============================================================
def run_test(test_name: str, user_message: str):
    """테스트 실행 헬퍼 함수
    
    새로운 StateGraph 구조에서는 State를 통해 데이터가 공유됩니다.
    """
    print(f"\n{'='*80}")
    print(f"{test_name}")
    print(f"{'='*80}")
    print(f"User: {user_message}\n")

    # 초기 State 설정
    initial_state = {
        "messages": [HumanMessage(content=user_message)],
        "lot_id": "",
        "lot_ids": "",
        "wf_ids": "",
        "groupkey": "",
        "map_type": "binmap",
        "bin_type": "pt1h_bin",
        "wafer_data": [],
        "image_paths": [],
        "next_agent": "",
    }
    
    # invoke로 실행하고 최종 State 받기
    final_state = supervisor.invoke(initial_state)
    
    # 메시지 출력
    if final_state.get("messages"):
        for msg in final_state["messages"]:
            if hasattr(msg, 'name') and msg.name:
                print(f"[{msg.name}]: {msg.content}\n")
            elif hasattr(msg, 'content'):
                print(f"{msg.content}\n")
    
    # 최종 State 요약 출력
    print("[최종 State 요약]")
    if final_state.get("image_paths"):
        print(f"  - 생성된 이미지: {final_state['image_paths']}")
    if final_state.get("wafer_data"):
        print(f"  - 조회된 wafer 수: {len(final_state['wafer_data'])}개")
    print(f"  - lot_id: {final_state.get('lot_id', '')}")
    print(f"  - lot_ids: {final_state.get('lot_ids', '')}")
    print(f"  - wf_ids: {final_state.get('wf_ids', '')}")
    print(f"  - map_type: {final_state.get('map_type', '')}")


if __name__ == "__main__":
    print("Wafer Map Supervisor 시작 (StateGraph 기반)")
    print("=" * 80)
    print("이제 Agent들이 WaferMapState를 통해 구조화된 데이터를 공유합니다.")
    print("=" * 80)

    # 테스트 1: Binmap 단일
    run_test(
        "테스트 1: Binmap 단일",
        "lot_id 4SS4UR9, wf_id 1의 binmap을 보여줘"
    )

    # 테스트 2: Binmap + Cummap 동시
    run_test(
        "테스트 2: Binmap + Cummap 동시",
        "lot_id 4SS4UR9의 wf_id 1, 2, 3 binmap과 cummap을 그려줘"
    )

    # 테스트 3: 다른 lot 비교 (groupkey 사용)
    run_test(
        "테스트 3: 다른 lot 비교 (groupkey)",
        "4SS4UR9.1, 4SSYY23.2, 4SS8JDL.3 binmap을 비교해줘"
    )
