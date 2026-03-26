import os
import streamlit as st
from supabase import create_client, Client


@st.cache_resource
def get_client() -> Client:
    try:
        url = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL", ""))
        key = st.secrets.get("SUPABASE_KEY", os.environ.get("SUPABASE_KEY", ""))
    except Exception:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
    return create_client(url, key)


def save_bid_record(record: dict) -> bool:
    """입찰 기록 저장"""
    try:
        get_client().table("bid_records").insert(record).execute()
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False


def load_bid_records(user_id: str) -> list:
    """사용자 입찰 기록 전체 조회"""
    try:
        res = (
            get_client()
            .table("bid_records")
            .select("*")
            .eq("user_id", user_id)
            .order("open_date", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        st.error(f"조회 실패: {e}")
        return []


def delete_bid_record(record_id: int) -> bool:
    """입찰 기록 삭제"""
    try:
        get_client().table("bid_records").delete().eq("id", record_id).execute()
        return True
    except Exception as e:
        st.error(f"삭제 실패: {e}")
        return False


# ── 공고 캐시 (API 트래픽 절감) ───────────────────────────────────────────
def cache_get_bid(bid_no: str) -> dict | None:
    """캐시된 공고 정보 조회 (없으면 None)"""
    try:
        res = (
            get_client()
            .table("bid_cache")
            .select("data")
            .eq("bid_no", bid_no)
            .execute()
        )
        if res.data:
            return res.data[0]["data"]
    except Exception:
        pass
    return None


def cache_save_bid(bid_no: str, data: dict) -> None:
    """공고 정보를 캐시에 저장 (upsert)"""
    try:
        get_client().table("bid_cache").upsert(
            {"bid_no": bid_no, "data": data},
            on_conflict="bid_no",
        ).execute()
    except Exception:
        pass
