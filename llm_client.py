"""
LLM 통합 클라이언트 — Anthropic 또는 Gemini 키 중 있는 걸로 자동 선택
"""

import os
import json
import re


def call_llm(prompt: str, anthropic_key: str = "", gemini_key: str = "",
             max_tokens: int = 2048) -> str:
    """
    anthropic_key 또는 gemini_key 중 하나만 있으면 됨.
    둘 다 있으면 Anthropic 우선.
    """
    ant_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY", "")
    gem_key = gemini_key or os.getenv("GEMINI_API_KEY", "")

    if ant_key:
        return _call_anthropic(prompt, ant_key, max_tokens)
    elif gem_key:
        return _call_gemini(prompt, gem_key, max_tokens)
    else:
        raise ValueError("API 키 없음 — Anthropic 또는 Gemini 키를 입력해주세요.")


def _call_anthropic(prompt: str, api_key: str, max_tokens: int) -> str:
    import anthropic as ant
    client = ant.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def _call_gemini(prompt: str, api_key: str, max_tokens: int) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    return resp.text


def call_llm_with_images(prompt: str, image_urls: list,
                         anthropic_key: str = "", gemini_key: str = "",
                         max_tokens: int = 1024) -> str:
    """
    이미지 URL + 텍스트 프롬프트를 LLM에 전달.
    이벤트 배너 이미지에서 기간·조건·ETF 정보 추출용.
    Anthropic만 지원 (Gemini는 텍스트 폴백).
    """
    ant_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY", "")
    gem_key = gemini_key or os.getenv("GEMINI_API_KEY", "")

    valid_imgs = [u for u in (image_urls or []) if u and u.startswith("http")]

    if ant_key and valid_imgs:
        return _call_anthropic_with_images(prompt, valid_imgs, ant_key, max_tokens)
    # 이미지 없거나 Anthropic 키 없으면 텍스트 전용 폴백
    return call_llm(prompt, anthropic_key=ant_key, gemini_key=gem_key, max_tokens=max_tokens)


def _call_anthropic_with_images(prompt: str, image_urls: list,
                                 api_key: str, max_tokens: int) -> str:
    import anthropic as ant
    client = ant.Anthropic(api_key=api_key)
    content = []
    for url in image_urls[:4]:  # 최대 4개 이미지 (토큰 제한)
        content.append({
            "type": "image",
            "source": {"type": "url", "url": url},
        })
    content.append({"type": "text", "text": prompt})
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
    )
    return msg.content[0].text


def get_api_keys_from_session() -> tuple[str, str]:
    """Streamlit session_state에서 두 키 읽기."""
    try:
        import streamlit as st
        ant = st.session_state.get("anthropic_api_key", "") or os.getenv("ANTHROPIC_API_KEY", "")
        gem = st.session_state.get("gemini_api_key", "")    or os.getenv("GEMINI_API_KEY", "")
        return ant, gem
    except Exception:
        return os.getenv("ANTHROPIC_API_KEY", ""), os.getenv("GEMINI_API_KEY", "")
