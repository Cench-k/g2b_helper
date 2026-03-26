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
