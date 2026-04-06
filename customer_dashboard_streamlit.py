import io
import calendar

import pandas as pd
import streamlit as st

st.set_page_config(page_title="고객 대시보드", layout="wide")

st.title("고객 분석 대시보드")
st.caption("엑셀 원본 파일을 업로드하면 선택한 월 기준으로 자동 분석됩니다.")

REQUIRED_COLUMNS = {
    "구매일자": "날짜 형식 또는 날짜로 변환 가능한 값",
    "고객ID": "고객을 구분할 수 있는 고유값",
    "구매채널": "온라인 또는 오프라인",
    "구매금액": "숫자 형식",
    "고객등급": "예: VIP, GOLD, SILVER, 일반"
}

MONTH_LABEL_FORMAT = "%b-%y"


def show_required_columns():
    st.subheader("필요한 컬럼명")
    required_df = pd.DataFrame(
        {
            "컬럼명": list(REQUIRED_COLUMNS.keys()),
            "설명": list(REQUIRED_COLUMNS.values())
        }
    )
    st.dataframe(required_df, use_container_width=True, hide_index=True)


@st.cache_data(show_spinner=False)
def load_excel(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    if file_name.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(file_bytes))
    return pd.read_excel(io.BytesIO(file_bytes))



def normalize_channel(value: str) -> str:
    text = str(value).strip()
    mapping = {
        "online": "온라인",
        "온라인": "온라인",
        "on": "온라인",
        "offline": "오프라인",
        "오프라인": "오프라인",
        "off": "오프라인"
    }
    return mapping.get(text.lower(), text)



def get_month_end(date_value: pd.Timestamp) -> pd.Timestamp:
    last_day = calendar.monthrange(date_value.year, date_value.month)[1]
    return pd.Timestamp(date_value.year, date_value.month, last_day)



def get_analysis_period(selected_month_end: pd.Timestamp) -> dict:
    active_end = selected_month_end.normalize()
    active_start = (active_end - pd.DateOffset(months=5)).replace(day=1)
    inactive_start = (active_start - pd.DateOffset(months=6)).replace(day=1)
    inactive_end = active_start - pd.DateOffset(days=1)

    return {
        "기준월말": active_end,
        "활성시작일": active_start,
        "활성종료일": active_end,
        "비활성시작일": inactive_start,
        "비활성종료일": inactive_end,
    }



def preprocess_data(df: pd.DataFrame) -> pd.DataFrame:
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"필수 컬럼이 없습니다: {', '.join(missing_cols)}")

    work_df = df.copy()
    work_df["구매일자"] = pd.to_datetime(work_df["구매일자"], errors="coerce")
    work_df["구매금액"] = pd.to_numeric(work_df["구매금액"], errors="coerce")
    work_df["구매채널"] = work_df["구매채널"].apply(normalize_channel)
    work_df["고객등급"] = work_df["고객등급"].astype(str).str.strip()
    work_df["고객ID"] = work_df["고객ID"].astype(str).str.strip()

    work_df = work_df.dropna(subset=["구매일자", "구매금액"])
    work_df = work_df[work_df["고객ID"] != ""]

    if work_df.empty:
        raise ValueError("유효한 데이터가 없습니다. 구매일자, 구매금액, 고객ID 값을 확인해주세요.")

    work_df["구매월"] = work_df["구매일자"].dt.to_period("M").dt.to_timestamp()
    return work_df



def build_customer_status(df: pd.DataFrame, period_info: dict) -> pd.DataFrame:
    active_start = period_info["활성시작일"]
    active_end = period_info["활성종료일"]
    inactive_start = period_info["비활성시작일"]
    inactive_end = period_info["비활성종료일"]

    customer_base = (
        df.groupby("고객ID", dropna=False)
        .agg(
            최근구매일자=("구매일자", "max"),
            고객등급=("고객등급", lambda x: x.dropna().iloc[-1] if not x.dropna().empty else "미분류")
        )
        .reset_index()
    )

    active_customers = set(
        df.loc[(df["구매일자"] >= active_start) & (df["구매일자"] <= active_end), "고객ID"].astype(str)
    )
    inactive_candidate_customers = set(
        df.loc[(df["구매일자"] >= inactive_start) & (df["구매일자"] <= inactive_end), "고객ID"].astype(str)
    )
    inactive_customers = inactive_candidate_customers - active_customers

    customer_base["고객상태"] = "휴면/기타"
    customer_base.loc[customer_base["고객ID"].isin(active_customers), "고객상태"] = "활성화 고객"
    customer_base.loc[customer_base["고객ID"].isin(inactive_customers), "고객상태"] = "비활성화 고객"

    return customer_base.sort_values(["고객상태", "최근구매일자"], ascending=[True, False]).reset_index(drop=True)



def build_channel_summary(df: pd.DataFrame, customer_status_df: pd.DataFrame, period_info: dict) -> pd.DataFrame:
    active_start = period_info["활성시작일"]
    active_end = period_info["활성종료일"]

    active_df = df[(df["구매일자"] >= active_start) & (df["구매일자"] <= active_end)].copy()
    active_customer_ids = set(customer_status_df.loc[customer_status_df["고객상태"] == "활성화 고객", "고객ID"])
    active_df = active_df[active_df["고객ID"].isin(active_customer_ids)]

    summary = (
        active_df.groupby("구매채널", dropna=False)
        .agg(
            구매고객수=("고객ID", "nunique"),
            구매금액합계=("구매금액", "sum")
        )
        .reset_index()
    )

    channel_order = ["온라인", "오프라인"]
    known = summary[summary["구매채널"].isin(channel_order)].copy()
    unknown = summary[~summary["구매채널"].isin(channel_order)].copy()
    known["정렬순서"] = known["구매채널"].map({"온라인": 0, "오프라인": 1})
    known = known.sort_values("정렬순서").drop(columns="정렬순서")
    summary = pd.concat([known, unknown], ignore_index=True)

    total_row = pd.DataFrame(
        [{
            "구매채널": "전체",
            "구매고객수": active_df["고객ID"].nunique(),
            "구매금액합계": active_df["구매금액"].sum()
        }]
    )
    return pd.concat([summary, total_row], ignore_index=True)



def build_active_grade_summary(customer_status_df: pd.DataFrame) -> pd.DataFrame:
    active_df = customer_status_df[customer_status_df["고객상태"] == "활성화 고객"].copy()
    return (
        active_df.groupby("고객등급", dropna=False)
        .agg(활성화고객수=("고객ID", "nunique"))
        .reset_index()
        .sort_values(["활성화고객수", "고객등급"], ascending=[False, True])
    )



def build_status_summary(customer_status_df: pd.DataFrame) -> pd.DataFrame:
    status_order = ["활성화 고객", "비활성화 고객", "휴면/기타"]
    summary = (
        customer_status_df.groupby("고객상태", dropna=False)
        .agg(고객수=("고객ID", "nunique"))
        .reset_index()
    )
    summary["정렬순서"] = summary["고객상태"].map({name: idx for idx, name in enumerate(status_order)})
    return summary.sort_values("정렬순서").drop(columns="정렬순서")



def build_month_options(df: pd.DataFrame) -> list[pd.Timestamp]:
    months = sorted(df["구매월"].dropna().unique())
    return [pd.Timestamp(month) for month in months]



def format_number(value):
    if pd.isna(value):
        return "-"
    return f"{value:,.0f}"



def format_month_label(date_value: pd.Timestamp) -> str:
    return date_value.strftime(MONTH_LABEL_FORMAT)


show_required_columns()

uploaded_file = st.file_uploader(
    "엑셀 파일 업로드",
    type=["xlsx", "xls", "csv"],
    help="파일을 다시 업로드하면 자동으로 모든 지표가 갱신됩니다."
)

if uploaded_file is None:
    st.info("분석할 파일을 업로드해주세요.")
    st.stop()

try:
    raw_df = load_excel(uploaded_file.getvalue(), uploaded_file.name)
    data_df = preprocess_data(raw_df)

    month_options = build_month_options(data_df)
    if not month_options:
        raise ValueError("분석 가능한 구매월 데이터가 없습니다.")

    month_labels = [format_month_label(month) for month in month_options]
    default_index = len(month_options) - 1

    st.subheader("기준월 선택")
    selected_label = st.selectbox(
        "분석 기준월을 선택하세요",
        options=month_labels,
        index=default_index,
        help="예: Mar-26 선택 시 활성화 고객은 2025-10-01~2026-03-31 구매회원, 비활성화 고객은 2025-04-01~2025-09-30 구매회원이며 활성화 회원은 제외됩니다."
    )

    selected_month = month_options[month_labels.index(selected_label)]
    selected_month_end = get_month_end(selected_month)
    period_info = get_analysis_period(selected_month_end)

    customer_status_df = build_customer_status(data_df, period_info)
    channel_summary = build_channel_summary(data_df, customer_status_df, period_info)
    active_grade_summary = build_active_grade_summary(customer_status_df)
    status_summary = build_status_summary(customer_status_df)

    active_count = int((customer_status_df["고객상태"] == "활성화 고객").sum())
    inactive_count = int((customer_status_df["고객상태"] == "비활성화 고객").sum())
    total_customers = int(customer_status_df["고객ID"].nunique())

    active_period_df = data_df[
        (data_df["구매일자"] >= period_info["활성시작일"]) & (data_df["구매일자"] <= period_info["활성종료일"])
    ].copy()
    total_sales = float(active_period_df["구매금액"].sum())

    st.subheader("기준 안내")
    st.write(
        f"- 선택 기준월: {selected_label}\n"
        f"- 활성화 고객 구매기간: {period_info['활성시작일'].strftime('%Y-%m-%d')} ~ {period_info['활성종료일'].strftime('%Y-%m-%d')}\n"
        f"- 비활성화 고객 구매기간: {period_info['비활성시작일'].strftime('%Y-%m-%d')} ~ {period_info['비활성종료일'].strftime('%Y-%m-%d')}\n"
        f"- 비활성화 고객은 활성화 고객을 제외한 고객만 집계"
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("전체 고객 수", format_number(total_customers))
    col2.metric("활성화 고객 수", format_number(active_count))
    col3.metric("비활성화 고객 수", format_number(inactive_count))
    col4.metric("활성기간 구매금액", format_number(total_sales))

    st.divider()

    left, right = st.columns(2)

    with left:
        st.subheader("온라인 / 오프라인 구매고객 수 및 구매금액")
        display_channel = channel_summary.copy()
        display_channel["구매고객수"] = display_channel["구매고객수"].map(format_number)
        display_channel["구매금액합계"] = display_channel["구매금액합계"].map(format_number)
        st.dataframe(display_channel, use_container_width=True, hide_index=True)

        chart_channel = channel_summary[channel_summary["구매채널"] != "전체"].copy()
        if not chart_channel.empty:
            st.bar_chart(chart_channel.set_index("구매채널")[["구매고객수", "구매금액합계"]])

    with right:
        st.subheader("등급별 활성화 고객 수")
        if active_grade_summary.empty:
            st.warning("활성화 고객 데이터가 없습니다.")
        else:
            display_grade = active_grade_summary.copy()
            display_grade["활성화고객수"] = display_grade["활성화고객수"].map(format_number)
            st.dataframe(display_grade, use_container_width=True, hide_index=True)
            st.bar_chart(active_grade_summary.set_index("고객등급")[["활성화고객수"]])

    st.divider()

    st.subheader("고객 상태별 집계")
    display_status = status_summary.copy()
    display_status["고객수"] = display_status["고객수"].map(format_number)
    st.dataframe(display_status, use_container_width=True, hide_index=True)

    st.subheader("고객별 최근 구매일 기준 상태")
    customer_display = customer_status_df.copy()
    customer_display["최근구매일자"] = customer_display["최근구매일자"].dt.strftime("%Y-%m-%d")
    st.dataframe(customer_display, use_container_width=True, hide_index=True)

except Exception as e:
    st.error(f"파일 처리 중 오류가 발생했습니다: {e}")
    st.stop()
